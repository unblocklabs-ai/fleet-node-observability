"""Install the Collector's full Authorization header from a protected token file."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from ..agent import load_agent_config, render_authorization_header
from ..config import ConfigError
from .configure_openclaw_otel import _write_secure_atomic


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Install a fleet node agent credential.")
    result.add_argument("--config", type=Path, required=True)
    result.add_argument("--token-file", type=Path, required=True)
    return result


def _read_protected_token(path: Path) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ConfigError(f"ingest token file {path} not found") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ConfigError("ingest token file must be a regular file, not a symlink")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ConfigError("ingest token file must not be readable or writable by group or other")
    try:
        token = path.read_text(encoding="utf-8").rstrip("\n")
    except OSError as exc:
        raise ConfigError(f"unable to read ingest token file: {exc}") from exc
    if "\n" in token or "\r" in token:
        raise ConfigError("ingest token file must contain exactly one token line")
    return token


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_agent_config(args.config)
        header = render_authorization_header(config.node_label, _read_protected_token(args.token_file))
        config.authorization_header_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_secure_atomic(config.authorization_header_path, header)
        os.chmod(config.authorization_header_path.parent, 0o700)
    except (ConfigError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(config.authorization_header_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
