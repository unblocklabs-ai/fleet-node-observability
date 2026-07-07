#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import http.client
import importlib.util
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROXY_PATH = ROOT / "src" / "fleet_node_observability" / "commands" / "node_exporter_proxy.py"

spec = importlib.util.spec_from_file_location("fleet_node_exporter_proxy", PROXY_PATH)
assert spec and spec.loader
proxy = importlib.util.module_from_spec(spec)
sys.modules["fleet_node_exporter_proxy"] = proxy
spec.loader.exec_module(proxy)


class UpstreamMetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            body = b"node_cpu_seconds_total 1\n"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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


class ProxyTokenAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        proxy.TOKEN_FILE = Path("/tmp/unused")
        proxy.UPSTREAM_HOST = "127.0.0.1"
        proxy.UPSTREAM_PORT = 9100
        proxy.PROXY_HOST = "127.0.0.1"
        proxy.PROXY_PORT = 19100
        self.token = "node-exporter-token"

    def _start_upstream(self, upstream_port: int):
        upstream = ThreadingHTTPServer((proxy.UPSTREAM_HOST, upstream_port), UpstreamMetricsHandler)
        return upstream

    def test_missing_token_file_returns_503(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            proxy.TOKEN_FILE = Path(temp_dir) / "no-token"
            with self.assertRaises(RuntimeError):
                proxy.read_token()

    def test_read_token_supports_env_style_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token.env"
            token_file.write_text("NODE_TOKEN=node-token")
            proxy.TOKEN_FILE = token_file
            self.assertEqual(proxy.read_token(), "node-token")

    def test_request_without_token_is_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token.txt"
            token_file.write_text(self.token)
            proxy.TOKEN_FILE = token_file

            upstream_port = free_port()
            proxy.UPSTREAM_PORT = upstream_port
            upstream = self._start_upstream(upstream_port)

            proxy_port = free_port()
            proxy.PROXY_PORT = proxy_port
            proxy_server = proxy.ThreadingHTTPServer((proxy.PROXY_HOST, proxy_port), proxy.MetricsProxy)

            with running_server(upstream), running_server(proxy_server):
                req = urllib.request.Request(f"http://{proxy.PROXY_HOST}:{proxy_port}/metrics")
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    urllib.request.urlopen(req, timeout=2)
            exc.exception.close()
            self.assertEqual(exc.exception.code, 403)

    def test_request_with_wrong_token_is_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token.txt"
            token_file.write_text(self.token)
            proxy.TOKEN_FILE = token_file

            upstream_port = free_port()
            proxy.UPSTREAM_PORT = upstream_port
            upstream = self._start_upstream(upstream_port)

            proxy_port = free_port()
            proxy.PROXY_PORT = proxy_port
            proxy_server = proxy.ThreadingHTTPServer((proxy.PROXY_HOST, proxy_port), proxy.MetricsProxy)

            with running_server(upstream), running_server(proxy_server):
                req = urllib.request.Request(f"http://{proxy.PROXY_HOST}:{proxy_port}/metrics")
                req.add_header(proxy.HEADER_NAME, "wrong")
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    urllib.request.urlopen(req, timeout=2)
            exc.exception.close()
            self.assertEqual(exc.exception.code, 403)

    def test_request_with_duplicate_token_headers_is_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token.txt"
            token_file.write_text(self.token)
            proxy.TOKEN_FILE = token_file

            upstream_port = free_port()
            proxy.UPSTREAM_PORT = upstream_port
            upstream = self._start_upstream(upstream_port)

            proxy_port = free_port()
            proxy.PROXY_PORT = proxy_port
            proxy_server = proxy.ThreadingHTTPServer((proxy.PROXY_HOST, proxy_port), proxy.MetricsProxy)

            with running_server(upstream), running_server(proxy_server):
                conn = http.client.HTTPConnection(proxy.PROXY_HOST, proxy_port, timeout=2)
                conn.putrequest("GET", "/metrics")
                conn.putheader(proxy.HEADER_NAME, self.token)
                conn.putheader(proxy.HEADER_NAME, self.token)
                conn.endheaders()
                response = conn.getresponse()
                response.read()
                conn.close()

            self.assertEqual(response.status, 403)

    def test_request_to_invalid_path_is_404(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token.txt"
            token_file.write_text(self.token)
            proxy.TOKEN_FILE = token_file

            upstream = self._start_upstream(free_port())
            proxy.UPSTREAM_PORT = upstream.server_address[1]
            proxy_port = free_port()
            proxy.PROXY_PORT = proxy_port
            proxy_server = proxy.ThreadingHTTPServer((proxy.PROXY_HOST, proxy_port), proxy.MetricsProxy)

            with running_server(upstream), running_server(proxy_server):
                req = urllib.request.Request(f"http://{proxy.PROXY_HOST}:{proxy_port}/health")
                req.add_header(proxy.HEADER_NAME, self.token)
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    urllib.request.urlopen(req, timeout=2)
            exc.exception.close()
            self.assertEqual(exc.exception.code, 404)

    def test_request_with_token_proxies_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token_file = Path(temp_dir) / "token.txt"
            token_file.write_text(self.token)
            proxy.TOKEN_FILE = token_file

            upstream_port = free_port()
            proxy.UPSTREAM_PORT = upstream_port
            upstream = self._start_upstream(upstream_port)

            proxy_port = free_port()
            proxy.PROXY_PORT = proxy_port
            proxy_server = proxy.ThreadingHTTPServer((proxy.PROXY_HOST, proxy_port), proxy.MetricsProxy)

            with running_server(upstream), running_server(proxy_server):
                req = urllib.request.Request(f"http://{proxy.PROXY_HOST}:{proxy_port}/metrics")
                req.add_header(proxy.HEADER_NAME, self.token)
                response = urllib.request.urlopen(req, timeout=2)
                body = response.read().decode("utf-8")
                content_type = response.getheader("Content-Type")
            self.assertEqual(response.status, 200)
            self.assertIn("node_cpu_seconds_total 1", body)
            self.assertIn("text/plain; version=0.0.4", content_type or "")


if __name__ == "__main__":
    unittest.main()
