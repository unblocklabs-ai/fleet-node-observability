from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_PATH = (
    ROOT
    / "src"
    / "fleet_node_observability"
    / "commands"
    / "collect_openclaw_cron_schedule.py"
)
spec = importlib.util.spec_from_file_location("openclaw_cron_schedule_collector", COLLECTOR_PATH)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
sys.modules["openclaw_cron_schedule_collector"] = collector
spec.loader.exec_module(collector)


class OpenClawCronScheduleCollectorTests(unittest.TestCase):
    def test_render_groups_enabled_jobs_by_local_schedule(self) -> None:
        jobs = [
            {
                "id": "job-a",
                "name": "First job",
                "enabled": True,
                "schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "America/New_York"},
                "state": {
                    "nextRunAtMs": 1_800_000_000_000,
                    "lastRunStatus": "ok",
                    "lastDurationMs": 90_000,
                    "consecutiveErrors": 0,
                },
            },
            {
                "id": "job-b",
                "name": "Second job",
                "enabled": True,
                "schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "America/New_York"},
                "state": {
                    "nextRunAtMs": 1_800_000_000_000,
                    "lastRunStatus": "error",
                    "lastDurationMs": 240_000,
                    "consecutiveErrors": 2,
                    "lastRunAtMs": 1_799_998_123_456,
                    "runningAtMs": 1_799_999_000_000,
                },
            },
            {
                "id": "job-c",
                "name": "Disabled job",
                "enabled": False,
                "schedule": {"kind": "every", "everyMs": 300_000},
                "state": {},
            },
        ]

        text = collector.render("pearl", jobs, now=1_700_000_000)

        self.assertIn(
            'openclaw_cron_schedule_group_jobs{node="pearl",node_label="pearl",cron_schedule_kind="cron",cron_schedule="0 9 * * *",cron_timezone="America/New_York"} 2',
            text,
        )
        self.assertNotIn('cron_schedule="every:300000ms"} 1', text)
        self.assertIn('openclaw_cron_schedule_jobs_total{node="pearl",node_label="pearl",cron_enabled="true"} 2', text)
        self.assertIn('cron_job_name="Second job"', text)
        self.assertIn('cron_status="error"', text)
        self.assertIn('cron_running="true"', text)
        self.assertIn("openclaw_cron_job_next_run_timestamp_seconds", text)
        self.assertIn("openclaw_cron_job_last_duration_seconds", text)
        self.assertIn("openclaw_cron_job_consecutive_errors", text)
        self.assertRegex(text, r'openclaw_cron_job_last_run_timestamp_seconds\{[^\n]+\} 1799998123.456')
        self.assertRegex(text, r'openclaw_cron_job_running_since_timestamp_seconds\{[^\n]+\} 1799999000.000')

    def test_absent_or_invalid_run_times_are_not_reported_as_epoch(self) -> None:
        for value in (None, 0, -1, True, "123", float("nan"), float("inf")):
            text = collector.render("bill", [{"id": "a", "state": {"lastRunAtMs": value, "runningAtMs": value}}])
            samples = [line for line in text.splitlines() if not line.startswith("#")]
            self.assertFalse(any(line.startswith(("openclaw_cron_job_last_run_timestamp_seconds", "openclaw_cron_job_running_since_timestamp_seconds")) for line in samples))

    def test_trigger_commands_and_control_characters_never_become_labels(self) -> None:
        job = {
            "id": "job-control",
            "name": "Safe\nname\twith controls",
            "enabled": True,
            "schedule": {"kind": "stream", "command": ["sh", "-c", "token=secret"]},
            "state": {"lastStatus": "secret-specific-status"},
        }

        text = collector.render("bill", [job], now=1)

        self.assertIn('cron_schedule="stream"', text)
        self.assertNotIn("token=secret", text)
        self.assertNotIn("secret-specific-status", text)
        self.assertNotIn("Safe\\n", text)
        self.assertIn('cron_job_name="Safe name with controls"', text)
        self.assertIn('cron_status="other"', text)

    def test_parse_accepts_openclaw_banner_before_json(self) -> None:
        payload = collector.parse_json_output(
            "OpenClaw diagnostics ready\n"
            + json.dumps({"jobs": [{"id": "one"}]})
            + "\ntrailing diagnostic"
        )
        self.assertEqual(payload["jobs"][0]["id"], "one")

    def test_collection_uses_all_jobs_and_bounded_failure_categories(self) -> None:
        completed = collector.subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"jobs": []}), stderr=""
        )
        with (
            mock.patch.object(collector.shutil, "which", return_value="/bin/openclaw"),
            mock.patch.object(collector.Path, "home", return_value=Path("/Users/managed-node")),
            mock.patch.object(collector.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual(collector.collect_jobs(timeout=1), [])
        run.assert_called_once_with(
            ["/bin/openclaw", "cron", "list", "--all", "--json"],
            cwd=Path("/Users/managed-node"),
            capture_output=True,
            text=True,
            check=False,
            timeout=1,
        )

        with (
            mock.patch.object(collector.shutil, "which", return_value=None),
            self.assertRaisesRegex(collector.CollectionError, "not found") as raised,
        ):
            collector.collect_jobs(timeout=1)
        self.assertEqual(raised.exception.error_type, "binary_missing")
        failure = collector.render_failure("bill", raised.exception.error_type, now=2)
        self.assertIn('error_type="binary_missing"', failure)
        self.assertNotIn("not found", failure)

    def test_atomic_output_replaces_stale_success_with_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "cron_schedule.prom"
            output.write_text("stale success\n", encoding="utf-8")
            collector.write_textfile_atomic(
                output, collector.render_failure("bill", "command_failed", now=3)
            )
            text = output.read_text(encoding="utf-8")
        self.assertNotIn("stale success", text)
        self.assertIn("openclaw_cron_schedule_collector_success", text)
        self.assertIn('error_type="command_failed"', text)


if __name__ == "__main__":
    unittest.main()
