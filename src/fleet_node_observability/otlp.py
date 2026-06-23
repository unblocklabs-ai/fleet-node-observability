"""OpenTelemetry and OpenClaw header helpers."""

from __future__ import annotations

from base64 import b64encode
from urllib.parse import quote, unquote

from .config import normalize_label


def basic_auth_header(username: str, token: str) -> str:
    encoded = b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def encode_otlp_header_value(value: str) -> str:
    return quote(value, safe="")


def parse_otlp_headers(encoded_headers: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not encoded_headers:
        return parsed
    for pair in encoded_headers.split(","):
        if "=" not in pair:
            raise ValueError(f"invalid OTLP header pair: {pair!r}")
        key, value = pair.split("=", 1)
        parsed[key] = unquote(value)
    return parsed


def otlp_headers(
    *,
    node_label: str,
    token: str,
    network: str = "lan",
    cf_access_client_id: str | None = None,
    cf_access_client_secret: str | None = None,
) -> str:
    normalized_network = network.strip().lower().replace("-", "_")
    if normalized_network not in {"lan", "off_lan"}:
        raise ValueError(f"unsupported network '{network}' (expected 'lan' or 'off_lan')")

    if not token:
        raise ValueError("token is required")

    headers: list[tuple[str, str]] = [
        ("Authorization", basic_auth_header(normalize_label(node_label), token)),
    ]

    if normalized_network == "off_lan":
        if not cf_access_client_id or not cf_access_client_secret:
            raise ValueError(
                "CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET are required for off-LAN nodes"
            )
        headers.extend(
            (
                ("CF-Access-Client-Id", cf_access_client_id),
                ("CF-Access-Client-Secret", cf_access_client_secret),
            )
        )
    return ",".join(f"{key}={encode_otlp_header_value(value)}" for key, value in headers)
