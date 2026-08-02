"""Install the Collector's full Authorization header from a protected token file."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from ..agent import load_agent_config, render_authorization_header
from ..atomic import write_private_atomic
from ..config import ConfigError

MAX_TOKEN_FILE_BYTES = 16 * 1024
_STABLE_STAT_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Install a fleet node agent credential.")
    result.add_argument("--config", type=Path, required=True)
    result.add_argument("--token-file", type=Path, required=True)
    return result


def read_protected_token(path: Path) -> str:
    descriptor = -1
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ConfigError(f"ingest token file {path} not found") from exc
    except OSError as exc:
        raise ConfigError(f"unable to read ingest token file: {exc}") from exc

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ConfigError("ingest token file must be a regular file, not a symlink")
        if stat.S_IMODE(before.st_mode) & 0o077:
            raise ConfigError(
                "ingest token file must not be readable or writable by group or other"
            )
        if before.st_size > MAX_TOKEN_FILE_BYTES:
            raise ConfigError(
                f"ingest token file exceeds the {MAX_TOKEN_FILE_BYTES}-byte safety limit"
            )

        chunks: list[bytes] = []
        total = 0
        while total <= MAX_TOKEN_FILE_BYTES:
            chunk = os.read(descriptor, min(4096, MAX_TOKEN_FILE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_TOKEN_FILE_BYTES:
            raise ConfigError(
                f"ingest token file exceeds the {MAX_TOKEN_FILE_BYTES}-byte safety limit"
            )

        after = os.fstat(descriptor)
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise ConfigError("ingest token file changed while it was being read") from exc
        if any(
            getattr(before, field) != getattr(after, field)
            or getattr(after, field) != getattr(current, field)
            for field in _STABLE_STAT_FIELDS
        ):
            raise ConfigError("ingest token file changed while it was being read")
    finally:
        os.close(descriptor)

    try:
        contents = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError("ingest token file must contain valid UTF-8") from exc

    token = contents.removesuffix("\n")
    if (
        not token
        or "\r" in contents
        or "\n" in token
        or token != token.strip()
    ):
        raise ConfigError(
            "ingest token file must contain one nonempty token with no surrounding whitespace "
            "and at most one final LF"
        )
    return token


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_agent_config(args.config)
        header = render_authorization_header(config.node_label, read_protected_token(args.token_file))
        config.authorization_header_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_private_atomic(config.authorization_header_path, header)
        os.chmod(config.authorization_header_path.parent, 0o700)
    except (ConfigError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(config.authorization_header_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
