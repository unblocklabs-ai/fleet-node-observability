"""Render and optionally validate a fleet node Collector configuration."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..agent import load_agent_config, render_collector_config
from ..config import ConfigError


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Render the unified fleet node Collector config.")
    result.add_argument("--config", type=Path, required=True, help="Node agent JSON config")
    result.add_argument("--output", type=Path, help="Write rendered Collector JSON here")
    result.add_argument(
        "--collector-binary",
        type=Path,
        help="Run the pinned Collector's config validator after rendering",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_agent_config(args.config)
        rendered = json.dumps(render_collector_config(config), indent=2) + "\n"
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    output = args.output or config.collector_config_path
    temp_path: Path | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.is_symlink() or (output.exists() and not output.is_file()):
            raise OSError("output must be a regular file, not a symlink or directory")
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
        temp_path = Path(temp_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())

        if args.collector_binary is not None:
            result = subprocess.run(
                [str(args.collector_binary), "validate", f"--config=file:{temp_path}"],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                print(result.stderr or result.stdout, file=sys.stderr, end="")
                return result.returncode

        os.replace(temp_path, output)
        temp_path = None
        os.chmod(output, 0o600)
    except OSError as exc:
        print(f"unable to write {output}: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
