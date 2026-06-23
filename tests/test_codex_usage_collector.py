#!/usr/bin/env python3
from __future__ import annotations

import base64
import importlib.util
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_PATH = ROOT / "src" / "fleet_node_observability" / "commands" / "collect_codex_usage.py"

spec = importlib.util.spec_from_file_location("codex_usage_collector", COLLECTOR_PATH)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
sys.modules["codex_usage_collector"] = collector
spec.loader.exec_module(collector)


def jwt(payload: dict) -> str:
    raw = json.dumps(payload).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


class CodexUsageCollectorTests(unittest.TestCase):
    def test_oauth_response_maps_usage_credits_and_identity(self) -> None:
        email, plan = collector.identity_from_id_token(
            jwt(
                {
                    "email": "fleet@example.com",
                    "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
                }
            )
        )
        snapshot = collector.snapshot_from_oauth(
            {
                "plan_type": "plus",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 12,
                        "reset_at": 1770000000,
                        "limit_window_seconds": 18000,
                    },
                    "secondary_window": {
                        "used_percent": 34,
                        "reset_at": 1770600000,
                        "limit_window_seconds": 604800,
                    },
                },
                "credits": {"has_credits": True, "unlimited": False, "balance": "4.5"},
            },
            email,
            plan,
        )
        self.assertEqual(snapshot["source"], "oauth")
        self.assertEqual(snapshot["account_domain"], "example.com")
        self.assertEqual(snapshot["account_email"], "fleet@example.com")
        self.assertEqual(snapshot["plan_type"], "plus")
        self.assertEqual(snapshot["primary"]["remaining_percent"], 88)
        self.assertEqual(snapshot["primary"]["window_minutes"], 300)
        self.assertEqual(snapshot["secondary"]["window_minutes"], 10080)
        self.assertEqual(snapshot["credits"]["has_credits"], 1.0)
        self.assertEqual(snapshot["credits"]["balance"], 4.5)

    def test_cli_response_maps_usage_credits_and_account(self) -> None:
        snapshot = collector.snapshot_from_cli(
            {
                "rateLimits": {
                    "primary": {"usedPercent": 9, "windowDurationMins": 300, "resetsAt": 1770000000},
                    "secondary": {"usedPercent": 44, "windowDurationMins": 10080, "resetsAt": 1770600000},
                    "credits": {"hasCredits": False, "unlimited": True, "balance": 0},
                    "planType": "team",
                }
            },
            {"account": {"type": "chatgpt", "email": "codex@example.com", "planType": "pro"}},
        )
        self.assertEqual(snapshot["source"], "cli")
        self.assertEqual(snapshot["account_domain"], "example.com")
        self.assertEqual(snapshot["account_email"], "codex@example.com")
        self.assertEqual(snapshot["plan_type"], "pro")
        self.assertEqual(snapshot["primary"]["used_percent"], 9)
        self.assertEqual(snapshot["credits"]["unlimited"], 1.0)

    def test_jsonl_fallback_uses_last_observed_token_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            session_dir = codex_home / "sessions" / "2026" / "05" / "28"
            session_dir.mkdir(parents=True)
            event_time = int(collector.parse_iso8601("2026-02-01T00:00:00Z").timestamp())
            (session_dir / "rollout.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"timestamp": "2026-02-01T00:00:00Z", "type": "event_msg", "payload": {}}),
                        json.dumps(
                            {
                                "timestamp": "2026-02-01T00:00:00Z",
                                "type": "event_msg",
                                "payload": {
                                    "type": "token_count",
                                    "rate_limits": {
                                        "limit_id": "codex",
                                        "primary": {
                                            "used_percent": 5,
                                            "window_minutes": 300,
                                            "resets_in_seconds": 60,
                                        },
                                        "secondary": {
                                            "used_percent": 8,
                                            "window_minutes": 10080,
                                            "resets_at": event_time + 600,
                                        },
                                        "credits": {"has_credits": False, "unlimited": False, "balance": None},
                                        "plan_type": "pro",
                                    },
                                },
                            }
                        ),
                    ]
                )
            )
            snapshot = collector.fetch_jsonl_usage(codex_home, 10)
        self.assertEqual(snapshot["source"], "jsonl")
        self.assertEqual(snapshot["plan_type"], "pro")
        self.assertEqual(snapshot["primary"]["resets_at_seconds"], event_time + 60)
        self.assertEqual(snapshot["secondary"]["resets_at_seconds"], event_time + 600)

    def test_jsonl_fallback_reads_bounded_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            session_dir = codex_home / "sessions"
            session_dir.mkdir()
            session_file = session_dir / "large.jsonl"
            session_file.write_text("x" * (collector.JSONL_TAIL_BYTES + 1024))
            with session_file.open("a") as handle:
                handle.write(
                    "\n"
                    + json.dumps(
                        {
                            "timestamp": "2026-02-01T00:00:00Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "rate_limits": {
                                    "primary": {"used_percent": 11, "window_minutes": 300, "resets_at": 1770000000}
                                },
                            },
                        }
                    )
                )
            snapshot = collector.fetch_jsonl_usage(codex_home, 10)
        self.assertEqual(snapshot["source"], "jsonl")
        self.assertEqual(snapshot["primary"]["used_percent"], 11)

    def test_save_credentials_is_private_and_atomic_enough_for_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            auth_path = codex_home / "auth.json"
            auth_path.write_text(json.dumps({"tokens": {"access_token": "old", "refresh_token": "refresh"}}))
            auth_path.chmod(0o644)

            collector.save_credentials(
                codex_home,
                collector.CodexCredentials(
                    access_token="new",
                    refresh_token="new-refresh",
                    id_token="id",
                    account_id="acct",
                    last_refresh=None,
                ),
            )

            payload = json.loads(auth_path.read_text())
            self.assertEqual(payload["tokens"]["access_token"], "new")
            self.assertEqual(stat.S_IMODE(auth_path.stat().st_mode), 0o600)

    def test_cli_usage_times_out_when_app_server_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            codex = bin_dir / "codex"
            codex.write_text("#!/bin/sh\nsleep 5\n")
            codex.chmod(0o755)
            old_path = os.environ.get("PATH")
            os.environ["PATH"] = str(bin_dir)
            started = time.monotonic()
            try:
                with self.assertRaises(collector.CollectionError) as raised:
                    collector.fetch_cli_usage(timeout=0.1)
            finally:
                if old_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = old_path
        self.assertEqual(raised.exception.error_type, "cli_rpc_timeout")
        self.assertLess(time.monotonic() - started, 2.0)

    def test_failure_output_is_metric_friendly(self) -> None:
        payload = collector.build_output(
            node="valhalla",
            profile="default",
            snapshot={"source": "none"},
            success=False,
            error_type="oauth_auth_missing",
            error_message="missing",
        )
        self.assertEqual(payload["collector_success"], 0.0)
        self.assertEqual(payload["severity"], "error")
        self.assertEqual(payload["error_type"], "oauth_auth_missing")

    def test_prometheus_output_preserves_dashboard_metric_names(self) -> None:
        payload = collector.build_output(
            node="bill",
            profile="default",
            snapshot={
                "source": "jsonl",
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
                "snapshot_age_seconds": 4,
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

    def test_account_email_is_retained_by_decision(self) -> None:
        payload = collector.build_output(
            node="bill",
            profile="default",
            snapshot={
                "source": "oauth",
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

    def test_collect_auto_falls_back_to_jsonl_after_oauth_and_cli_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            session_dir = codex_home / "sessions"
            session_dir.mkdir()
            (session_dir / "latest.jsonl").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-02-01T00:00:00Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {"used_percent": 1, "window_minutes": 300, "resets_at": 1770000000}
                            },
                        },
                    }
                )
            )
            old_path = os.environ.get("PATH")
            os.environ["PATH"] = ""
            try:
                snapshot = collector.collect("auto", codex_home, timeout=0.01, max_jsonl_files=10)
            finally:
                if old_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = old_path
        self.assertEqual(snapshot["source"], "jsonl")
        self.assertEqual(snapshot["primary"]["used_percent"], 1)


if __name__ == "__main__":
    unittest.main()
