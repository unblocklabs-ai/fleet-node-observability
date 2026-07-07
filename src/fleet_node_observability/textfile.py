"""Utilities for writing Prometheus textfile-style metrics."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any


def escape_label_value(value: Any) -> str:
    """Escape a Prometheus label value."""
    text = str(value)
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace('"', '\\"')


def render_labels(labels: dict[str, Any]) -> str:
    if not labels:
        return ""
    sorted_items = ",".join(
        f'{name}="{escape_label_value(value)}"' for name, value in sorted(labels.items())
    )
    return "{" + sorted_items + "}"


def render_gauge_metric(name: str, value: int | float, labels: dict[str, Any] | None = None) -> str:
    label_suffix = render_labels(labels or {})
    return f"{name}{label_suffix} {value}"


def write_textfile_atomic(path: Path, content: str, *, mode: int = 0o644) -> None:
    """Write metric text to *path* using atomic replace semantics."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        # If write fails before rename, do not leave a stray temporary file behind.
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
