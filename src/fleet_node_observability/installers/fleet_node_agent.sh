#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: sudo install-fleet-node-agent --config <path> --node-user <account> --ingest-token-file <path> [options]

Installs the single fleet node telemetry process on macOS. The ingest token must
come from a mode-0600 file; it is never accepted through argv or environment.

Options:
  --node-user <account>       Required unprivileged account that owns the node runtime.
  --collector-archive <path>  Use an already-downloaded pinned Collector archive.
  -h, --help                  Show this help.
USAGE
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG_PATH=""
TOKEN_FILE=""
NODE_USER=""
COLLECTOR_ARCHIVE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --ingest-token-file)
      TOKEN_FILE="${2:-}"
      shift 2
      ;;
    --node-user)
      NODE_USER="${2:-}"
      shift 2
      ;;
    --collector-archive)
      COLLECTOR_ARCHIVE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$CONFIG_PATH" || -z "$NODE_USER" || -z "$TOKEN_FILE" ]]; then
  echo "--config, --node-user, and --ingest-token-file are required" >&2
  usage >&2
  exit 2
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "This installer must run as root because it writes LaunchDaemons." >&2
  exit 1
fi
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "fleet-node-agent currently supports macOS nodes only." >&2
  exit 1
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "$PYTHON_BIN is required" >&2
  exit 1
fi
PYTHON_BIN="$(command -v "$PYTHON_BIN")"
for command_name in curl dscl shasum tar launchctl sudo; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$command_name is required" >&2
    exit 1
  fi
done

