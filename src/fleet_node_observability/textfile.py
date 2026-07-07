"""Utilities for writing Prometheus textfile-style metrics."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any


def escape_label_value(value: Any) -> str:
    """Escape a Prometheus label value and strip unsupported controls."""
    text = str(value)
    out: list[str] = []
    for char in text:
        codepoint = ord(char)
        if char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif char == "\r":
            out.append("\\r")
        elif char == '"':
            out.append('\\"')
        elif codepoint < 0x20 or codepoint == 0x7F:
            continue
        else:
            out.append(char)
    return "".join(out)


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
