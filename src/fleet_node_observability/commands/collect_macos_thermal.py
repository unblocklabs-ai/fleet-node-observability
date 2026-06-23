#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path


LEVELS = {
    "nominal": 0,
    "normal": 0,
    "fair": 1,
    "moderate": 1,
    "serious": 2,
    "heavy": 2,
    "critical": 3,
    "trapping": 3,
}


def escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def labels(**kwargs: str) -> str:
    return "{" + ",".join(f'{key}="{escape_label(value)}"' for key, value in kwargs.items()) + "}"


def parse_thermal_level(text: str) -> tuple[int | None, str]:
    normalized = text.strip().lower()
    if "no thermal warning level has been recorded" in normalized:
        return 0, "nominal"
    for name, level in LEVELS.items():
        if re.search(rf"\b{name}\b", normalized):
            return level, name
    match = re.search(r"thermal\s+pressure\s*:\s*([A-Za-z]+)", text, flags=re.IGNORECASE)
    if match:
        raw = match.group(1).lower()
        return LEVELS.get(raw), raw
    return None, "unknown"


def read_pmset() -> tuple[int | None, str, str | None]:
    try:
        result = subprocess.run(
            ["pmset", "-g", "therm"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "unknown", str(exc)
    if result.returncode != 0:
        return (
            None,
            "unknown",
            (result.stderr or result.stdout).strip() or f"pmset exited {result.returncode}",
        )
    level, name = parse_thermal_level(result.stdout)
    return level, name, None if level is not None else "pmset output did not include a recognized thermal pressure level"


def render(node: str, source: str, now: int | None = None) -> str:
    timestamp = int(time.time()) if now is None else now
    level, pressure, error = read_pmset()
    available = 1 if level is not None else 0
    success = 1 if error is None else 0
    common = labels(node=node, node_label=node, source=source)
    pressure_labels = labels(node=node, node_label=node, source=source, pressure=pressure)
    lines = [
        "# HELP fleet_macos_thermal_pressure_available Whether macOS thermal pressure was collected from this node.",
        "# TYPE fleet_macos_thermal_pressure_available gauge",
        f"fleet_macos_thermal_pressure_available{common} {available}",
        "# HELP fleet_macos_thermal_pressure_level macOS thermal pressure level: 0 nominal, 1 moderate, 2 serious, 3 critical.",
        "# TYPE fleet_macos_thermal_pressure_level gauge",
        f"fleet_macos_thermal_pressure_level{pressure_labels} {level if level is not None else 0}",
        "# HELP fleet_macos_thermal_collector_success Whether the latest macOS thermal collection succeeded.",
        "# TYPE fleet_macos_thermal_collector_success gauge",
        f"fleet_macos_thermal_collector_success{common} {success}",
        "# HELP fleet_macos_thermal_collected_at_seconds Unix timestamp of latest macOS thermal collection.",
        "# TYPE fleet_macos_thermal_collected_at_seconds gauge",
        f"fleet_macos_thermal_collected_at_seconds{common} {timestamp}",
    ]
    if error:
        lines.extend(
            [
                "# HELP fleet_macos_thermal_collection_error_info Latest macOS thermal collection error.",
                "# TYPE fleet_macos_thermal_collection_error_info gauge",
                f"fleet_macos_thermal_collection_error_info{labels(node=node, node_label=node, source=source, error=error[:160])} 1",
            ]
        )
    return "\n".join(lines) + "\n"


def write_textfile_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit macOS thermal pressure as node_exporter textfile metrics.")
    parser.add_argument("--node", default=socket.gethostname().split(".", 1)[0].lower())
    parser.add_argument("--source", default="pmset")
    parser.add_argument("--output", type=Path, help="Write Prometheus textfile output to this path.")
    args = parser.parse_args()

    content = render(args.node, args.source)
    if args.output:
        write_textfile_atomic(args.output.expanduser(), content)
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
