"""Shared Python package for node-side observability helpers."""

from .config import (
    ConfigError,
    OpenClawOtlpConfig,
    normalize_label,
    resolve_otlp_config,
)
from .otlp import basic_auth_header, encode_otlp_header_value, parse_otlp_headers, otlp_headers

__all__ = [
    "ConfigError",
    "OpenClawOtlpConfig",
    "normalize_label",
    "resolve_otlp_config",
    "basic_auth_header",
    "encode_otlp_header_value",
    "parse_otlp_headers",
    "otlp_headers",
]
