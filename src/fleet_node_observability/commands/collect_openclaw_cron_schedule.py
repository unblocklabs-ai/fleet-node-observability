"""Export a bounded, node-local view of the OpenClaw cron schedule."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fleet_node_observability.textfile import escape_label_value, write_textfile_atomic

MAX_JOB_ID_LENGTH = 80
MAX_JOB_NAME_LENGTH = 120
MAX_SCHEDULE_LENGTH = 160
MAX_TIMEZONE_LENGTH = 80
KNOWN_SCHEDULE_KINDS = {"at", "cron", "every", "on-exit", "stream"}
KNOWN_JOB_STATUSES = {
    "cancelled",
    "error",
    "failed",
    "never",
    "ok",
    "running",
    "skipped",
    "success",
    "timeout",
}


class CollectionError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def labels(values: dict[str, Any]) -> str:
    return "{" + ",".join(
        f'{key}="{escape_label_value(value)}"' for key, value in values.items()
    ) + "}"


def bounded(value: Any, limit: int, fallback: str) -> str:
    text = " ".join(str(value or fallback).split())
    return text[:limit] or fallback


def parse_json_output(output: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^[ \t]*(?=\{)", output):
        try:
            payload, _ = decoder.raw_decode(output, match.end())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise CollectionError("invalid_json", "openclaw cron list did not return a JSON object")


def collect_jobs(*, timeout: float = 20) -> list[dict[str, Any]]:
    executable = shutil.which("openclaw")
    if executable is None:
        raise CollectionError("binary_missing", "openclaw executable was not found")
    try:
        result = subprocess.run(
            [executable, "cron", "list", "--all", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CollectionError("timeout", "openclaw cron list timed out") from exc
    except OSError as exc:
        raise CollectionError("command_failed", "openclaw cron list could not start") from exc
    if result.returncode != 0:
        raise CollectionError("command_failed", f"openclaw cron list exited {result.returncode}")
    payload = parse_json_output(result.stdout)
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise CollectionError("invalid_payload", "openclaw cron list JSON is missing jobs")
    return jobs


def schedule_identity(schedule: Any) -> tuple[str, str, str]:
    if not isinstance(schedule, dict):
        return "unknown", "unknown", ""
    raw_kind = bounded(schedule.get("kind"), 24, "unknown").lower()
    kind = raw_kind if raw_kind in KNOWN_SCHEDULE_KINDS else "unknown"
    timezone = bounded(schedule.get("tz"), MAX_TIMEZONE_LENGTH, "local")
    if kind == "cron":
        value = bounded(schedule.get("expr"), MAX_SCHEDULE_LENGTH, "unknown")
    elif kind == "every":
        every_ms = schedule.get("everyMs")
        value = f"every:{every_ms}ms" if isinstance(every_ms, (int, float)) else "every:unknown"
    elif kind == "at":
        value = bounded(schedule.get("at"), MAX_SCHEDULE_LENGTH, "unknown")
    elif kind in {"on-exit", "stream"}:
        # Command contents may contain secrets or unbounded user text. The trigger kind is enough
        # for schedule grouping; operators can inspect OpenClaw for command details.
        value = kind
    else:
        value = kind
    return kind, value, timezone


def job_labels(node: str, job: dict[str, Any]) -> dict[str, str]:
    state = job.get("state") if isinstance(job.get("state"), dict) else {}
    kind, schedule, timezone = schedule_identity(job.get("schedule"))
    raw_status = bounded(
        state.get("lastRunStatus") or state.get("lastStatus"), 24, "never"
    ).lower()
    status = raw_status if raw_status in KNOWN_JOB_STATUSES else "other"
    return {
        "node": node,
        "node_label": node,
        "cron_job_id": bounded(job.get("id"), MAX_JOB_ID_LENGTH, "unknown"),
        "cron_job_name": bounded(job.get("name"), MAX_JOB_NAME_LENGTH, "unnamed"),
        "cron_schedule_kind": kind,
        "cron_schedule": schedule,
        "cron_timezone": timezone,
        "cron_enabled": "true" if job.get("enabled", True) else "false",
        "cron_status": status,
        "cron_running": "true" if state.get("runningAtMs") else "false",
    }


def render(node: str, jobs: list[dict[str, Any]], *, now: int | None = None) -> str:
    timestamp = int(time.time()) if now is None else now
    base = {"node": node, "node_label": node}
    lines = [
        "# HELP openclaw_cron_schedule_collected_at_seconds Unix timestamp of the latest local OpenClaw cron schedule collection.",
        "# TYPE openclaw_cron_schedule_collected_at_seconds gauge",
        f"openclaw_cron_schedule_collected_at_seconds{labels(base)} {timestamp}",
        "# HELP openclaw_cron_schedule_collector_success Whether the latest local OpenClaw cron schedule collection succeeded.",
        "# TYPE openclaw_cron_schedule_collector_success gauge",
        f"openclaw_cron_schedule_collector_success{labels(base)} 1",
        "# HELP openclaw_cron_schedule_jobs_total OpenClaw cron jobs by enabled state.",
        "# TYPE openclaw_cron_schedule_jobs_total gauge",
    ]
    enabled_counts = Counter("true" if job.get("enabled", True) else "false" for job in jobs)
    for enabled in ("true", "false"):
        lines.append(
            f'openclaw_cron_schedule_jobs_total{labels({**base, "cron_enabled": enabled})} '
            f"{enabled_counts[enabled]}"
        )

    groups: Counter[tuple[str, str, str]] = Counter()
    for job in jobs:
        if job.get("enabled", True):
            groups[schedule_identity(job.get("schedule"))] += 1
    lines.extend(
        [
            "# HELP openclaw_cron_schedule_group_jobs Enabled OpenClaw cron jobs sharing one local schedule.",
            "# TYPE openclaw_cron_schedule_group_jobs gauge",
        ]
    )
    for (kind, schedule, timezone), count in sorted(groups.items()):
        group_labels = {
            **base,
            "cron_schedule_kind": kind,
            "cron_schedule": schedule,
            "cron_timezone": timezone,
        }
        lines.append(f"openclaw_cron_schedule_group_jobs{labels(group_labels)} {count}")

    lines.extend(
        [
            "# HELP openclaw_cron_job_info Bounded identity and latest state for an OpenClaw cron job.",
            "# TYPE openclaw_cron_job_info gauge",
            "# HELP openclaw_cron_job_next_run_timestamp_seconds Unix timestamp of the job's next scheduled run.",
            "# TYPE openclaw_cron_job_next_run_timestamp_seconds gauge",
            "# HELP openclaw_cron_job_last_duration_seconds Duration of the job's latest run.",
            "# TYPE openclaw_cron_job_last_duration_seconds gauge",
            "# HELP openclaw_cron_job_consecutive_errors Current consecutive execution error count.",
            "# TYPE openclaw_cron_job_consecutive_errors gauge",
        ]
    )
    for job in sorted(jobs, key=lambda item: (str(item.get("name") or ""), str(item.get("id") or ""))):
        state = job.get("state") if isinstance(job.get("state"), dict) else {}
        identity = job_labels(node, job)
        lines.append(f"openclaw_cron_job_info{labels(identity)} 1")
        next_run_ms = state.get("nextRunAtMs")
        if isinstance(next_run_ms, (int, float)) and next_run_ms > 0:
            lines.append(
                f"openclaw_cron_job_next_run_timestamp_seconds{labels(identity)} {next_run_ms / 1000:g}"
            )
        duration_ms = state.get("lastDurationMs")
        if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
            lines.append(
                f"openclaw_cron_job_last_duration_seconds{labels(identity)} {duration_ms / 1000:g}"
            )
        errors = state.get("consecutiveErrors")
        error_count = errors if isinstance(errors, (int, float)) and errors >= 0 else 0
        lines.append(f"openclaw_cron_job_consecutive_errors{labels(identity)} {error_count:g}")
    return "\n".join(lines) + "\n"


def render_failure(node: str, error_type: str, *, now: int | None = None) -> str:
    timestamp = int(time.time()) if now is None else now
    base = {"node": node, "node_label": node}
    return "\n".join(
        [
            "# HELP openclaw_cron_schedule_collected_at_seconds Unix timestamp of the latest local OpenClaw cron schedule collection.",
            "# TYPE openclaw_cron_schedule_collected_at_seconds gauge",
            f"openclaw_cron_schedule_collected_at_seconds{labels(base)} {timestamp}",
            "# HELP openclaw_cron_schedule_collector_success Whether the latest local OpenClaw cron schedule collection succeeded.",
            "# TYPE openclaw_cron_schedule_collector_success gauge",
            f"openclaw_cron_schedule_collector_success{labels(base)} 0",
            "# HELP openclaw_cron_schedule_collection_error Latest bounded collection failure category.",
            "# TYPE openclaw_cron_schedule_collection_error gauge",
            f'openclaw_cron_schedule_collection_error{labels({**base, "error_type": error_type})} 1',
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default=socket.gethostname().split(".", 1)[0].lower())
    parser.add_argument("--output", type=Path, help="Write Prometheus textfile output here.")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()

    try:
        content = render(args.node, collect_jobs(timeout=args.timeout))
        status = 0
    except CollectionError as exc:
        content = render_failure(args.node, exc.error_type)
        status = 1
    if args.output:
        write_textfile_atomic(args.output.expanduser(), content)
    else:
        print(content, end="")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
