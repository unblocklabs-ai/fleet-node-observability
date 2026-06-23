#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
TOKEN_REFRESH_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
UNKNOWN = "unknown"
UTC = dt.timezone.utc
JSONL_TAIL_BYTES = 1024 * 1024


class CollectionError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        self.error_type = error_type
        super().__init__(message)


@dataclass
class CodexCredentials:
    access_token: str
    refresh_token: str
    id_token: str | None
    account_id: str | None
    last_refresh: dt.datetime | None


def now_seconds() -> int:
    return int(time.time())


def iso_now() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_iso8601(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def codex_home_from_env(env: dict[str, str]) -> Path:
    configured = env.get("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


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
    return clean_text(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


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


def parse_jwt_payload(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1].replace("-", "+").replace("_", "/")
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.b64decode(payload)
        parsed = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def identity_from_id_token(id_token: str | None) -> tuple[str | None, str | None]:
    payload = parse_jwt_payload(id_token)
    profile = payload.get("https://api.openai.com/profile")
    auth = payload.get("https://api.openai.com/auth")
    email = payload.get("email")
    if not email and isinstance(profile, dict):
        email = profile.get("email")
    plan = payload.get("chatgpt_plan_type")
    if not plan and isinstance(auth, dict):
        plan = auth.get("chatgpt_plan_type")
    return optional_text(email), optional_text(plan)


def load_credentials(codex_home: Path) -> CodexCredentials:
    path = codex_home / "auth.json"
    if not path.exists():
        raise CollectionError("oauth_auth_missing", f"Codex auth.json not found at {path}")
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionError("oauth_auth_invalid", f"Unable to parse {path}: {exc}") from exc

    if isinstance(raw, dict) and optional_text(raw.get("OPENAI_API_KEY")):
        raise CollectionError("oauth_auth_api_key", "auth.json contains an API key, not ChatGPT OAuth tokens")

    tokens = raw.get("tokens") if isinstance(raw, dict) else None
    if not isinstance(tokens, dict):
        raise CollectionError("oauth_auth_missing_tokens", "auth.json has no tokens object")

    access_token = optional_text(tokens.get("access_token") or tokens.get("accessToken"))
    refresh_token = optional_text(tokens.get("refresh_token") or tokens.get("refreshToken"))
    if not access_token:
        raise CollectionError("oauth_auth_missing_tokens", "auth.json has no access token")

    return CodexCredentials(
        access_token=access_token,
        refresh_token=refresh_token or "",
        id_token=optional_text(tokens.get("id_token") or tokens.get("idToken")),
        account_id=optional_text(tokens.get("account_id") or tokens.get("accountId")),
        last_refresh=parse_iso8601(optional_text(raw.get("last_refresh"))),
    )


def credentials_need_refresh(credentials: CodexCredentials) -> bool:
    if not credentials.refresh_token:
        return False
    if credentials.last_refresh is None:
        return True
    age = dt.datetime.now(UTC) - credentials.last_refresh
    return age > dt.timedelta(days=8)


def save_credentials(codex_home: Path, credentials: CodexCredentials) -> None:
    path = codex_home / "auth.json"
    try:
        raw = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
    tokens["access_token"] = credentials.access_token
    tokens["refresh_token"] = credentials.refresh_token
    if credentials.id_token:
        tokens["id_token"] = credentials.id_token
    if credentials.account_id:
        tokens["account_id"] = credentials.account_id
    raw["tokens"] = tokens
    raw["last_refresh"] = iso_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".auth.", delete=False) as tmp:
            tmp_path = tmp.name
            os.chmod(tmp.name, 0o600)
            json.dump(raw, tmp, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass


def refresh_credentials(credentials: CodexCredentials, timeout: float) -> CodexCredentials:
    body = json.dumps(
        {
            "client_id": CODEX_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": credentials.refresh_token,
            "scope": "openid profile email",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_REFRESH_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CollectionError("oauth_refresh_failed", f"Token refresh failed with HTTP {exc.code}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionError("oauth_refresh_failed", f"Token refresh failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise CollectionError("oauth_refresh_invalid", "Token refresh returned non-object JSON")
    return CodexCredentials(
        access_token=optional_text(payload.get("access_token")) or credentials.access_token,
        refresh_token=optional_text(payload.get("refresh_token")) or credentials.refresh_token,
        id_token=optional_text(payload.get("id_token")) or credentials.id_token,
        account_id=credentials.account_id,
        last_refresh=dt.datetime.now(UTC),
    )


def fetch_oauth_usage(codex_home: Path, timeout: float) -> dict[str, Any]:
    credentials = load_credentials(codex_home)
    if credentials_need_refresh(credentials):
        credentials = refresh_credentials(credentials, timeout)
        save_credentials(codex_home, credentials)

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {credentials.access_token}",
        "User-Agent": "fleet-observability-codex-usage-collector",
    }
    if credentials.account_id:
        headers["ChatGPT-Account-Id"] = credentials.account_id
    request = urllib.request.Request(CHATGPT_USAGE_URL, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise CollectionError("oauth_unauthorized", f"OAuth usage request failed with HTTP {exc.code}") from exc
        raise CollectionError("oauth_http_error", f"OAuth usage request failed with HTTP {exc.code}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionError("oauth_fetch_failed", f"OAuth usage request failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise CollectionError("oauth_invalid_response", "OAuth usage response was not a JSON object")

    email, plan_from_token = identity_from_id_token(credentials.id_token)
    return snapshot_from_oauth(payload, email, plan_from_token)


def parse_oauth_window(window: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(window, dict):
        return None
    used = as_float(window.get("used_percent"))
    reset_at = as_float(window.get("reset_at"))
    window_seconds = as_float(window.get("limit_window_seconds"))
    if used is None and reset_at is None and window_seconds is None:
        return None
    result: dict[str, float] = {}
    if used is not None:
        result["used_percent"] = used
        result["remaining_percent"] = max(0.0, 100.0 - used)
    if reset_at is not None:
        result["resets_at_seconds"] = reset_at
    if window_seconds is not None:
        result["window_minutes"] = window_seconds / 60.0
    return result


def snapshot_from_oauth(payload: dict[str, Any], email: str | None, plan_from_token: str | None) -> dict[str, Any]:
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        rate_limit = {}
    credits = payload.get("credits")
    if not isinstance(credits, dict):
        credits = {}
    snapshot = {
        "source": "oauth",
        "account_domain": account_domain(email),
        "account_email": clean_text(email),
        "plan_type": optional_text(payload.get("plan_type")) or plan_from_token,
        "primary": parse_oauth_window(rate_limit.get("primary_window")),
        "secondary": parse_oauth_window(rate_limit.get("secondary_window")),
        "credits": {
            "has_credits": bool_metric(credits.get("has_credits")),
            "unlimited": bool_metric(credits.get("unlimited")),
            "balance": as_float(credits.get("balance")),
        },
        "snapshot_age_seconds": 0.0,
    }
    if snapshot["primary"] is None and snapshot["secondary"] is None and not any(
        value is not None for value in snapshot["credits"].values()
    ):
        raise CollectionError("oauth_no_usage", "OAuth response did not contain usage windows or credits")
    return snapshot


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

        raise CollectionError("cli_rpc_timeout", "Timed out waiting for Codex RPC response")


def rpc_request(
    process: subprocess.Popen[str], reader: JsonLineReader, request_id: int, method: str, timeout: float
) -> dict[str, Any]:
    assert process.stdin is not None
    process.stdin.write(compact_json({"id": request_id, "method": method, "params": {}}) + "\n")
    process.stdin.flush()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = reader.read(max(0.1, deadline - time.monotonic()))
        if message.get("id") != request_id:
            continue
        if isinstance(message.get("error"), dict):
            error = message["error"]
            raise CollectionError("cli_rpc_error", clean_text(error.get("message"), "Codex RPC returned an error"))
        result = message.get("result")
        if isinstance(result, dict):
            return result
        raise CollectionError("cli_rpc_invalid_response", f"Codex RPC {method} response had no object result")
    raise CollectionError("cli_rpc_timeout", f"Timed out waiting for Codex RPC method {method}")


def fetch_cli_usage(timeout: float) -> dict[str, Any]:
    codex = shutil.which("codex")
    if not codex:
        raise CollectionError("cli_missing", "codex executable not found on PATH")
    process = subprocess.Popen(
        [codex, "-s", "read-only", "-a", "untrusted", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        reader = JsonLineReader(process.stdout)
        process.stdin.write(
            compact_json(
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {"clientInfo": {"name": "fleet-observability", "version": "1.0.0"}},
                }
            )
            + "\n"
        )
        process.stdin.write(compact_json({"method": "initialized", "params": {}}) + "\n")
        process.stdin.flush()
        reader.read(timeout)  # initialize response
        limits = rpc_request(process, reader, 2, "account/rateLimits/read", timeout)
        try:
            account = rpc_request(process, reader, 3, "account/read", timeout)
        except CollectionError:
            account = {}
        return snapshot_from_cli(limits, account)
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


def parse_cli_window(window: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(window, dict):
        return None
    used = as_float(window.get("usedPercent") if "usedPercent" in window else window.get("used_percent"))
    reset_at = as_float(window.get("resetsAt") if "resetsAt" in window else window.get("resets_at"))
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


def snapshot_from_cli(limits_payload: dict[str, Any], account_payload: dict[str, Any]) -> dict[str, Any]:
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
        "source": "cli",
        "account_domain": account_domain(optional_text(account.get("email"))),
        "account_email": clean_text(account.get("email")),
        "plan_type": optional_text(account.get("planType")) or optional_text(rate_limits.get("planType")),
        "primary": parse_cli_window(rate_limits.get("primary")),
        "secondary": parse_cli_window(rate_limits.get("secondary")),
        "credits": {
            "has_credits": bool_metric(credits.get("hasCredits") if "hasCredits" in credits else credits.get("has_credits")),
            "unlimited": bool_metric(credits.get("unlimited")),
            "balance": as_float(credits.get("balance")),
        },
        "snapshot_age_seconds": 0.0,
    }
    if snapshot["primary"] is None and snapshot["secondary"] is None and not any(
        value is not None for value in snapshot["credits"].values()
    ):
        raise CollectionError("cli_no_usage", "CLI RPC did not return usage windows or credits")
    return snapshot


def iter_session_files(codex_home: Path) -> list[Path]:
    roots = [codex_home / "sessions", codex_home / "archived_sessions"]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        files.extend(path for path in root.rglob("*.jsonl") if path.is_file())
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def parse_event_timestamp(value: Any, fallback: int) -> int:
    parsed = parse_iso8601(optional_text(value))
    return int(parsed.timestamp()) if parsed else fallback


def read_jsonl_tail_lines(path: Path, max_bytes: int = JSONL_TAIL_BYTES) -> list[str]:
    size = path.stat().st_size
    start = max(0, size - max_bytes)
    with path.open("rb") as handle:
        handle.seek(start)
        if start > 0:
            handle.readline()
        data = handle.read(max_bytes)
    return data.decode("utf-8", errors="replace").splitlines()


def parse_jsonl_window(window: dict[str, Any] | None, event_time: int) -> dict[str, float] | None:
    if not isinstance(window, dict):
        return None
    used = as_float(window.get("used_percent"))
    window_minutes = as_float(window.get("window_minutes"))
    reset_at = as_float(window.get("resets_at"))
    resets_in = as_float(window.get("resets_in_seconds"))
    if reset_at is None and resets_in is not None:
        reset_at = float(event_time) + resets_in
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


def snapshot_from_jsonl_event(event: dict[str, Any], event_time: int) -> dict[str, Any] | None:
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return None
    if rate_limits.get("limit_id") not in {None, "codex"}:
        return None
    credits = rate_limits.get("credits")
    if not isinstance(credits, dict):
        credits = {}
    primary = parse_jsonl_window(rate_limits.get("primary"), event_time)
    secondary = parse_jsonl_window(rate_limits.get("secondary"), event_time)
    if primary is None and secondary is None:
        return None
    return {
        "source": "jsonl",
        "account_domain": UNKNOWN,
        "account_email": UNKNOWN,
        "plan_type": optional_text(rate_limits.get("plan_type")),
        "primary": primary,
        "secondary": secondary,
        "credits": {
            "has_credits": bool_metric(credits.get("has_credits")),
            "unlimited": bool_metric(credits.get("unlimited")),
            "balance": as_float(credits.get("balance")),
        },
        "snapshot_age_seconds": max(0.0, float(now_seconds() - event_time)),
    }


def fetch_jsonl_usage(codex_home: Path, max_files: int) -> dict[str, Any]:
    for path in iter_session_files(codex_home)[:max_files]:
        try:
            fallback_time = int(path.stat().st_mtime)
            lines = read_jsonl_tail_lines(path)
        except OSError:
            continue
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_time = parse_event_timestamp(event.get("timestamp"), fallback_time)
            snapshot = snapshot_from_jsonl_event(event, event_time)
            if snapshot is not None:
                return snapshot
    raise CollectionError("jsonl_no_usage", f"No usable Codex token_count event found under {codex_home}")


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
        "account_email": payload["account_email"],
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


def write_textfile_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content)
    os.replace(tmp_path, path)


def collect(source: str, codex_home: Path, timeout: float, max_jsonl_files: int) -> dict[str, Any]:
    errors: list[CollectionError] = []
    sources = [source] if source != "auto" else ["oauth", "cli", "jsonl"]
    for candidate in sources:
        try:
            if candidate == "oauth":
                return fetch_oauth_usage(codex_home, timeout)
            if candidate == "cli":
                return fetch_cli_usage(timeout)
            if candidate == "jsonl":
                return fetch_jsonl_usage(codex_home, max_jsonl_files)
        except CollectionError as exc:
            errors.append(exc)
            if source != "auto":
                raise
    if errors:
        summary = "; ".join(f"{error.error_type}: {error}" for error in errors)
        raise CollectionError("all_sources_failed", summary)
    raise CollectionError("invalid_source", f"Unknown source: {source}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit Codex usage telemetry as JSON or Prometheus textfile metrics.")
    parser.add_argument("--node", default=os.uname().nodename.lower())
    parser.add_argument("--profile", default="default")
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--source", choices=["auto", "oauth", "cli", "jsonl"], default="auto")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-jsonl-files", type=int, default=200)
    parser.add_argument("--format", choices=["json", "prometheus"], default="json")
    parser.add_argument("--output", type=Path, help="Write output to this path. Useful for node_exporter textfile metrics.")
    args = parser.parse_args()

    codex_home = args.codex_home.expanduser() if args.codex_home else codex_home_from_env(os.environ)
    try:
        snapshot = collect(args.source, codex_home, args.timeout, args.max_jsonl_files)
        payload = build_output(node=args.node, profile=args.profile, snapshot=snapshot, success=True)
    except CollectionError as exc:
        payload = build_output(
            node=args.node,
            profile=args.profile,
            snapshot={"source": "none", "snapshot_age_seconds": 0.0},
            success=False,
            error_type=exc.error_type,
            error_message=str(exc),
        )
    output = compact_json(payload) + "\n" if args.format == "json" else prometheus_output(payload)
    if args.output:
        write_textfile_atomic(args.output.expanduser(), output)
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
