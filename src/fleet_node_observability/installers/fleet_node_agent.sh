#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: sudo install-fleet-node-agent --config <path> --ingest-token-file <path> [options]

Installs the single fleet node telemetry process on macOS. The ingest token must
come from a mode-0600 file; it is never accepted through argv or environment.

Options:
  --collector-archive <path>  Use an already-downloaded pinned Collector archive.
  --retire-legacy-pull        Push mode only: retire the old scrape proxy/exposed exporter.
  --skip-openclaw-config      Do not rewrite OpenClaw diagnostics to loopback OTLP.
  -h, --help                  Show this help.
USAGE
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG_PATH=""
TOKEN_FILE=""
COLLECTOR_ARCHIVE=""
RETIRE_LEGACY_PULL=0
SKIP_OPENCLAW_CONFIG=0

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
    --collector-archive)
      COLLECTOR_ARCHIVE="${2:-}"
      shift 2
      ;;
    --retire-legacy-pull)
      RETIRE_LEGACY_PULL=1
      shift
      ;;
    --skip-openclaw-config)
      SKIP_OPENCLAW_CONFIG=1
      shift
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

if [[ -z "$CONFIG_PATH" || -z "$TOKEN_FILE" ]]; then
  echo "--config and --ingest-token-file are required" >&2
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
for command_name in curl shasum tar launchctl sudo; do
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
FROZEN_CONFIG="$TMP_DIR/node-config.json"
PYTHONPATH="$REPO_DIR/src" "$PYTHON_BIN" - "$CONFIG_PATH" "$FROZEN_CONFIG" <<'PY'
import json
import os
import sys
from dataclasses import fields
from fleet_node_observability.agent import load_agent_config

config = load_agent_config(sys.argv[1])
payload = {field.name: str(getattr(config, field.name)) for field in fields(config)}
descriptor = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
chmod 0444 "$FROZEN_CONFIG"
FROZEN_CONFIG_SHA256="$(shasum -a 256 "$FROZEN_CONFIG" | awk '{print $1}')"

verify_frozen_config() {
  local actual_sha256
  actual_sha256="$(shasum -a 256 "$FROZEN_CONFIG" | awk '{print $1}')"
  if [[ "$actual_sha256" != "$FROZEN_CONFIG_SHA256" ]]; then
    echo "normalized node config changed during installation" >&2
    exit 1
  fi
}

frozen_value() {
  local field="$1"
  verify_frozen_config
  "$PYTHON_BIN" - "$FROZEN_CONFIG" "$field" <<'PY'
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

NODE_LABEL="$(frozen_value node_label)"
NODE_USER="$(frozen_value node_user)"
NODE_HOME="$(frozen_value node_home)"
TELEMETRY_MODE="$(frozen_value telemetry_mode)"
COLLECTOR_CONFIG="$(frozen_value collector_config_path)"
AUTH_HEADER_FILE="$(frozen_value authorization_header_path)"
QUEUE_DIR="$(frozen_value queue_directory)"
COLLECTOR_BIN="$(frozen_value collector_binary_path)"
TEXTFILE_DIR="$(frozen_value node_exporter_textfile_dir)"
HEALTH_ENDPOINT="$(frozen_value health_endpoint)"
COLLECTOR_METRICS_ENDPOINT="$(frozen_value collector_metrics_endpoint)"
NODE_EXPORTER_TARGET="$(frozen_value node_exporter_target)"
LOCAL_OTLP_ENDPOINT="$(frozen_value local_otlp_endpoint)"
TELEMETRY_ENDPOINT="$(frozen_value telemetry_endpoint)"

if ! id "$NODE_USER" >/dev/null 2>&1; then
  echo "Configured node_user does not exist: $NODE_USER" >&2
  exit 1
fi
NODE_UID="$(id -u "$NODE_USER")"
if [[ "$NODE_UID" -eq 0 ]]; then
  echo "node_user must be an unprivileged local account" >&2
  exit 1
fi
SYSTEM_HOME="$(dscl . -read "/Users/$NODE_USER" NFSHomeDirectory 2>/dev/null | sed -n 's/^NFSHomeDirectory: //p' | head -n 1)"
if [[ -z "$SYSTEM_HOME" || "$(cd "$SYSTEM_HOME" && pwd -P)" != "$NODE_HOME" ]]; then
  echo "node_home must match the system home for $NODE_USER: ${SYSTEM_HOME:-unresolved}" >&2
  exit 1
fi
if [[ "$RETIRE_LEGACY_PULL" -eq 1 && "$TELEMETRY_MODE" != "push" ]]; then
  echo "--retire-legacy-pull is allowed only when telemetry_mode is push" >&2
  exit 1
fi

run_as_node() {
  sudo -u "$NODE_USER" "$@"
}

case "$(uname -m)" in
  arm64) PLATFORM="darwin_arm64" ;;
  x86_64) PLATFORM="darwin_amd64" ;;
  *) echo "Unsupported macOS architecture: $(uname -m)" >&2; exit 1 ;;
