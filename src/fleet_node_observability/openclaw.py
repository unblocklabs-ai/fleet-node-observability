"""Safe OpenClaw diagnostics configuration for the loopback Collector."""

from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from .atomic import write_new_private_file, write_private_atomic
from .config import ConfigError

MAX_OPENCLAW_CONFIG_BYTES = 1024 * 1024
_STABLE_STAT_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")


@dataclass(frozen=True)
class OpenClawRevision:
    path: Path
    stat_values: tuple[int, ...] | None
    content: str


@dataclass
class LoadedOpenClawConfig:
    payload: dict
    revision: OpenClawRevision


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _read_revision(path: Path) -> OpenClawRevision:
    absolute = _absolute_path(path)
    descriptor = -1
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return OpenClawRevision(path=absolute, stat_values=None, content="")
    except OSError as exc:
        raise ConfigError(f"unable to read {path}: {exc}") from exc

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ConfigError(f"{path} must be a regular file, not a symlink or directory")
        if before.st_size > MAX_OPENCLAW_CONFIG_BYTES:
            raise ConfigError(f"{path} exceeds the 1 MiB safety limit")

        chunks: list[bytes] = []
        total = 0
        while total <= MAX_OPENCLAW_CONFIG_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, MAX_OPENCLAW_CONFIG_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_OPENCLAW_CONFIG_BYTES:
            raise ConfigError(f"{path} exceeds the 1 MiB safety limit")

        after = os.fstat(descriptor)
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise ConfigError(f"{path} changed while it was being read") from exc
        before_values = tuple(getattr(before, field) for field in _STABLE_STAT_FIELDS)
        after_values = tuple(getattr(after, field) for field in _STABLE_STAT_FIELDS)
        current_values = tuple(getattr(current, field) for field in _STABLE_STAT_FIELDS)
        if before_values != after_values or after_values != current_values:
            raise ConfigError(f"{path} changed while it was being read")
    finally:
        os.close(descriptor)

    try:
        content = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path} must contain valid UTF-8") from exc
    return OpenClawRevision(path=absolute, stat_values=after_values, content=content)


def load_openclaw_config(path: Path) -> LoadedOpenClawConfig:
    revision = _read_revision(path)
    if revision.stat_values is None:
        return LoadedOpenClawConfig(payload={}, revision=revision)
    try:
        payload = json.loads(revision.content)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} contains invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return LoadedOpenClawConfig(payload=payload, revision=revision)


def apply_loopback_diagnostics(payload: dict, *, endpoint: str) -> dict:
    diagnostics = payload.get("diagnostics")
    if diagnostics is None:
        diagnostics = {}
        payload["diagnostics"] = diagnostics
    if not isinstance(diagnostics, dict):
        raise ConfigError("diagnostics must be an object")

    otel = diagnostics.get("otel")
    if otel is None:
        otel = {}
        diagnostics["otel"] = otel
    if not isinstance(otel, dict):
        raise ConfigError("diagnostics.otel must be an object")

    diagnostics["enabled"] = True
    for legacy_endpoint in ("tracesEndpoint", "metricsEndpoint", "logsEndpoint"):
        otel.pop(legacy_endpoint, None)
    otel.update(
        {
            "enabled": True,
            "endpoint": endpoint,
            "protocol": "http/protobuf",
            "serviceName": "openclaw_gateway",
            "traces": True,
            "metrics": True,
            "logs": True,
            "logsExporter": "otlp",
            "captureContent": False,
            "headers": {},
        }
    )
    return payload


def _assert_unchanged(path: Path, expected: OpenClawRevision) -> None:
    if _absolute_path(path) != expected.path or _read_revision(path) != expected:
        raise ConfigError(f"{path} changed after it was read; refusing to overwrite it")


def write_openclaw_config(
    path: Path,
    payload: dict,
    *,
    expected: OpenClawRevision,
    backup: bool,
) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_unchanged(path, expected)
    backup_path = None
    if backup and expected.stat_values is not None:
        backup_path = path.with_name(
            f"{path.name}.bak-fleet-otel-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        )
        try:
            write_new_private_file(backup_path, expected.content)
        except OSError as exc:
            raise ConfigError(f"unable to create backup {backup_path}: {exc}") from exc

    _assert_unchanged(path, expected)
    try:
        write_private_atomic(path, json.dumps(payload, indent=2) + "\n")
    except OSError as exc:
        raise ConfigError(f"unable to write {path}: {exc}") from exc
    return backup_path
