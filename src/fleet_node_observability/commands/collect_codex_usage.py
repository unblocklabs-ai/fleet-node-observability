#!/usr/bin/env python3
"""Collect Codex account usage through the supported Codex app-server API."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import select
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from fleet_node_observability.textfile import escape_label_value, write_textfile_atomic


UNKNOWN = "unknown"
UTC = dt.timezone.utc


class CollectionError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        self.error_type = error_type
        super().__init__(message)


def now_seconds() -> int:
    return int(time.time())


def iso_now() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def compact_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def clean_text(value: Any, default: str = UNKNOWN) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def escape_prom_label(value: Any) -> str:
    return escape_label_value(clean_text(value))


def prom_labels(labels: dict[str, Any]) -> str:
    pairs = [f'{key}="{escape_prom_label(value)}"' for key, value in labels.items()]
    return "{" + ",".join(pairs) + "}" if pairs else ""


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bool_metric(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return 1.0
        if normalized in {"0", "false", "no"}:
            return 0.0
    return None


def account_domain(email: str | None) -> str:
    text = clean_text(email)
    if not text or "@" not in text:
        return UNKNOWN
    domain = text.rsplit("@", 1)[1].strip().lower()
    return domain or UNKNOWN


class JsonLineReader:
    def __init__(self, stdout: Any):
        self.fd = stdout.fileno()
        self.buffer = ""

    def read(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    return payload

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                break
            chunk = os.read(self.fd, 4096)
            if not chunk:
                break
            self.buffer += chunk.decode("utf-8", errors="replace")

        raise CollectionError(
            "app_server_timeout", "Timed out waiting for Codex app-server response"
        )


def rpc_request(
    process: subprocess.Popen[str],
    reader: JsonLineReader,
    request_id: int,
    method: str,
    timeout: float,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assert process.stdin is not None
    process.stdin.write(
        compact_json(
            {
                "id": request_id,
                "method": method,
                "params": params if params is not None else {},
            }
        )
        + "\n"
    )
    process.stdin.flush()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = reader.read(max(0.1, deadline - time.monotonic()))
        if message.get("id") != request_id:
            continue
        if isinstance(message.get("error"), dict):
            raise CollectionError(
                "app_server_capability_unavailable",
                f"Codex app-server method {method} failed",
            )
        result = message.get("result")
        if isinstance(result, dict):
            return result
        raise CollectionError(
            "app_server_invalid_response",
            f"Codex app-server method {method} returned no object result",
        )
    raise CollectionError(
        "app_server_timeout", f"Timed out waiting for Codex app-server method {method}"
    )


def fetch_app_server_usage(timeout: float) -> dict[str, Any]:
    """Read account identity and rate limits from Codex-owned auth state."""

    codex = shutil.which("codex")
    if not codex:
        raise CollectionError("app_server_missing", "codex executable not found on PATH")
    try:
        process = subprocess.Popen(
            [codex, "-s", "read-only", "-a", "untrusted", "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError as exc:
        raise CollectionError(
            "app_server_start_failed", "Unable to start Codex app-server"
        ) from exc

    try:
        assert process.stdin is not None
        assert process.stdout is not None
        reader = JsonLineReader(process.stdout)
        deadline = time.monotonic() + timeout
        rpc_request(
            process,
            reader,
            1,
            "initialize",
            max(0.01, deadline - time.monotonic()),
            params={"clientInfo": {"name": "fleet-node-observability", "version": "0.2.0"}},
        )
        process.stdin.write(compact_json({"method": "initialized", "params": {}}) + "\n")
        process.stdin.flush()
        account = rpc_request(
            process,
            reader,
            2,
            "account/read",
            max(0.01, deadline - time.monotonic()),
            params={"refreshToken": False},
        )
        limits = rpc_request(
            process,
            reader,
            3,
            "account/rateLimits/read",
            max(0.01, deadline - time.monotonic()),
        )
        return snapshot_from_app_server(limits, account)
    except (BrokenPipeError, OSError) as exc:
        raise CollectionError(
            "app_server_connection_failed", "Codex app-server connection closed unexpectedly"
        ) from exc
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


def parse_app_server_window(window: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(window, dict):
        return None
    used = as_float(
        window.get("usedPercent") if "usedPercent" in window else window.get("used_percent")
    )
    reset_at = as_float(
        window.get("resetsAt") if "resetsAt" in window else window.get("resets_at")
    )
    window_minutes = as_float(
        window.get("windowDurationMins")
        if "windowDurationMins" in window
        else window.get("window_minutes")
    )
    if used is None and reset_at is None and window_minutes is None:
        return None
    result: dict[str, float] = {}
    if used is not None:
        result["used_percent"] = used
        result["remaining_percent"] = max(0.0, 100.0 - used)
    if reset_at is not None:
        result["resets_at_seconds"] = reset_at
    if window_minutes is not None:
        result["window_minutes"] = window_minutes
    return result


def snapshot_from_app_server(
    limits_payload: dict[str, Any], account_payload: dict[str, Any]
) -> dict[str, Any]:
    rate_limits = limits_payload.get("rateLimits")
    if not isinstance(rate_limits, dict):
        rate_limits = limits_payload
    account = account_payload.get("account")
    if not isinstance(account, dict):
        account = {}
    credits = rate_limits.get("credits")
    if not isinstance(credits, dict):
        credits = {}
    snapshot = {
        "source": "app_server",
        "account_domain": account_domain(optional_text(account.get("email"))),
        "account_email": clean_text(account.get("email")),
        "plan_type": optional_text(account.get("planType"))
        or optional_text(rate_limits.get("planType")),
        "primary": parse_app_server_window(rate_limits.get("primary")),
        "secondary": parse_app_server_window(rate_limits.get("secondary")),
        "credits": {
            "has_credits": bool_metric(
                credits.get("hasCredits")
                if "hasCredits" in credits
                else credits.get("has_credits")
            ),
            "unlimited": bool_metric(credits.get("unlimited")),
            "balance": as_float(credits.get("balance")),
        },
        "snapshot_age_seconds": 0.0,
    }
    if snapshot["primary"] is None and snapshot["secondary"] is None and not any(
        value is not None for value in snapshot["credits"].values()
    ):
        raise CollectionError(
            "app_server_no_usage",
            "Codex app-server did not return usage windows or credits",
        )
    return snapshot


def flatten_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for prefix in ("primary", "secondary"):
        window = snapshot.get(prefix)
        if not isinstance(window, dict):
            continue
        out[f"{prefix}_used_percent"] = window.get("used_percent")
        out[f"{prefix}_remaining_percent"] = window.get("remaining_percent")
        out[f"{prefix}_window_minutes"] = window.get("window_minutes")
        out[f"{prefix}_resets_at_seconds"] = window.get("resets_at_seconds")
    credits = snapshot.get("credits")
    if isinstance(credits, dict):
        out["credits_has_credits"] = credits.get("has_credits")
        out["credits_unlimited"] = credits.get("unlimited")
        out["credits_balance"] = credits.get("balance")
    return out


def build_output(
    *,
    node: str,
    profile: str,
    snapshot: dict[str, Any] | None,
    success: bool,
    error_type: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or {}
    payload = {
        "message": "codex usage snapshot" if success else "codex usage collector failed",
        "event_type": "codex_usage_snapshot",
        "log_type": "codex_usage",
        "component": "codex_usage_collector",
        "severity": "info" if success else "error",
        "issue_type": "none" if success else "codex_usage_collection_failed",
        "issue_group": "none" if success else "codex_usage",
        "node": node,
        "profile": profile,
        "source": clean_text(snapshot.get("source"), "none"),
        "collector_success": 1.0 if success else 0.0,
        "collected_at": iso_now(),
        "collected_at_seconds": float(now_seconds()),
        "snapshot_age_seconds": as_float(snapshot.get("snapshot_age_seconds")) or 0.0,
        "account_domain": clean_text(snapshot.get("account_domain")),
        "account_email": clean_text(snapshot.get("account_email")),
        "plan_type": clean_text(snapshot.get("plan_type")),
        "error_type": clean_text(error_type, "none"),
        "error_message": clean_text(error_message, "none"),
    }
    payload.update(flatten_snapshot(snapshot))
    return payload


def prom_sample(name: str, value: Any, labels: dict[str, Any]) -> str | None:
    number = as_float(value)
    if number is None:
        return None
    return f"{name}{prom_labels(labels)} {number}"


def prometheus_output(payload: dict[str, Any]) -> str:
    identity_labels = {
        "node": payload["node"],
        "profile": payload["profile"],
        "account_domain": payload["account_domain"],
        "plan_type": payload["plan_type"],
        "source": payload["source"],
    }
    collector_labels = {
        **identity_labels,
        "error_type": payload["error_type"],
    }
    metric_map = {
        "collector_success": "codex_collector_success",
        "snapshot_age_seconds": "codex_usage_snapshot_age_seconds",
        "collected_at_seconds": "codex_usage_collected_at_seconds",
        "primary_used_percent": "codex_usage_primary_used_percent",
        "primary_remaining_percent": "codex_usage_primary_remaining_percent",
        "primary_window_minutes": "codex_usage_primary_window_minutes",
        "primary_resets_at_seconds": "codex_usage_primary_resets_at_seconds",
        "secondary_used_percent": "codex_usage_secondary_used_percent",
        "secondary_remaining_percent": "codex_usage_secondary_remaining_percent",
        "secondary_window_minutes": "codex_usage_secondary_window_minutes",
        "secondary_resets_at_seconds": "codex_usage_secondary_resets_at_seconds",
        "credits_has_credits": "codex_credits_has_credits",
        "credits_unlimited": "codex_credits_unlimited",
        "credits_balance": "codex_credits_balance",
    }
    help_lines = [
        "# HELP codex_collector_success Whether the latest Codex usage collection attempt succeeded.",
        "# TYPE codex_collector_success gauge",
        "# HELP codex_usage_snapshot_age_seconds Age of the source Codex usage snapshot.",
        "# TYPE codex_usage_snapshot_age_seconds gauge",
        "# HELP codex_usage_collected_at_seconds Unix timestamp when Codex usage was collected.",
        "# TYPE codex_usage_collected_at_seconds gauge",
    ]
    samples: list[str] = []
    for payload_key, metric_name in metric_map.items():
        labels = collector_labels if payload_key == "collector_success" else identity_labels
        sample = prom_sample(metric_name, payload.get(payload_key), labels)
        if sample:
            samples.append(sample)
    return "\n".join(help_lines + samples) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit Codex app-server usage telemetry as JSON or Prometheus textfile metrics."
    )
    parser.add_argument("--node", default=os.uname().nodename.lower())
    parser.add_argument("--profile", default="default")
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Total seconds allowed for the app-server request sequence.",
    )
    parser.add_argument("--format", choices=["json", "prometheus"], default="json")
    parser.add_argument(
        "--output", type=Path, help="Write output to this node_exporter textfile path."
    )
    args = parser.parse_args(argv)

    try:
        snapshot = fetch_app_server_usage(args.timeout)
        payload = build_output(
            node=args.node, profile=args.profile, snapshot=snapshot, success=True
        )
    except CollectionError as exc:
        payload = build_output(
            node=args.node,
            profile=args.profile,
            snapshot={"source": "app_server", "snapshot_age_seconds": 0.0},
            success=False,
            error_type=exc.error_type,
            error_message=str(exc),
        )
    output = (
        compact_json(payload) + "\n"
        if args.format == "json"
        else prometheus_output(payload)
    )
    if args.output:
        write_textfile_atomic(args.output.expanduser(), output)
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
