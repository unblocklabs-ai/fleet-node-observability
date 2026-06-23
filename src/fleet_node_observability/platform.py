"""Small platform helpers shared across node command modules."""

from __future__ import annotations

import platform
import sys


def is_macos() -> bool:
    """Return True when running on Darwin/macOS."""
    return sys.platform == "darwin" or platform.system().lower() == "darwin"


def require_macos() -> None:
    """Raise a clear error when a helper is used outside its supported platform."""
    if not is_macos():
        raise RuntimeError("This command is currently designed for macOS hosts.")
