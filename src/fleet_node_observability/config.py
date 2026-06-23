"""Configuration helpers for node-side OTLP generation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import DEFAULT_OPENCLAW_CONFIG_PATH, expand_user_path

NODE_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ConfigError(ValueError):
    """Configuration error with a user-facing message."""


def normalize_label(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    if not normalized:
        raise ConfigError("node label normalizes to empty value")
    if normalized[0].isdigit():
        normalized = f"node_{normalized}"
    if not NODE_LABEL_RE.match(normalized):
        raise ConfigError(f"invalid node label '{name}'")
    return normalized


def bool_setting(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


@dataclass(frozen=True)
class OpenClawOtlpConfig:
    node_label: str
    network: str
    endpoint: str
    service_name: str
    openclaw_config_path: Path
    cf_access_client_id: str | None
    cf_access_client_secret: str | None


def _normalize_network(value: str | None) -> str:
    network = (value or "lan").strip().lower().replace("-", "_")
    if network not in {"lan", "off_lan"}:
        raise ConfigError(f"unsupported network '{value}' (expected 'lan' or 'off_lan')")
    return network


def _load_node_config(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"node config {path} not found") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"node config {path} must contain a JSON object")
    return payload


def _value_from_config(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def resolve_otlp_config(
    *,
    node_label: str | None = None,
    network: str | None = None,
    endpoint: str | None = None,
    service_name: str | None = None,
    openclaw_config_path: str | Path | None = None,
    cf_access_client_id: str | None = None,
    cf_access_client_secret: str | None = None,
    config_path: Path | str | None = None,
) -> OpenClawOtlpConfig:
    payload: dict[str, Any] = {}
    if config_path is not None:
        payload = _load_node_config(Path(config_path))

    resolved_node_label = _value_from_config(payload, "node_label", "node")
    if node_label is not None:
        resolved_node_label = node_label
    if not resolved_node_label:
        raise ConfigError("node_label is required")

    resolved_network = _normalize_network(
        network if network is not None else _value_from_config(payload, "network") or "lan"
    )

    resolved_endpoint = endpoint
    if resolved_endpoint is None:
        resolved_endpoint = _value_from_config(payload, "otlp_http_endpoint", "openclaw_endpoint")
    if resolved_endpoint is None:
        raise ConfigError("OTLP endpoint is required")

    resolved_service_name = service_name or _value_from_config(payload, "openclaw_service_name") or "openclaw_gateway"
    resolved_openclaw_config = expand_user_path(
        openclaw_config_path or _value_from_config(payload, "openclaw_config_path") or DEFAULT_OPENCLAW_CONFIG_PATH
    )

    resolved_cf_id = cf_access_client_id
    if resolved_cf_id is None:
        resolved_cf_id = _value_from_config(payload, "cf_access_client_id", "cf_access_client", "cf_client_id")
        if resolved_cf_id is None:
            cloudflare_headers = _value_from_config(payload, "cloudflare_access_headers")
            if isinstance(cloudflare_headers, dict):
                resolved_cf_id = cloudflare_headers.get("CF-Access-Client-Id")

    resolved_cf_secret = cf_access_client_secret
    if resolved_cf_secret is None:
        resolved_cf_secret = _value_from_config(
            payload,
            "cf_access_client_secret",
            "cf_access_secret",
            "cf_client_secret",
        )
        if resolved_cf_secret is None:
            cloudflare_headers = _value_from_config(payload, "cloudflare_access_headers")
            if isinstance(cloudflare_headers, dict):
                resolved_cf_secret = cloudflare_headers.get("CF-Access-Client-Secret")

    if (
        resolved_network == "off_lan"
        and (not resolved_cf_id or not resolved_cf_secret)
    ):
        raise ConfigError("CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET are required for off-LAN")

    return OpenClawOtlpConfig(
        node_label=normalize_label(str(resolved_node_label)),
        network=resolved_network,
        endpoint=str(resolved_endpoint),
        service_name=str(resolved_service_name),
        openclaw_config_path=resolved_openclaw_config,
        cf_access_client_id=resolved_cf_id,
        cf_access_client_secret=resolved_cf_secret,
    )
