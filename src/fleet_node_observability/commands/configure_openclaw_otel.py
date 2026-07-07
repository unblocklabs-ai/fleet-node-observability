"""Configure durable OpenClaw OTLP diagnostics settings."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from ..config import ConfigError, resolve_otlp_config
from ..otlp import parse_otlp_headers, otlp_headers


def load_openclaw_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} contains invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"unable to read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return payload


def write_openclaw_config(path: Path, payload: dict, *, backup: bool) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)

    backup_path = None
    if backup and path.exists():
        backup_path = path.with_name(f"{path.name}.bak-fleet-otel-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
        try:
            backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"unable to create backup {backup_path}: {exc}") from exc

    updated_payload = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        tmp_path.write_text(updated_payload, encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError as exc:
        raise ConfigError(f"unable to write {path}: {exc}") from exc
    return backup_path


def apply_diagnostics_payload(payload: dict, *, endpoint: str, service_name: str, headers: dict[str, str]) -> dict:
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
    otel["enabled"] = True
    otel["endpoint"] = endpoint
    otel["protocol"] = "http/protobuf"
    otel["serviceName"] = service_name
    otel["logs"] = True
    otel["captureContent"] = True
    otel["headers"] = headers
    return payload


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write durable OpenClaw OTLP diagnostics settings to openclaw.json."
    )
    parser.add_argument("node_label", nargs="?", help="Node label used for Basic auth username")
    parser.add_argument(
        "--config",
        type=Path,
        help="Node-local JSON config file that provides endpoint/network/service defaults.",
    )
    parser.add_argument(
        "--endpoint",
        help="OTLP HTTP endpoint to write into OpenClaw diagnostics config.",
    )
    parser.add_argument("--service-name", help="OTLP service.name value for diagnostics output.")
    parser.add_argument(
        "--network",
        choices=["lan", "off_lan"],
        help="Node network mode. Required for Cloudflare Access checks.",
    )
    parser.add_argument(
        "--openclaw-config",
        type=Path,
        default=None,
        help="Target openclaw.json path. Defaults to openclaw_config_path in --config or ~/.openclaw/openclaw.json",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("FLEET_INGEST_TOKEN"),
        help="Raw per-node ingest token. Prefer FLEET_INGEST_TOKEN; CLI tokens may appear in shell history/process listings.",
    )
    parser.add_argument(
        "--cf-access-client-id",
        default=os.environ.get("CF_ACCESS_CLIENT_ID"),
        help="Cloudflare Access service-token Client ID for off-LAN nodes.",
    )
    parser.add_argument(
        "--cf-access-client-secret",
        default=os.environ.get("CF_ACCESS_CLIENT_SECRET"),
        help="Cloudflare Access service-token Client Secret for off-LAN nodes.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Disable timestamped backup before writing openclaw.json.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = args.token
    if not token:
        print("FLEET_INGEST_TOKEN must be set or passed with --token.", file=sys.stderr)
        return 1

    try:
        resolved = resolve_otlp_config(
            node_label=args.node_label,
            network=args.network,
            endpoint=args.endpoint,
            service_name=args.service_name,
            openclaw_config_path=args.openclaw_config,
            cf_access_client_id=args.cf_access_client_id,
            cf_access_client_secret=args.cf_access_client_secret,
            config_path=args.config,
        )
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    if resolved.node_label is None:
        print("node_label is required", file=sys.stderr)
        return 1

    try:
        encoded_headers = otlp_headers(
            node_label=resolved.node_label,
            token=token,
            network=resolved.network,
            cf_access_client_id=resolved.cf_access_client_id,
            cf_access_client_secret=resolved.cf_access_client_secret,
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    headers = parse_otlp_headers(encoded_headers)
    output_path = resolved.openclaw_config_path
    try:
        payload = apply_diagnostics_payload(load_openclaw_config(output_path), endpoint=resolved.endpoint, service_name=resolved.service_name, headers=headers)
        backup_path = write_openclaw_config(output_path, payload, backup=not args.no_backup)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Updated {output_path}")
    if backup_path is not None:
        print(f"Backup: {backup_path}")
    print("Set diagnostics.enabled=true")
    print("Set diagnostics.otel.enabled=true")
    print("Set diagnostics.otel.protocol=http/protobuf")
    print("Set diagnostics.otel.logs=true")
    print("Set diagnostics.otel.captureContent=true")
    print("Set diagnostics.otel.serviceName=<redacted>")
    print("Set diagnostics.otel.endpoint=<redacted>")
    print("Set diagnostics.otel.headers.Authorization=<redacted>")
    if resolved.network == "off_lan":
        print("Set diagnostics.otel.headers.CF-Access-Client-Id=<redacted>")
        print("Set diagnostics.otel.headers.CF-Access-Client-Secret=<redacted>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
