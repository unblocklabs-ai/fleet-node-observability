"""Print OpenClaw OTLP env settings for a node."""

from __future__ import annotations

import argparse
import os
import sys

from ..config import ConfigError, resolve_otlp_config
from ..otlp import otlp_headers


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print OpenClaw OTLP settings for a node.")
    parser.add_argument("node_label", nargs="?", help="Node label used for Basic auth username")
    parser.add_argument(
        "--config",
        type=str,
        help="Node-local JSON config file that provides endpoint/network/service defaults.",
    )
    parser.add_argument(
        "--endpoint",
        help="OTLP HTTP endpoint to print.",
    )
    parser.add_argument("--service-name", help="OTLP service.name value to print.")
    parser.add_argument(
        "--network",
        choices=["lan", "off_lan"],
        help="Node network mode. Required for Cloudflare Access checks.",
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
            cf_access_client_id=args.cf_access_client_id,
            cf_access_client_secret=args.cf_access_client_secret,
            config_path=args.config,
        )
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        headers = otlp_headers(
            node_label=resolved.node_label,
            token=token,
            network=resolved.network,
            cf_access_client_id=resolved.cf_access_client_id,
            cf_access_client_secret=resolved.cf_access_client_secret,
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    print("Configure OpenClaw with:")
    print(f"  OTEL_EXPORTER_OTLP_ENDPOINT={resolved.endpoint}")
    print("  OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf")
    print(f'  OTEL_EXPORTER_OTLP_HEADERS="{headers}"')
    print(f'  OTEL_RESOURCE_ATTRIBUTES="service.name={resolved.service_name}"')
    print("  diagnostics.otel.enabled=true")
    print("  diagnostics.otel.logs=true")
    print("  diagnostics.otel.captureContent=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
