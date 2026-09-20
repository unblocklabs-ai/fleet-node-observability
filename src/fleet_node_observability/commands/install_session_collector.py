"""Add only the session collector to an already managed macOS node."""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

LABEL = "com.unblocklabs.openclaw-sessions-textfile"


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("Run as root on an already managed node")
    source = Path(__file__).with_name("collect_openclaw_sessions.py")
    template = Path("/Library/LaunchDaemons/com.unblocklabs.openclaw-cron-schedule-textfile.plist")
    config = plistlib.loads(template.read_bytes())
    args = config["ProgramArguments"]
    cron_script = Path(args[1])
    if cron_script.name != "collect_openclaw_cron_schedule.py" or not cron_script.is_file():
        raise SystemExit("Unexpected managed cron collector path")
    runtime = cron_script.parents[3]
    if runtime.name != "fleet-node-observability" or runtime.parent.name != ".openclaw":
        raise SystemExit("Unexpected managed runtime")
    destination = cron_script.with_name(source.name)
    output = Path(args[args.index("--output") + 1]).with_name("openclaw_sessions.prom")
    state = runtime / "state" / "sessions" / "starts.sqlite"
    plist = template.with_name(LABEL + ".plist")
    for target in (runtime, destination, plist, output, state):
        if any(part.is_symlink() for part in (target, *target.parents)):
            raise SystemExit("Refusing symlinked managed path")
    backup = Path(tempfile.mkdtemp(prefix="fleet-sessions-before-", dir="/var/tmp"))
    for target in (destination, plist):
        if target.exists():
            shutil.copy2(target, backup / target.name)
    metadata = cron_script.stat()
    # Source-only additive module installation, not a full runtime/version upgrade.
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)
    os.chown(destination, metadata.st_uid, metadata.st_gid)
    destination.chmod(0o644)
    config["Label"] = LABEL
    config["ProgramArguments"] = [args[0], str(destination), "--node", args[args.index("--node") + 1],
                                  "--state", str(state), "--output", str(output)]
    config["StartInterval"] = 300
    config["RunAtLoad"] = True
    for key in ("StandardOutPath", "StandardErrorPath"):
        config[key] = str(Path(config[key]).with_name("sessions" + (".err.log" if key == "StandardErrorPath" else ".log")))
    # Validate and run as the existing collector account before registering the service.
    environment = os.environ | config.get("EnvironmentVariables", {})
    command = ["sudo", "-u", config["UserName"], "env"]
    command += [f"{k}={v}" for k, v in config.get("EnvironmentVariables", {}).items()]
    subprocess.run(command + config["ProgramArguments"], check=True, timeout=90, env=environment)
    if plist.exists():
        subprocess.run(["launchctl", "bootout", "system", str(plist)], check=False, capture_output=True)
    plist.write_bytes(plistlib.dumps(config))
    plist.chmod(0o644)
    subprocess.run(["plutil", "-lint", str(plist)], check=True)
    subprocess.run(["launchctl", "bootstrap", "system", str(plist)], check=True)
    print(f"Installed {LABEL}; sha256={hashlib.sha256(source.read_bytes()).hexdigest()}; backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