reject_symlink_components() {
  local name="$1"
  local path="$2"
  local current=""
  local component
  if [[ "$path" != /* || "$path" == "/" || "$path" == *"/../"* || "$path" == *"/.." ]]; then
    echo "$name must be a safe absolute path: $path" >&2
    exit 1
  fi
  while IFS= read -r component; do
    [[ -z "$component" ]] && continue
    current="$current/$component"
    if [[ -L "$current" ]]; then
      echo "$name must not contain symlink components: $current" >&2
      exit 1
    fi
  done < <(printf '%s\n' "${path#/}" | tr '/' '\n')
}

if [[ -L "$CONFIG_PATH" || ! -f "$CONFIG_PATH" ]]; then
  echo "--config must be a regular file, not a symlink" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d /tmp/fleet-node-agent-install.XXXXXX)"
chmod 0711 "$TMP_DIR"
trap 'rm -rf "$TMP_DIR"' EXIT
SOURCE_CONFIG="$TMP_DIR/source-node-config.json"
"$PYTHON_BIN" - "$CONFIG_PATH" "$SOURCE_CONFIG" <<'PY'
import os
import stat
import sys

descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)
try:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise SystemExit("--config must be a regular file, not a symlink")
    chunks = []
    total = 0
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > 1024 * 1024:
            raise SystemExit("--config exceeds the 1 MiB safety limit")
        chunks.append(chunk)
    after = os.fstat(descriptor)
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        raise SystemExit("--config changed while it was being read")
finally:
    os.close(descriptor)

output = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
with os.fdopen(output, "wb") as handle:
    handle.write(b"".join(chunks))
    handle.flush()
    os.fsync(handle.fileno())
PY

if ! id "$NODE_USER" >/dev/null 2>&1; then
  echo "Configured node_user does not exist: $NODE_USER" >&2
  exit 1
fi
NODE_UID="$(id -u "$NODE_USER")"
if [[ "$NODE_UID" -eq 0 ]]; then
  echo "node_user must be an unprivileged local account" >&2
  exit 1
fi

TOKEN_SNAPSHOT="$TMP_DIR/ingest-token"
PYTHONPATH="$REPO_DIR/src" "$PYTHON_BIN" - \
  "$TOKEN_FILE" "$TOKEN_SNAPSHOT" "$NODE_UID" <<'PY'
import os
import sys
from pathlib import Path

from fleet_node_observability.commands.write_agent_secret import read_protected_token
from fleet_node_observability.config import ConfigError

try:
    token = read_protected_token(Path(sys.argv[1]))
except ConfigError as exc:
    raise SystemExit(str(exc)) from None
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(sys.argv[2], flags, 0o400)
try:
    os.fchown(descriptor, int(sys.argv[3]), -1)
    os.fchmod(descriptor, 0o400)
    with os.fdopen(descriptor, "wb") as handle:
        descriptor = -1
        handle.write(token.encode())
        handle.flush()
        os.fsync(handle.fileno())
finally:
    if descriptor >= 0:
        os.close(descriptor)
PY

SYSTEM_HOME="$(dscl . -read "/Users/$NODE_USER" NFSHomeDirectory 2>/dev/null | sed -n 's/^NFSHomeDirectory: //p' | head -n 1)"
if [[ -z "$SYSTEM_HOME" || ! -d "$SYSTEM_HOME" ]]; then
  echo "unable to resolve the system home for $NODE_USER" >&2
  exit 1
fi
NODE_HOME="$(cd "$SYSTEM_HOME" && pwd -P)"

case "$(uname -m)" in
  arm64) ARCHITECTURE="arm64"; PLATFORM="darwin_arm64" ;;
  x86_64) ARCHITECTURE="x86_64"; PLATFORM="darwin_amd64" ;;
  *) echo "Unsupported macOS architecture: $(uname -m)" >&2; exit 1 ;;
esac

select_homebrew() {
  local preferred_prefix="/usr/local"
  local alternate_prefix="/opt/homebrew"
  local prefix
  if [[ "$ARCHITECTURE" == "arm64" ]]; then
    preferred_prefix="/opt/homebrew"
    alternate_prefix="/usr/local"
  fi
  for prefix in "$preferred_prefix" "$alternate_prefix"; do
    if [[ -x "$prefix/bin/brew" && -x "$prefix/bin/node_exporter" ]]; then
      printf '%s\n' "$prefix"
      return 0
    fi
  done
  for prefix in "$preferred_prefix" "$alternate_prefix"; do
    if [[ -x "$prefix/bin/brew" ]]; then
      printf '%s\n' "$prefix"
      return 0
    fi
  done
  echo "Homebrew is required at /opt/homebrew or /usr/local" >&2
  return 1
}

HOMEBREW_PREFIX="$(select_homebrew)"
BREW_BIN="$HOMEBREW_PREFIX/bin/brew"
REPORTED_HOMEBREW_PREFIX="$(sudo -u "$NODE_USER" "$BREW_BIN" --prefix)"
if [[ "$REPORTED_HOMEBREW_PREFIX" != "$HOMEBREW_PREFIX" ]]; then
  echo "selected Homebrew reported an unexpected prefix: $REPORTED_HOMEBREW_PREFIX" >&2
  exit 1
fi

RESOLVED_MANIFEST="$TMP_DIR/resolved-node-manifest.json"
PYTHONPATH="$REPO_DIR/src" "$PYTHON_BIN" - \
  "$SOURCE_CONFIG" "$RESOLVED_MANIFEST" "$NODE_USER" "$NODE_HOME" "$ARCHITECTURE" \
  "$HOMEBREW_PREFIX" <<'PY'
import json
import os
import sys
from dataclasses import fields
from pathlib import Path
from fleet_node_observability.agent import load_agent_config

config = load_agent_config(
    sys.argv[1],
    node_user=sys.argv[3],
    node_home=sys.argv[4],
    architecture=sys.argv[5],
    homebrew_prefix=sys.argv[6],
)
payload = {}
for field in fields(config):
    value = getattr(config, field.name)
    payload[field.name] = str(value) if isinstance(value, Path) else value
descriptor = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
chmod 0444 "$RESOLVED_MANIFEST"
chmod 0444 "$SOURCE_CONFIG"
RESOLVED_MANIFEST_SHA256="$(shasum -a 256 "$RESOLVED_MANIFEST" | awk '{print $1}')"

verify_resolved_manifest() {
  local actual_sha256
  actual_sha256="$(shasum -a 256 "$RESOLVED_MANIFEST" | awk '{print $1}')"
  if [[ "$actual_sha256" != "$RESOLVED_MANIFEST_SHA256" ]]; then
    echo "resolved node manifest changed during installation" >&2
    exit 1
  fi
}

manifest_value() {
  local field="$1"
  verify_resolved_manifest
  "$PYTHON_BIN" - "$RESOLVED_MANIFEST" "$field" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)[sys.argv[2]])
PY
}

release_value() {
  local platform="$1"
  local field="$2"
  PYTHONPATH="$REPO_DIR/src" "$PYTHON_BIN" - "$platform" "$field" <<'PY'
import sys
from fleet_node_observability.agent import COLLECTOR_RELEASES

print(COLLECTOR_RELEASES[sys.argv[1]][sys.argv[2]])
PY
}

NODE_LABEL="$(manifest_value node_label)"
NODE_USER="$(manifest_value node_user)"
NODE_HOME="$(manifest_value node_home)"
CODEX_USAGE_ENABLED="$(manifest_value codex_usage_enabled)"
COLLECTOR_CONFIG="$(manifest_value collector_config_path)"
AUTH_HEADER_FILE="$(manifest_value authorization_header_path)"
QUEUE_DIR="$(manifest_value queue_directory)"
COLLECTOR_BIN="$(manifest_value collector_binary_path)"
TEXTFILE_DIR="$(manifest_value node_exporter_textfile_dir)"
HEALTH_ENDPOINT="$(manifest_value health_endpoint)"
COLLECTOR_METRICS_ENDPOINT="$(manifest_value collector_metrics_endpoint)"
NODE_EXPORTER_TARGET="$(manifest_value node_exporter_target)"
LOCAL_OTLP_ENDPOINT="$(manifest_value local_otlp_endpoint)"
TELEMETRY_ENDPOINT="$(manifest_value telemetry_endpoint)"

run_as_node() {
  sudo -u "$NODE_USER" "$@"
}

COLLECTOR_URL="$(release_value "$PLATFORM" url)"
COLLECTOR_SHA256="$(release_value "$PLATFORM" sha256)"

ARCHIVE="$TMP_DIR/otelcol-contrib.tar.gz"
if [[ -n "$COLLECTOR_ARCHIVE" ]]; then
  if [[ ! -f "$COLLECTOR_ARCHIVE" || -L "$COLLECTOR_ARCHIVE" ]]; then
    echo "--collector-archive must be a regular file, not a symlink" >&2
    exit 1
  fi
  cp "$COLLECTOR_ARCHIVE" "$ARCHIVE"
else
  echo "[fleet-node-agent] downloading pinned Collector for $PLATFORM"
  curl --fail --silent --show-error --location "$COLLECTOR_URL" --output "$ARCHIVE"
fi
ACTUAL_SHA256="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$COLLECTOR_SHA256" ]]; then
  echo "Collector SHA256 mismatch: expected $COLLECTOR_SHA256, got $ACTUAL_SHA256" >&2
  exit 1
fi

mkdir -p "$TMP_DIR/extract"
tar -xzf "$ARCHIVE" -C "$TMP_DIR/extract"
EXTRACTED_BIN="$(find "$TMP_DIR/extract" -type f -name otelcol-contrib -perm -111 -print -quit)"
if [[ -z "$EXTRACTED_BIN" ]]; then
  echo "Pinned Collector archive did not contain otelcol-contrib" >&2
  exit 1
fi
STAGED_COLLECTOR_BIN="$TMP_DIR/otelcol-contrib"
STAGED_HEARTBEAT="$TMP_DIR/fleet-node-agent-heartbeat"
install -m 0555 "$EXTRACTED_BIN" "$STAGED_COLLECTOR_BIN"
install -m 0555 \
  "$REPO_DIR/src/fleet_node_observability/collectors/fleet_node_agent_heartbeat.sh" \
  "$STAGED_HEARTBEAT"

RUNTIME_DIR="$NODE_HOME/.openclaw/fleet-node-observability"
RUNTIME_BIN="$RUNTIME_DIR/bin"
RUNTIME_PYTHON="$RUNTIME_DIR/python"
RUNTIME_LOGS="$RUNTIME_DIR/logs"
RUNTIME_STATE="$RUNTIME_DIR/state"
for managed_path in \
  "$RUNTIME_DIR" "$COLLECTOR_CONFIG" "$AUTH_HEADER_FILE" "$QUEUE_DIR" \
  "$COLLECTOR_BIN" "$TEXTFILE_DIR" "$RUNTIME_STATE" "$RUNTIME_PYTHON"; do
  reject_symlink_components "managed node path" "$managed_path"
done
run_as_node mkdir -p "$RUNTIME_BIN" "$RUNTIME_PYTHON/fleet_node_observability/commands" \
  "$RUNTIME_LOGS" "$RUNTIME_STATE" \
  "$(dirname "$COLLECTOR_CONFIG")" "$(dirname "$AUTH_HEADER_FILE")" "$QUEUE_DIR" "$TEXTFILE_DIR"
run_as_node chmod 0700 "$RUNTIME_DIR" "$RUNTIME_BIN" "$RUNTIME_PYTHON" \
  "$RUNTIME_PYTHON/fleet_node_observability" \
  "$RUNTIME_PYTHON/fleet_node_observability/commands" "$RUNTIME_LOGS" "$RUNTIME_STATE" \
  "$(dirname "$COLLECTOR_CONFIG")" "$(dirname "$AUTH_HEADER_FILE")" "$QUEUE_DIR"
if ! run_as_node test -w "$TEXTFILE_DIR"; then
  echo "node_exporter textfile directory is not writable by $NODE_USER: $TEXTFILE_DIR" >&2
  exit 1
fi
run_as_node install -m 0755 "$STAGED_COLLECTOR_BIN" "$COLLECTOR_BIN"
run_as_node install -m 0755 "$STAGED_HEARTBEAT" "$RUNTIME_BIN/fleet-node-agent-heartbeat"
run_as_node install -m 0755 \
  "$REPO_DIR/src/fleet_node_observability/collectors/openclaw_gateway_health.sh" \
  "$RUNTIME_BIN/openclaw-gateway-health"
for runtime_source in __init__.py agent.py atomic.py config.py openclaw.py textfile.py; do
  run_as_node install -m 0644 \
    "$REPO_DIR/src/fleet_node_observability/$runtime_source" \
    "$RUNTIME_PYTHON/fleet_node_observability/$runtime_source"
done
run_as_node install -m 0644 \
  "$REPO_DIR/src/fleet_node_observability/commands/__init__.py" \
  "$RUNTIME_PYTHON/fleet_node_observability/commands/__init__.py"
for runtime_command in \
  collect_codex_usage.py \
  collect_macos_thermal.py \
  configure_openclaw_local_otel.py; do
  run_as_node install -m 0644 \
    "$REPO_DIR/src/fleet_node_observability/commands/$runtime_command" \
    "$RUNTIME_PYTHON/fleet_node_observability/commands/$runtime_command"
done

verify_resolved_manifest
run_as_node env PYTHONPATH="$REPO_DIR/src" \
  "$PYTHON_BIN" -m fleet_node_observability.commands.write_agent_secret \
  --config "$SOURCE_CONFIG" --token-file "$TOKEN_SNAPSHOT" >/dev/null

verify_resolved_manifest
run_as_node env PYTHONPATH="$REPO_DIR/src" \
  "$PYTHON_BIN" -m fleet_node_observability.commands.render_agent_config \
  --config "$SOURCE_CONFIG" --output "$COLLECTOR_CONFIG" \
  --collector-binary "$COLLECTOR_BIN" >/dev/null

escape_xml() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

COLLECTOR_LABEL="com.unblocklabs.fleet-node-agent"
COLLECTOR_PLIST="/Library/LaunchDaemons/$COLLECTOR_LABEL.plist"
HEARTBEAT_LABEL="com.unblocklabs.fleet-node-agent-heartbeat"
HEARTBEAT_PLIST="/Library/LaunchDaemons/$HEARTBEAT_LABEL.plist"
NODE_EXPORTER_LABEL="com.unblocklabs.node-exporter"
SYSTEM_NODE_EXPORTER_PLIST="/Library/LaunchDaemons/$NODE_EXPORTER_LABEL.plist"
GATEWAY_LABEL="com.unblocklabs.openclaw-gateway-health-textfile"
GATEWAY_PLIST="/Library/LaunchDaemons/$GATEWAY_LABEL.plist"
THERMAL_LABEL="com.unblocklabs.macos-thermal-textfile"
THERMAL_PLIST="/Library/LaunchDaemons/$THERMAL_LABEL.plist"
CODEX_LABEL="com.unblocklabs.codex-usage-textfile"
CODEX_PLIST="/Library/LaunchDaemons/$CODEX_LABEL.plist"

find_node_exporter() {
  local candidate="$HOMEBREW_PREFIX/bin/node_exporter"
  [[ -x "$candidate" ]] || return 1
  printf '%s\n' "$candidate"
}

ensure_node_exporter_installed() {
  if find_node_exporter >/dev/null; then
    return
  fi
  sudo -u "$NODE_USER" "$BREW_BIN" install node_exporter
  if ! find_node_exporter >/dev/null; then
    echo "Homebrew did not install node_exporter under $HOMEBREW_PREFIX" >&2
    exit 1
  fi
}

install_loopback_node_exporter() {
  ensure_node_exporter_installed
  local node_exporter_bin
  node_exporter_bin="$(find_node_exporter)"

  cat >"$TMP_DIR/node-exporter.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$NODE_EXPORTER_LABEL</string>
  <!-- fleet-node-agent-managed -->
  <key>UserName</key><string>$(escape_xml "$NODE_USER")</string>
  <key>ProgramArguments</key><array>
    <string>$(escape_xml "$node_exporter_bin")</string>
    <string>--web.listen-address=127.0.0.1:9100</string>
    <string>--no-collector.thermal</string>
    <string>--collector.textfile.directory=$(escape_xml "$TEXTFILE_DIR")</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$(escape_xml "$RUNTIME_LOGS/node-exporter.log")</string>
  <key>StandardErrorPath</key><string>$(escape_xml "$RUNTIME_LOGS/node-exporter.err.log")</string>
</dict></plist>
PLIST
  launchctl bootout system "$SYSTEM_NODE_EXPORTER_PLIST" >/dev/null 2>&1 || true
  install -m 0644 "$TMP_DIR/node-exporter.plist" "$SYSTEM_NODE_EXPORTER_PLIST"
  launchctl bootstrap system "$SYSTEM_NODE_EXPORTER_PLIST"
  launchctl kickstart -k "system/$NODE_EXPORTER_LABEL"
}

install_loopback_node_exporter

cat >"$TMP_DIR/collector.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$COLLECTOR_LABEL</string>
  <key>UserName</key><string>$(escape_xml "$NODE_USER")</string>
  <key>ProgramArguments</key><array>
    <string>$(escape_xml "$COLLECTOR_BIN")</string>
    <string>--config=file:$(escape_xml "$COLLECTOR_CONFIG")</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$(escape_xml "$RUNTIME_LOGS/collector.log")</string>
  <key>StandardErrorPath</key><string>$(escape_xml "$RUNTIME_LOGS/collector.err.log")</string>
</dict></plist>
PLIST
install -o root -g wheel -m 0644 "$TMP_DIR/collector.plist" "$COLLECTOR_PLIST"

cat >"$TMP_DIR/heartbeat.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$HEARTBEAT_LABEL</string>
  <key>UserName</key><string>$(escape_xml "$NODE_USER")</string>
  <key>ProgramArguments</key><array>
    <string>$(escape_xml "$RUNTIME_BIN/fleet-node-agent-heartbeat")</string>
    <string>$(escape_xml "$TEXTFILE_DIR")</string>
    <string>$(escape_xml "$NODE_LABEL")</string>
    <string>$(escape_xml "$COLLECTOR_METRICS_ENDPOINT")</string>
    <string>$(escape_xml "$RUNTIME_STATE")</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$(escape_xml "$RUNTIME_LOGS/heartbeat.log")</string>
  <key>StandardErrorPath</key><string>$(escape_xml "$RUNTIME_LOGS/heartbeat.err.log")</string>
</dict></plist>
PLIST
install -o root -g wheel -m 0644 "$TMP_DIR/heartbeat.plist" "$HEARTBEAT_PLIST"

GATEWAY_SCRIPT="$RUNTIME_BIN/openclaw-gateway-health"
GATEWAY_TEXTFILE="$TEXTFILE_DIR/openclaw_gateway_ready.prom"
cat >"$TMP_DIR/gateway.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$GATEWAY_LABEL</string>
  <key>UserName</key><string>$(escape_xml "$NODE_USER")</string>
  <key>ProgramArguments</key><array>
    <string>$(escape_xml "$GATEWAY_SCRIPT")</string>
    <string>prometheus</string>
    <string>http://127.0.0.1:18789/readyz</string>
    <string>$(escape_xml "$NODE_LABEL")</string>
    <string>$(escape_xml "$GATEWAY_TEXTFILE")</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$(escape_xml "$RUNTIME_LOGS/gateway-health.log")</string>
  <key>StandardErrorPath</key><string>$(escape_xml "$RUNTIME_LOGS/gateway-health.err.log")</string>
</dict></plist>
PLIST
install -o root -g wheel -m 0644 "$TMP_DIR/gateway.plist" "$GATEWAY_PLIST"

PYTHONPATH_VALUE="$RUNTIME_PYTHON"
PATH_VALUE="$NODE_HOME/.npm-global/bin:$NODE_HOME/.local/bin:$NODE_HOME/.local/share/fnm/aliases/default/bin:$HOMEBREW_PREFIX/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
THERMAL_SCRIPT="$RUNTIME_PYTHON/fleet_node_observability/commands/collect_macos_thermal.py"
THERMAL_TEXTFILE="$TEXTFILE_DIR/macos_thermal.prom"
cat >"$TMP_DIR/thermal.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$THERMAL_LABEL</string>
  <key>UserName</key><string>$(escape_xml "$NODE_USER")</string>
  <key>ProgramArguments</key><array>
    <string>$(escape_xml "$PYTHON_BIN")</string>
    <string>$(escape_xml "$THERMAL_SCRIPT")</string>
    <string>--node</string><string>$(escape_xml "$NODE_LABEL")</string>
    <string>--output</string><string>$(escape_xml "$THERMAL_TEXTFILE")</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>$(escape_xml "$NODE_HOME")</string>
    <key>PATH</key><string>$(escape_xml "$PATH_VALUE")</string>
    <key>PYTHONPATH</key><string>$(escape_xml "$PYTHONPATH_VALUE")</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$(escape_xml "$RUNTIME_LOGS/thermal.log")</string>
  <key>StandardErrorPath</key><string>$(escape_xml "$RUNTIME_LOGS/thermal.err.log")</string>
</dict></plist>
PLIST
install -o root -g wheel -m 0644 "$TMP_DIR/thermal.plist" "$THERMAL_PLIST"

CODEX_SCRIPT="$RUNTIME_PYTHON/fleet_node_observability/commands/collect_codex_usage.py"
CODEX_TEXTFILE="$TEXTFILE_DIR/codex_usage.prom"
if [[ "$CODEX_USAGE_ENABLED" == "True" ]]; then
  cat >"$TMP_DIR/codex.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$CODEX_LABEL</string>
  <key>UserName</key><string>$(escape_xml "$NODE_USER")</string>
  <key>ProgramArguments</key><array>
    <string>$(escape_xml "$PYTHON_BIN")</string>
    <string>$(escape_xml "$CODEX_SCRIPT")</string>
    <string>--node</string><string>$(escape_xml "$NODE_LABEL")</string>
    <string>--format</string><string>prometheus</string>
    <string>--output</string><string>$(escape_xml "$CODEX_TEXTFILE")</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>$(escape_xml "$NODE_HOME")</string>
    <key>PATH</key><string>$(escape_xml "$PATH_VALUE")</string>
    <key>PYTHONPATH</key><string>$(escape_xml "$PYTHONPATH_VALUE")</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>$(escape_xml "$RUNTIME_LOGS/codex-usage.log")</string>
  <key>StandardErrorPath</key><string>$(escape_xml "$RUNTIME_LOGS/codex-usage.err.log")</string>
</dict></plist>
PLIST
  install -o root -g wheel -m 0644 "$TMP_DIR/codex.plist" "$CODEX_PLIST"
else
  launchctl bootout system "$CODEX_PLIST" >/dev/null 2>&1 || true
  rm -f "$CODEX_PLIST"
  run_as_node rm -f "$CODEX_TEXTFILE" "$CODEX_TEXTFILE".*.tmp
fi

restart_launchdaemon() {
  local label="$1"
  local plist="$2"
  launchctl bootout system "$plist" >/dev/null 2>&1 || true
  launchctl bootstrap system "$plist"
  launchctl kickstart -k "system/$label"
}

restart_launchdaemon "$COLLECTOR_LABEL" "$COLLECTOR_PLIST"
restart_launchdaemon "$HEARTBEAT_LABEL" "$HEARTBEAT_PLIST"
restart_launchdaemon "$GATEWAY_LABEL" "$GATEWAY_PLIST"
restart_launchdaemon "$THERMAL_LABEL" "$THERMAL_PLIST"
if [[ "$CODEX_USAGE_ENABLED" == "True" ]]; then
  restart_launchdaemon "$CODEX_LABEL" "$CODEX_PLIST"
fi

HEALTH_URL="http://$HEALTH_ENDPOINT/"
READY=0
for _ in {1..20}; do
  if curl --fail --silent --max-time 2 "$HEALTH_URL" >/dev/null; then
    READY=1
    break
  fi
  sleep 1
done
if [[ "$READY" -ne 1 ]]; then
  echo "fleet-node-agent did not become healthy at $HEALTH_URL" >&2
  run_as_node tail -n 40 "$RUNTIME_LOGS/collector.err.log" >&2 || true
  exit 1
fi

verify_node_exporter_metrics() {
  local metrics_url="$1"
  local metrics_file="$TMP_DIR/node-exporter.metrics"
  install -m 0600 /dev/null "$metrics_file"
  if ! curl --fail --silent --show-error --max-time 5 \
    --output "$metrics_file" "$metrics_url"; then
    echo "node_exporter loopback scrape failed" >&2
    return 1
  fi
  if ! grep -q '^node_cpu_seconds_total' "$metrics_file"; then
    echo "node_exporter loopback scrape did not include node_cpu_seconds_total" >&2
    return 1
  fi
  if ! grep -q '^fleet_node_agent_heartbeat_timestamp_seconds' "$metrics_file"; then
    echo "fresh node agent heartbeat was not visible through node_exporter" >&2
    return 1
  fi
}

verify_node_exporter_metrics "http://$NODE_EXPORTER_TARGET/metrics"

verify_resolved_manifest
run_as_node env PYTHONPATH="$RUNTIME_PYTHON" \
  "$PYTHON_BIN" -m fleet_node_observability.commands.configure_openclaw_local_otel \
  --config "$SOURCE_CONFIG"

echo "[fleet-node-agent] installed node=$NODE_LABEL"
echo "[fleet-node-agent] OpenClaw OTLP: http://$LOCAL_OTLP_ENDPOINT"
echo "[fleet-node-agent] central OTLP: $TELEMETRY_ENDPOINT"
echo "[fleet-node-agent] health: $HEALTH_URL"
echo "[fleet-node-agent] restart OpenClaw after reviewing the resulting config and any timestamped backup"