esac
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
RUNTIME_LOGS="$RUNTIME_DIR/logs"
RUNTIME_STATE="$RUNTIME_DIR/state"
for managed_path in \
  "$RUNTIME_DIR" "$COLLECTOR_CONFIG" "$AUTH_HEADER_FILE" "$QUEUE_DIR" \
  "$COLLECTOR_BIN" "$TEXTFILE_DIR" "$RUNTIME_STATE"; do
  reject_symlink_components "managed node path" "$managed_path"
done
run_as_node mkdir -p "$RUNTIME_BIN" "$RUNTIME_LOGS" "$RUNTIME_STATE" \
  "$(dirname "$COLLECTOR_CONFIG")" "$(dirname "$AUTH_HEADER_FILE")" "$QUEUE_DIR" "$TEXTFILE_DIR"
run_as_node chmod 0700 "$RUNTIME_DIR" "$RUNTIME_BIN" "$RUNTIME_LOGS" "$RUNTIME_STATE" \
  "$(dirname "$COLLECTOR_CONFIG")" "$(dirname "$AUTH_HEADER_FILE")" "$QUEUE_DIR"
if ! run_as_node test -w "$TEXTFILE_DIR"; then
  echo "node_exporter textfile directory is not writable by $NODE_USER: $TEXTFILE_DIR" >&2
  exit 1
fi
run_as_node install -m 0755 "$STAGED_COLLECTOR_BIN" "$COLLECTOR_BIN"
run_as_node install -m 0755 "$STAGED_HEARTBEAT" "$RUNTIME_BIN/fleet-node-agent-heartbeat"

verify_frozen_config
run_as_node env PYTHONPATH="$REPO_DIR/src" \
  "$PYTHON_BIN" -m fleet_node_observability.commands.write_agent_secret \
  --config "$FROZEN_CONFIG" --token-file "$TOKEN_FILE" >/dev/null

verify_frozen_config
run_as_node env PYTHONPATH="$REPO_DIR/src" \
  "$PYTHON_BIN" -m fleet_node_observability.commands.render_agent_config \
  --config "$FROZEN_CONFIG" --output "$COLLECTOR_CONFIG" \
  --collector-binary "$COLLECTOR_BIN" >/dev/null

escape_xml() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

COLLECTOR_LABEL="com.unblocklabs.fleet-node-agent"
COLLECTOR_PLIST="/Library/LaunchDaemons/$COLLECTOR_LABEL.plist"
HEARTBEAT_LABEL="com.unblocklabs.fleet-node-agent-heartbeat"
HEARTBEAT_PLIST="/Library/LaunchDaemons/$HEARTBEAT_LABEL.plist"

find_node_exporter() {
  for candidate in /opt/homebrew/bin/node_exporter /usr/local/bin/node_exporter; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

ensure_node_exporter_installed() {
  if find_node_exporter >/dev/null; then
    return
  fi
  local brew_bin=""
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x "$candidate" ]]; then
      brew_bin="$candidate"
      break
    fi
  done
  if [[ -z "$brew_bin" ]]; then
    echo "Homebrew is required to install node_exporter" >&2
    exit 1
  fi
  sudo -u "$NODE_USER" "$brew_bin" install node_exporter
}

