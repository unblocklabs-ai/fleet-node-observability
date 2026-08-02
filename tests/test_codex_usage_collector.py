#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_PATH = (
    ROOT / "src" / "fleet_node_observability" / "commands" / "collect_codex_usage.py"
)

spec = importlib.util.spec_from_file_location("codex_usage_collector", COLLECTOR_PATH)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
sys.modules["codex_usage_collector"] = collector
spec.loader.exec_module(collector)


class CodexUsageCollectorTests(unittest.TestCase):
    def install_fake_codex(
        self, bin_dir: Path, *, rate_limits_error: str | None = None
    ) -> Path:
        codex = bin_dir / "codex"
        responses = {
            "initialize": {"serverInfo": {"name": "codex", "version": "test"}},
            "account/read": {
                "account": {
                    "type": "chatgpt",
                    "email": "codex@example.com",
                    "planType": "pro",
                }
            },
            "account/rateLimits/read": {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 9,
                        "windowDurationMins": 300,
                        "resetsAt": 1770000000,
                    },
                    "secondary": {
                        "usedPercent": 44,
                        "windowDurationMins": 10080,
                        "resetsAt": 1770600000,
                    },
                    "credits": {
                        "hasCredits": False,
                        "unlimited": True,
                        "balance": 0,
                    },
                    "planType": "team",
                }
            },
        }
        script = f"""#!{sys.executable}
import json
import os
import sys

responses = {responses!r}
error = {rate_limits_error!r}
initialize_replied = False
initialized = False
for raw in sys.stdin:
    request = json.loads(raw)
    method = request.get("method")
    with open(os.environ["FAKE_CODEX_REQUESTS"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(request, sort_keys=True) + "\\n")
    if method == "initialized":
        if not initialize_replied:
            raise SystemExit("initialized notification arrived before initialize response")
        initialized = True
        continue
    if "id" not in request:
        continue
    if method in {{"account/read", "account/rateLimits/read"}} and not initialized:
        response = {{"id": request["id"], "error": {{"message": "initialized required"}}}}
    elif method == "account/rateLimits/read" and error is not None:
        response = {{"id": request["id"], "error": {{"message": error}}}}
    else:
        response = {{"id": request["id"], "result": responses[method]}}
    print(json.dumps(response), flush=True)
    if method == "initialize":
        initialize_replied = True
"""
        codex.write_text(script, encoding="utf-8")
        codex.chmod(0o755)
        return codex

    def test_app_server_response_maps_usage_credits_and_account(self) -> None:
        snapshot = collector.snapshot_from_app_server(
            {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 9,
                        "windowDurationMins": 300,
                        "resetsAt": 1770000000,
                    },
                    "secondary": {
                        "usedPercent": 44,
                        "windowDurationMins": 10080,
                        "resetsAt": 1770600000,
                    },
                    "credits": {"hasCredits": False, "unlimited": True, "balance": 0},
                    "planType": "team",
                }
            },
            {
                "account": {
                    "type": "chatgpt",
                    "email": "codex@example.com",
                    "planType": "pro",
                }
            },
        )
        self.assertEqual(snapshot["source"], "app_server")
        self.assertEqual(snapshot["account_domain"], "example.com")
        self.assertEqual(snapshot["account_email"], "codex@example.com")
        self.assertEqual(snapshot["plan_type"], "pro")
        self.assertEqual(snapshot["primary"]["used_percent"], 9)
        self.assertEqual(snapshot["credits"]["unlimited"], 1.0)

    def test_fetch_uses_only_supported_app_server_account_methods(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self.install_fake_codex(bin_dir)
            requests_path = root / "requests.jsonl"
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                    "FAKE_CODEX_REQUESTS": str(requests_path),
                },
            ):
                snapshot = collector.fetch_app_server_usage(timeout=1)

            requests = [json.loads(line) for line in requests_path.read_text().splitlines()]
        self.assertEqual(
            [request["method"] for request in requests],
            ["initialize", "initialized", "account/read", "account/rateLimits/read"],
        )
        self.assertNotIn("id", requests[1])
        self.assertEqual(requests[2]["params"], {"refreshToken": False})
        self.assertEqual(snapshot["source"], "app_server")
        serialized = json.dumps(requests)
        self.assertNotIn("access_token", serialized)
        self.assertNotIn("refresh_token", serialized)
        self.assertNotIn("Authorization", serialized)

    def test_missing_app_server_is_a_clear_bounded_failure(self) -> None:
        with mock.patch.object(collector.shutil, "which", return_value=None):
            with self.assertRaises(collector.CollectionError) as raised:
                collector.fetch_app_server_usage(timeout=0.1)
        self.assertEqual(raised.exception.error_type, "app_server_missing")
        self.assertEqual(str(raised.exception), "codex executable not found on PATH")

    def test_app_server_timeout_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            codex = bin_dir / "codex"
            codex.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n")
            codex.chmod(0o755)
            started = time.monotonic()
            with mock.patch.dict(
                os.environ,
                {"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
            ):
                with self.assertRaises(collector.CollectionError) as raised:
                    collector.fetch_app_server_usage(timeout=0.1)
        self.assertEqual(raised.exception.error_type, "app_server_timeout")
        self.assertLess(time.monotonic() - started, 2.0)

    def test_app_server_error_does_not_echo_server_message(self) -> None:
        secret_marker = "do-not-log-this-token"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self.install_fake_codex(bin_dir, rate_limits_error=secret_marker)
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                    "FAKE_CODEX_REQUESTS": str(root / "requests.jsonl"),
                },
            ):
                with self.assertRaises(collector.CollectionError) as raised:
                    collector.fetch_app_server_usage(timeout=1)
        self.assertEqual(
            raised.exception.error_type, "app_server_capability_unavailable"
        )
        self.assertNotIn(secret_marker, str(raised.exception))
        self.assertIn("account/rateLimits/read", str(raised.exception))

    def test_failure_output_is_metric_friendly(self) -> None:
        payload = collector.build_output(
            node="valhalla",
            profile="default",
            snapshot={"source": "app_server"},
            success=False,
            error_type="app_server_missing",
            error_message="codex executable not found on PATH",
        )
        self.assertEqual(payload["collector_success"], 0.0)
        self.assertEqual(payload["severity"], "error")
        self.assertEqual(payload["error_type"], "app_server_missing")
        self.assertEqual(payload["source"], "app_server")

    def test_prometheus_output_preserves_dashboard_metric_names(self) -> None:
        payload = collector.build_output(
            node="bill",
            profile="default",
            snapshot={
                "source": "app_server",
                "account_domain": "example.com",
                "account_email": "bill@example.com",
                "plan_type": "pro",
                "primary": {
                    "used_percent": 25,
                    "remaining_percent": 75,
                    "window_minutes": 300,
                    "resets_at_seconds": 1770000000,
                },
                "secondary": {
                    "used_percent": 50,
                    "remaining_percent": 50,
                    "window_minutes": 10080,
                    "resets_at_seconds": 1770600000,
                },
                "credits": {"has_credits": 1, "unlimited": 0, "balance": 12.5},
                "snapshot_age_seconds": 0,
            },
            success=True,
        )

        text = collector.prometheus_output(payload)
        self.assertIn('codex_collector_success{node="bill"', text)
        self.assertIn("codex_usage_primary_remaining_percent", text)
        self.assertIn("codex_usage_secondary_resets_at_seconds", text)
        self.assertIn("codex_credits_balance", text)
        self.assertIn('account_domain="example.com"', text)
        self.assertIn('account_email="bill@example.com"', text)
        self.assertIn('source="app_server"', text)

    def test_account_email_is_retained_by_decision(self) -> None:
        payload = collector.build_output(
            node="bill",
            profile="default",
            snapshot={
                "source": "app_server",
                "account_domain": collector.account_domain("Bill@Example.COM"),
                "account_email": "Bill@Example.COM",
                "plan_type": "pro",
                "primary": {"used_percent": 25},
            },
            success=True,
        )

        text = collector.prometheus_output(payload)
        self.assertIn('account_domain="example.com"', text)
        self.assertIn('account_email="Bill@Example.COM"', text)

    def test_prometheus_textfile_write_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "codex_usage.prom"
            collector.write_textfile_atomic(path, "codex_collector_success 1\n")
            self.assertEqual(path.read_text(), "codex_collector_success 1\n")
            self.assertFalse(path.with_suffix(".prom.tmp").exists())


if __name__ == "__main__":
    unittest.main()
