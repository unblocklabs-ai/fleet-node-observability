from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

from fleet_node_observability.commands.configure_openclaw_otel import main as configure_main
from fleet_node_observability.commands.print_otlp_env import main as print_main


class OpenClawOtlpCommandTest(unittest.TestCase):
    def _run_print(self, args: list[str]) -> tuple[int, str, str]:
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = print_main(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _run_configure(self, args: list[str]) -> tuple[int, str, str]:
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = configure_main(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _parse_otlp_headers(self, output: str) -> dict[str, str]:
        line = next(line for line in output.splitlines() if "OTEL_EXPORTER_OTLP_HEADERS=" in line)
        encoded = line.split('OTEL_EXPORTER_OTLP_HEADERS="', 1)[1].split('"', 1)[0]
        return {
            key: unquote(value) for key, value in (item.split("=", 1) for item in encoded.split(","))
        }

    def test_print_command_uses_explicit_node_config_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Path(tmpdir) / "node-config.json"
            config.write_text(
                '{"node_label":"mini-03","network":"lan","otlp_http_endpoint":"http://192.168.10.11:4318","openclaw_service_name":"openclaw_gateway"}',
                encoding="utf-8",
            )
            rc, out, err = self._run_print(["--config", str(config), "--token", "token-1"])
            self.assertEqual(rc, 0)
            self.assertEqual(err, "")
            self.assertIn("OTEL_EXPORTER_OTLP_ENDPOINT=http://192.168.10.11:4318", out)
            self.assertIn("OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf", out)
            self.assertNotIn("display_name", out)
            self.assertNotIn("node_label", out)
            self.assertNotIn("host.name", out)

            headers = self._parse_otlp_headers(out)
            auth = base64.b64decode(headers["Authorization"].removeprefix("Basic ")).decode("utf-8")
            self.assertEqual(auth, "mini_03:token-1")

    def test_print_command_enforces_off_lan_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Path(tmpdir) / "node-config.json"
            config.write_text(
                '{"node_label":"mini-03","network":"off_lan","otlp_http_endpoint":"https://loki-ingest.example.com",'
                '"openclaw_service_name":"openclaw_gateway"}',
                encoding="utf-8",
            )
            rc, out, err = self._run_print(["--config", str(config), "--token", "token-1"])
            self.assertEqual(rc, 1)
            self.assertEqual(out, "")
            self.assertIn("off-LAN", err)

            rc, out, _ = self._run_print(
                [
                    "--config",
                    str(config),
                    "--token",
                    "token-1",
                    "--cf-access-client-id",
                    "client-id.access",
                    "--cf-access-client-secret",
                    "client-secret",
                ]
            )
            self.assertEqual(rc, 0)
            headers = self._parse_otlp_headers(out)
            self.assertEqual(headers["CF-Access-Client-Id"], "client-id.access")
            self.assertEqual(headers["CF-Access-Client-Secret"], "client-secret")

    def test_configure_command_updates_openclaw_json_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node_config = Path(tmpdir) / "node-config.json"
            node_config.write_text(
                '{"node_label":"mini-03","network":"lan","otlp_http_endpoint":"http://192.168.10.11:4318","openclaw_service_name":"openclaw_gateway"}',
                encoding="utf-8",
            )
            openclaw_config = Path(tmpdir) / ".openclaw" / "openclaw.json"
            openclaw_config.parent.mkdir(parents=True, exist_ok=True)
            openclaw_config.write_text('{"diagnostics":{"otel":{"metrics":true}}}\n', encoding="utf-8")

            rc, out, err = self._run_configure([
                "--config",
                str(node_config),
                "--openclaw-config",
                str(openclaw_config),
                "--token",
                "token-1",
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(err, "")
            payload = json.loads(openclaw_config.read_text(encoding="utf-8"))
            diagnostics = payload["diagnostics"]
            otel = diagnostics["otel"]
            self.assertTrue(diagnostics["enabled"])
            self.assertTrue(otel["enabled"])
            self.assertTrue(otel["logs"])
            self.assertTrue(otel["captureContent"])
            self.assertEqual(otel["protocol"], "http/protobuf")
            self.assertEqual(otel["endpoint"], "http://192.168.10.11:4318")
            self.assertEqual(otel["serviceName"], "openclaw_gateway")
            self.assertIn("Authorization", otel["headers"])
            decoded = base64.b64decode(otel["headers"]["Authorization"].removeprefix("Basic ")).decode("utf-8")
            self.assertEqual(decoded, "mini_03:token-1")
            self.assertIn("Backup:", out)

            backup = next(openclaw_config.parent.glob("openclaw.json.bak-fleet-otel-*"), None)
            self.assertIsNotNone(backup)

    def test_configure_command_skips_backup_with_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            node_config = Path(tmpdir) / "node-config.json"
            node_config.write_text(
                '{"node_label":"mini-03","network":"lan","otlp_http_endpoint":"http://192.168.10.11:4318","openclaw_service_name":"openclaw_gateway"}',
                encoding="utf-8",
            )
            openclaw_config = Path(tmpdir) / "openclaw.json"
            openclaw_config.write_text("{}", encoding="utf-8")

            rc, out, err = self._run_configure(
                [
                    "--config",
                    str(node_config),
                    "--openclaw-config",
                    str(openclaw_config),
                    "--no-backup",
                    "--token",
                    "token-1",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertEqual(err, "")
            self.assertNotIn("Backup:", out)
            backup_glob = list(openclaw_config.parent.glob("openclaw.json.bak-fleet-otel-*"))
            self.assertEqual(backup_glob, [])
