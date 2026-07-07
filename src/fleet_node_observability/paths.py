"""Standardized filesystem paths for node tools."""

from __future__ import annotations

from pathlib import Path


DEFAULT_OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"
DEFAULT_NODE_EXPORTER_SCRAPE_TOKEN_FILE = Path("/Library/OpenClaw/fleet-node-exporter-scrape-token")


def expand_user_path(path: str | Path) -> Path:
    """Resolve user-relative path values from node configuration."""
    return Path(path).expanduser()
