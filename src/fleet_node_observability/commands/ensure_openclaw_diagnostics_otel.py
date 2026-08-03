"""Ensure OpenClaw has its official OTLP diagnostics producer installed."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from typing import Any

from ..config import ConfigError

PLUGIN_ID = "diagnostics-otel"
PLUGIN_PACKAGE = "@openclaw/diagnostics-otel"
VERSION_PATTERN = re.compile(r"\b(\d{4}\.\d+\.\d+(?:-[A-Za-z0-9.]+)?)\b")


def matching_plugin_version(version_output: str) -> str:
    match = VERSION_PATTERN.search(version_output)
    if match is None:
        raise ConfigError("unable to determine the installed OpenClaw version")
    # OpenClaw packaging revisions such as 2026.7.1-2 share the 2026.7.1
    # extension release. Named prereleases such as -beta.5 remain intact.
    return re.sub(r"-\d+$", "", match.group(1))


def diagnostics_plugin(plugins_payload: dict[str, Any]) -> dict[str, Any] | None:
    plugins = plugins_payload.get("plugins")
    if not isinstance(plugins, list):
        raise ConfigError("OpenClaw plugin inventory did not contain a plugins list")
    matches = [item for item in plugins if isinstance(item, dict) and item.get("id") == PLUGIN_ID]
    if len(matches) > 1:
        raise ConfigError("OpenClaw reported duplicate diagnostics-otel plugins")
    return matches[0] if matches else None


def run_openclaw(openclaw: str, *args: str, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            [openclaw, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigError(f"OpenClaw {' '.join(args[:2])} timed out") from exc
    except OSError as exc:
        raise ConfigError(f"unable to run OpenClaw: {exc}") from exc
    if result.returncode != 0:
        raise ConfigError(
            f"OpenClaw {' '.join(args[:2])} failed with exit code {result.returncode}"
        )
    return result.stdout


def plugin_inventory(openclaw: str) -> dict[str, Any]:
    try:
        payload = json.loads(run_openclaw(openclaw, "plugins", "list", "--json"))
    except json.JSONDecodeError as exc:
        raise ConfigError("OpenClaw plugin inventory was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ConfigError("OpenClaw plugin inventory must be a JSON object")
    return payload


def main() -> int:
    try:
        openclaw = shutil.which("openclaw")
        if openclaw is None:
            raise ConfigError("openclaw executable not found on the managed node PATH")

        plugin = diagnostics_plugin(plugin_inventory(openclaw))
        if plugin is None:
            version = matching_plugin_version(run_openclaw(openclaw, "--version"))
            run_openclaw(
                openclaw,
                "plugins",
                "install",
                f"{PLUGIN_PACKAGE}@{version}",
                "--pin",
                timeout=300,
            )
            plugin = diagnostics_plugin(plugin_inventory(openclaw))
            if plugin is None:
                raise ConfigError("diagnostics-otel was absent after installation")

        if plugin.get("enabled") is not True:
            run_openclaw(openclaw, "plugins", "enable", PLUGIN_ID)
            plugin = diagnostics_plugin(plugin_inventory(openclaw))

        if plugin is None or plugin.get("enabled") is not True or plugin.get("status") != "loaded":
            raise ConfigError("diagnostics-otel is not enabled and loadable")

        print(
            "OpenClaw diagnostics-otel ready"
            + (f" version={plugin['version']}" if plugin.get("version") else "")
        )
        return 0
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
