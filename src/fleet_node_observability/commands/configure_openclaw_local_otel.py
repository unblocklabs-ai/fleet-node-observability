"""Point OpenClaw at the node-local Collector without central credentials."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..agent import load_agent_config, render_openclaw_local_settings
from ..config import ConfigError
from ..openclaw import (
    apply_loopback_diagnostics,
    load_openclaw_config,
    write_openclaw_config,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Configure OpenClaw to export OTLP only to the loopback fleet node agent."
    )
    result.add_argument("--config", type=Path, required=True, help="Node agent JSON config")
    result.add_argument("--no-backup", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_agent_config(args.config)
        settings = render_openclaw_local_settings(config)
        loaded = load_openclaw_config(config.openclaw_config_path)
        payload = apply_loopback_diagnostics(
            loaded.payload,
            endpoint=settings["endpoint"],
        )
        backup = write_openclaw_config(
            config.openclaw_config_path,
            payload,
            expected=loaded.revision,
            backup=not args.no_backup,
        )
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Updated {config.openclaw_config_path} for loopback OTLP")
    if backup is not None:
        print(f"Backup: {backup}")
    print("No central credential was written to OpenClaw configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
