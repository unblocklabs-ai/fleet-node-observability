"""Shared validation for node-side configuration."""

from __future__ import annotations

import re

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