install_loopback_node_exporter() {
  ensure_node_exporter_installed
  local node_exporter_bin
  local node_exporter_label="com.unblocklabs.node-exporter"
  local node_exporter_plist="/Library/LaunchDaemons/$node_exporter_label.plist"
  local user_id
  local legacy_user_plist="$NODE_HOME/Library/LaunchAgents/$node_exporter_label.plist"
  node_exporter_bin="$(find_node_exporter)"
  user_id="$NODE_UID"

  cat >"$TMP_DIR/node-exporter.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$node_exporter_label</string>
  <key>UserName</key><string>$(escape_xml "$NODE_USER")</string>
  <key>ProgramArguments</key><array>
    <string>$(escape_xml "$node_exporter_bin")</string>
    <string>--web.listen-address=127.0.0.1:9100</string>
    <string>--collector.textfile.directory=$(escape_xml "$TEXTFILE_DIR")</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$(escape_xml "$RUNTIME_LOGS/node-exporter.log")</string>
  <key>StandardErrorPath</key><string>$(escape_xml "$RUNTIME_LOGS/node-exporter.err.log")</string>
</dict></plist>
PLIST

  launchctl bootout "gui/$user_id" "$legacy_user_plist" >/dev/null 2>&1 || true
  launchctl bootout system "$node_exporter_plist" >/dev/null 2>&1 || true
  install -o root -g wheel -m 0644 "$TMP_DIR/node-exporter.plist" "$node_exporter_plist"
  launchctl bootstrap system "$node_exporter_plist"
  launchctl kickstart -k "system/$node_exporter_label"
  if [[ -f "$legacy_user_plist" ]]; then
    run_as_node mv "$legacy_user_plist" \
      "$legacy_user_plist.disabled-fleet-push-$(date -u +%Y%m%dT%H%M%SZ)"
  fi
}

if [[ "$TELEMETRY_MODE" == "push" ]]; then
  install_loopback_node_exporter
fi

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

if [[ "$TELEMETRY_MODE" == "dual" || "$TELEMETRY_MODE" == "push" ]]; then
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
fi

launchctl bootout system "$COLLECTOR_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap system "$COLLECTOR_PLIST"
launchctl kickstart -k "system/$COLLECTOR_LABEL"
if [[ -f "$HEARTBEAT_PLIST" ]]; then
  launchctl bootout system "$HEARTBEAT_PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap system "$HEARTBEAT_PLIST"
  launchctl kickstart -k "system/$HEARTBEAT_LABEL"
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
    echo "node_exporter loopback scrape failed; keeping legacy transport unchanged" >&2
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

if [[ "$TELEMETRY_MODE" == "dual" || "$TELEMETRY_MODE" == "push" ]]; then
  verify_node_exporter_metrics "http://$NODE_EXPORTER_TARGET/metrics"
fi

if [[ "$SKIP_OPENCLAW_CONFIG" -eq 0 ]]; then
  verify_frozen_config
  run_as_node env PYTHONPATH="$REPO_DIR/src" \
    "$PYTHON_BIN" -m fleet_node_observability.commands.configure_openclaw_local_otel \
    --config "$FROZEN_CONFIG"
fi

retire_plist() {
  local domain="$1"
  local plist="$2"
  if [[ ! -f "$plist" ]]; then
    return
  fi
  launchctl bootout "$domain" "$plist" >/dev/null 2>&1 || true
  mv "$plist" "$plist.disabled-fleet-push-$(date -u +%Y%m%dT%H%M%SZ)"
}

if [[ "$RETIRE_LEGACY_PULL" -eq 1 ]]; then
  retire_plist system "/Library/LaunchDaemons/com.unblocklabs.node-exporter-proxy.plist"
  echo "[fleet-node-agent] legacy pull services retired; per-node Cloudflare tunnel cleanup remains an operator cutover step"
fi

echo "[fleet-node-agent] installed node=$NODE_LABEL mode=$TELEMETRY_MODE"
echo "[fleet-node-agent] OpenClaw OTLP: http://$LOCAL_OTLP_ENDPOINT"
echo "[fleet-node-agent] central OTLP: $TELEMETRY_ENDPOINT"
echo "[fleet-node-agent] health: $HEALTH_URL"
echo "[fleet-node-agent] restart OpenClaw after reviewing its timestamped config backup"
