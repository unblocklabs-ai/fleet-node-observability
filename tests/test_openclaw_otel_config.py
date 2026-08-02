from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from fleet_node_observability.config import ConfigError
from fleet_node_observability.openclaw import (
    apply_loopback_diagnostics,
    load_openclaw_config,
    write_openclaw_config,
)


class OpenClawConfigTest(unittest.TestCase):
    def test_loopback_settings_replace_headers_and_disable_content_capture(self) -> None:
        payload = {
            "unrelated": True,
            "diagnostics": {
                "otel": {
                    "traces": False,
                    "metrics": False,
                    "logs": False,
                    "logsExporter": "console",
                    "tracesEndpoint": "https://stale.example/traces",
                    "metricsEndpoint": "https://stale.example/metrics",
                    "logsEndpoint": "https://stale.example/logs",
                    "headers": {"Authorization": "Basic secret", "Other": "stale"},
                }
            },
        }
        updated = apply_loopback_diagnostics(payload, endpoint="http://127.0.0.1:4318")
        self.assertTrue(updated["unrelated"])
        otel = updated["diagnostics"]["otel"]
        self.assertEqual(otel["endpoint"], "http://127.0.0.1:4318")
        self.assertEqual(otel["headers"], {})
        self.assertFalse(otel["captureContent"])
        self.assertTrue(otel["logs"])
        self.assertTrue(otel["metrics"])
        self.assertTrue(otel["traces"])
        self.assertEqual(otel["logsExporter"], "otlp")
        for old_endpoint in ("tracesEndpoint", "metricsEndpoint", "logsEndpoint"):
            self.assertNotIn(old_endpoint, otel)

    def test_rejects_non_object_diagnostics(self) -> None:
        for payload in ({"diagnostics": []}, {"diagnostics": {"otel": []}}):
            with self.subTest(payload=payload), self.assertRaises(ConfigError):
                apply_loopback_diagnostics(payload, endpoint="http://127.0.0.1:4318")

    def test_atomic_write_creates_private_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "openclaw.json"
            path.write_text('{"old":true}\n', encoding="utf-8")
            loaded = load_openclaw_config(path)
            backup = write_openclaw_config(
                path,
                {"new": True},
                expected=loaded.revision,
                backup=True,
            )
            self.assertIsNotNone(backup)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"new": True})
            assert backup is not None
            self.assertEqual(backup.read_text(encoding="utf-8"), '{"old":true}\n')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob(".openclaw.json.tmp-*")), [])

    def test_load_reports_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "openclaw.json"
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "contains invalid JSON"):
                load_openclaw_config(path)

    def test_write_refuses_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "outside.json"
            target.write_text('{"preserve":true}\n', encoding="utf-8")
            path = root / "openclaw.json"
            path.symlink_to(target)
            with self.assertRaisesRegex(ConfigError, "unable to read|not a symlink"):
                load_openclaw_config(path)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"preserve":true}\n')

    def test_write_aborts_after_concurrent_replacement_or_edit(self) -> None:
        for change in ("replace", "edit"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                path = root / "openclaw.json"
                path.write_text('{"original":true}\n', encoding="utf-8")
                loaded = load_openclaw_config(path)
                if change == "replace":
                    replacement = root / "replacement.json"
                    replacement.write_text('{"concurrent":"replacement"}\n', encoding="utf-8")
                    replacement.replace(path)
                else:
                    path.write_text('{"concurrent":"edit"}\n', encoding="utf-8")

                concurrent_content = path.read_text(encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, "changed after it was read"):
                    write_openclaw_config(
                        path,
                        {"ours": True},
                        expected=loaded.revision,
                        backup=True,
                    )
                self.assertEqual(path.read_text(encoding="utf-8"), concurrent_content)
                self.assertEqual(list(root.glob("*.bak-fleet-otel-*")), [])


if __name__ == "__main__":
    unittest.main()
