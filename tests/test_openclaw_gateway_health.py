from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "fleet_node_observability" / "collectors" / "openclaw_gateway_health.sh"


class ReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/readyz":
            self.send_response(204)
            self.end_headers()
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return None


@contextlib.contextmanager
def running_server(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


class OpenClawGatewayHealthTests(unittest.TestCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["OPENCLAW_GATEWAY_HEALTH_TIMEOUT_SECS"] = "1"
        return subprocess.run(
            ["sh", str(SCRIPT), *args],
            capture_output=True,
            env=env,
            text=True,
            timeout=5,
        )

    def test_help_exits_successfully(self) -> None:
        result = self.run_script("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage: openclaw-gateway-health", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_invalid_mode_fails_before_readiness_check(self) -> None:
        result = self.run_script("nonsense", "http://127.0.0.1:9/readyz")

        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid mode: nonsense", result.stderr)
        self.assertIn("Usage: openclaw-gateway-health", result.stderr)

    def test_status_mode_returns_failure_when_unready(self) -> None:
        result = self.run_script("status", "http://127.0.0.1:9/readyz", "mini_03")

        self.assertEqual(result.returncode, 1)
        self.assertIn('"event_type":"gateway_unready"', result.stdout)
        self.assertIn('"gateway_ready":false', result.stdout)

    def test_ready_heartbeat_suppresses_unready_noise(self) -> None:
        result = self.run_script("ready-heartbeat", "http://127.0.0.1:9/readyz", "mini_03")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_prometheus_mode_writes_zero_when_unready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "gateway.prom"
            result = self.run_script(
                "prometheus",
                "http://127.0.0.1:9/readyz",
                "mini_03",
                str(output),
            )

            self.assertEqual(result.returncode, 0)
            content = output.read_text()
            self.assertIn('openclaw_gateway_ready{node="mini_03",gateway_ready_url="http://127.0.0.1:9/readyz"} 0', content)
            self.assertNotIn("service=", content)
            self.assertNotIn("openclaw_gateway_last_ready_check_timestamp_seconds", content)

    def test_status_mode_reports_ready_when_ready_endpoint_responds(self) -> None:
        port = free_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), ReadyHandler)

        with running_server(server):
            result = self.run_script("status", f"http://127.0.0.1:{port}/readyz", "mini_03")

        self.assertEqual(result.returncode, 0)
        self.assertIn('"event_type":"gateway_ready"', result.stdout)
        self.assertIn('"gateway_ready":true', result.stdout)


if __name__ == "__main__":
    unittest.main()
