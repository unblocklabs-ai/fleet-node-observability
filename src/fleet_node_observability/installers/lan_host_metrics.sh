#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install-lan-host-metrics --config <path> [options]

Install host-metrics collectors on a macOS LAN node. The script configures:
- Homebrew node_exporter
- user LaunchAgent for node_exporter
- textfile collectors for Codex usage, OpenClaw readiness, and macOS thermal pressure

The script is intended for local invocation by the node user (or with
--force-user when overriding identity checks during rollout).
USAGE
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

find_python() {
  for candidate in /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3 python3 /usr/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  echo "Python 3 interpreter not found" >&2
  return 1
}

find_brew() {
  for candidate in brew /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  echo "Homebrew is required to install node_exporter, but brew was not found." >&2
  return 1
}

config_value() {
  local config_path="$1"
  shift
  if [[ -z "$config_path" ]]; then
    return 0
  fi
  "$PYTHON_BIN" - "$config_path" "$@" <<'PY'
import json
import sys

path = sys.argv[1]
keys = sys.argv[2:]

try:
    payload = json.loads(open(path, encoding="utf-8").read())
except FileNotFoundError:
    raise SystemExit(f"node config file not found: {path}")
except ValueError as exc:
    raise SystemExit(f"invalid node config JSON: {exc}")

if not isinstance(payload, dict):
    raise SystemExit("node config must contain a JSON object")

for key in keys:
    value = payload.get(key, "")
    if value in (None, ""):
        continue
    if isinstance(value, bool):
        value = "1" if value else "0"
    else:
        value = str(value)
    print(value)
    break
PY
}

install_runtime_tree() {
  local runtime_dir="$1"
  local runtime_bin_dir="$2"
  local runtime_src_dir="$3"

  rm -rf "$runtime_dir"
  mkdir -p "$runtime_dir" "$runtime_bin_dir" "$runtime_src_dir"
  (cd "$REPO_DIR" && cp -R bin/. "$runtime_bin_dir/")
  (cd "$REPO_DIR" && cp -R src/. "$runtime_src_dir/")
  if [[ "$(id -u)" -eq 0 ]]; then
    chown -R "$NODE_USER" "$runtime_dir"
  fi
}

NODE_LABEL_CLI=""
NODE_USER_CLI=""
NODE_HOME_CLI=""
NODE_EXPORTER_PORT_CLI=""
TUNNEL_HOSTNAME_CLI=""
OPENCLAW_READY_URL_CLI=""
CODEX_PROFILE_CLI=""
CODEX_USAGE_INTERVAL_CLI=""
CODEX_USAGE_ENABLED_CLI=""
TEXTFILE_DIR_CLI=""
FORCE_USER=0
CONFIG_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      if [[ $# -lt 2 ]]; then
        echo "--config requires a value" >&2
        usage >&2
        exit 1
      fi
      CONFIG_PATH="$2"
      shift 2
      ;;
    --node-label)
      NODE_LABEL_CLI="${2:-}"
      shift 2
      ;;
    --node-user)
      NODE_USER_CLI="${2:-}"
      shift 2
      ;;
    --node-home)
      NODE_HOME_CLI="${2:-}"
      shift 2
      ;;
    --node-exporter-port)
      NODE_EXPORTER_PORT_CLI="${2:-}"
      shift 2
      ;;
    --node-exporter-tunnel-hostname)
      TUNNEL_HOSTNAME_CLI="${2:-}"
      shift 2
      ;;
    --openclaw-ready-url)
      OPENCLAW_READY_URL_CLI="${2:-}"
      shift 2
      ;;
    --codex-profile)
      CODEX_PROFILE_CLI="${2:-}"
      shift 2
      ;;
    --codex-usage-interval-secs)
      CODEX_USAGE_INTERVAL_CLI="${2:-}"
      shift 2
      ;;
    --codex-usage-enabled)
      CODEX_USAGE_ENABLED_CLI="${2:-}"
      shift 2
      ;;
    --node-exporter-textfile-dir)
      TEXTFILE_DIR_CLI="${2:-}"
      shift 2
      ;;
    --force-user)
      FORCE_USER=1
      shift 1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

PYTHON_BIN="$(find_python)"
BREW_BIN="$(find_brew)"

NODE="${NODE_LABEL_CLI:-$(config_value "$CONFIG_PATH" node_label node)}"
NODE_USER="${NODE_USER_CLI:-$(config_value "$CONFIG_PATH" node_user user)}"
NODE_HOME="${NODE_HOME_CLI:-$(config_value "$CONFIG_PATH" home node_home)}"
NODE_EXPORTER_PORT="${NODE_EXPORTER_PORT_CLI:-$(config_value "$CONFIG_PATH" node_exporter_port)}"
NODE_EXPORTER_TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME_CLI:-$(config_value "$CONFIG_PATH" node_exporter_tunnel_hostname)}"
OPENCLAW_READY_URL="${OPENCLAW_READY_URL_CLI:-$(config_value "$CONFIG_PATH" openclaw_ready_url)}"
CODEX_PROFILE="${CODEX_PROFILE_CLI:-$(config_value "$CONFIG_PATH" codex_profile)}"
CODEX_USAGE_INTERVAL_SECS="${CODEX_USAGE_INTERVAL_CLI:-$(config_value "$CONFIG_PATH" codex_usage_interval_secs)}"
CODEX_USAGE_ENABLED="${CODEX_USAGE_ENABLED_CLI:-$(config_value "$CONFIG_PATH" codex_usage_enabled)}"
TEXTFILE_DIR="${TEXTFILE_DIR_CLI:-$(config_value "$CONFIG_PATH" node_exporter_textfile_dir)}"

NODE="${NODE:-}"
if [[ -z "$NODE" ]]; then
  echo "node label is required; pass --node-label or set node_label in --config." >&2
  exit 1
fi
if [[ -z "$NODE_USER" ]]; then
  echo "node user is required; pass --node-user or set node_user in --config." >&2
  exit 1
fi
if [[ -z "$NODE_HOME" ]]; then
  NODE_HOME="/Users/${NODE_USER}"
fi

if [[ "${NODE_EXPORTER_PORT:-}" == "" ]]; then
  NODE_EXPORTER_PORT="9100"
fi
if [[ "${CODEX_PROFILE:-}" == "" ]]; then
  CODEX_PROFILE="default"
fi
if [[ "${CODEX_USAGE_INTERVAL_SECS:-}" == "" ]]; then
  CODEX_USAGE_INTERVAL_SECS="300"
fi
if [[ "${CODEX_USAGE_ENABLED:-}" == "" ]]; then
  CODEX_USAGE_ENABLED="1"
fi
if [[ "${OPENCLAW_READY_URL:-}" == "" ]]; then
  OPENCLAW_READY_URL="http://127.0.0.1:18789/readyz"
fi
if [[ "${TEXTFILE_DIR:-}" == "" ]]; then
  TEXTFILE_DIR="/opt/homebrew/var/lib/node_exporter/textfile_collector"
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "node_exporter installer currently supports macOS nodes only." >&2
  exit 1
fi

if [[ "$(whoami)" != "$NODE_USER" && "$FORCE_USER" -ne 1 ]]; then
  echo "Refusing to install node_exporter for $NODE as user $(whoami); expected $NODE_USER." >&2
  echo "Run as $NODE_USER, or pass --force-user intentionally." >&2
  exit 1
fi

if ! "$BREW_BIN" list --formula node_exporter >/dev/null 2>&1; then
  echo "[node_exporter] installing Homebrew formula node_exporter"
  "$BREW_BIN" install node_exporter
else
  echo "[node_exporter] Homebrew formula already installed"
fi

BREW_PREFIX="$("$BREW_BIN" --prefix)"
NODE_EXPORTER_BIN="$BREW_PREFIX/bin/node_exporter"

NODE_EXPORTER_LISTEN_ADDRESS=":$NODE_EXPORTER_PORT"
if [[ -n "$NODE_EXPORTER_TUNNEL_HOSTNAME" ]]; then
  NODE_EXPORTER_TARGET="$NODE_EXPORTER_TUNNEL_HOSTNAME"
  NODE_EXPORTER_SCHEME="https"
else
  NODE_EXPORTER_TARGET="127.0.0.1:$NODE_EXPORTER_PORT"
  NODE_EXPORTER_SCHEME="http"
fi

NODE_EXPORTER_LABEL="com.unblocklabs.node-exporter"
NODE_EXPORTER_PLIST="$HOME/Library/LaunchAgents/$NODE_EXPORTER_LABEL.plist"
RUNTIME_DIR="$NODE_HOME/.openclaw/fleet-node-observability"
RUNTIME_BIN_DIR="$RUNTIME_DIR/bin"
RUNTIME_SRC_DIR="$RUNTIME_DIR/src"
CODEX_COLLECTOR="$RUNTIME_BIN_DIR/collect-codex-usage"
CODEX_TEXTFILE="$TEXTFILE_DIR/codex_usage.prom"
CODEX_LABEL="com.unblocklabs.codex-usage-textfile"
CODEX_PLIST="$HOME/Library/LaunchAgents/$CODEX_LABEL.plist"
GATEWAY_HEALTH="$RUNTIME_BIN_DIR/openclaw-gateway-health"
GATEWAY_TEXTFILE="$TEXTFILE_DIR/openclaw_gateway_ready.prom"
GATEWAY_LABEL="com.unblocklabs.openclaw-gateway-health-textfile"
GATEWAY_PLIST="$HOME/Library/LaunchAgents/$GATEWAY_LABEL.plist"
THERMAL_COLLECTOR="$RUNTIME_BIN_DIR/collect-macos-thermal"
THERMAL_TEXTFILE="$TEXTFILE_DIR/macos_thermal.prom"
THERMAL_LABEL="com.unblocklabs.macos-thermal-textfile"
THERMAL_PLIST="$HOME/Library/LaunchAgents/$THERMAL_LABEL.plist"
METRICS_INFO="$TEXTFILE_DIR/fleet_node_exporter_install.prom"

mkdir -p "$TEXTFILE_DIR" "$HOME/Library/LaunchAgents"
install_runtime_tree "$RUNTIME_DIR" "$RUNTIME_BIN_DIR" "$RUNTIME_SRC_DIR"

cat >"$METRICS_INFO" <<EOF
# HELP fleet_node_exporter_textfile_install_info node_exporter textfile collector install metadata.
# TYPE fleet_node_exporter_textfile_install_info gauge
fleet_node_exporter_textfile_install_info{node="$NODE"} 1
EOF

if "$BREW_BIN" services list 2>/dev/null | awk '$1 == "node_exporter" && $2 == "started" { found = 1 } END { exit found ? 0 : 1 }'; then
  echo "[node_exporter] stopping Homebrew service so the managed LaunchAgent can set textfile flags"
  "$BREW_BIN" services stop node_exporter >/dev/null
fi

cat >"$NODE_EXPORTER_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$NODE_EXPORTER_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$NODE_EXPORTER_BIN</string>
    <string>--web.listen-address=$NODE_EXPORTER_LISTEN_ADDRESS</string>
    <string>--collector.textfile.directory=$TEXTFILE_DIR</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/$NODE_EXPORTER_LABEL.out.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/$NODE_EXPORTER_LABEL.err.log</string>
</dict>
</plist>
EOF

echo "[node_exporter] loading managed LaunchAgent $NODE_EXPORTER_LABEL"
launchctl bootout "gui/$(id -u)" "$NODE_EXPORTER_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$NODE_EXPORTER_PLIST"
launchctl kickstart -k "gui/$(id -u)/$NODE_EXPORTER_LABEL"

if [[ "$CODEX_USAGE_ENABLED" == "1" ]]; then
  "$CODEX_COLLECTOR" \
    --node "$NODE" \
    --profile "$CODEX_PROFILE" \
    --format prometheus \
    --output "$CODEX_TEXTFILE"

  cat >"$CODEX_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$CODEX_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$CODEX_COLLECTOR</string>
    <string>--node</string>
    <string>$NODE</string>
    <string>--profile</string>
    <string>$CODEX_PROFILE</string>
    <string>--format</string>
    <string>prometheus</string>
    <string>--output</string>
    <string>$CODEX_TEXTFILE</string>
  </array>
  <key>StartInterval</key>
  <integer>$CODEX_USAGE_INTERVAL_SECS</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/$CODEX_LABEL.out.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/$CODEX_LABEL.err.log</string>
</dict>
</plist>
EOF
  echo "[node_exporter] loading Codex usage textfile LaunchAgent $CODEX_LABEL"
  launchctl bootout "gui/$(id -u)" "$CODEX_PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$CODEX_PLIST"
  launchctl kickstart -k "gui/$(id -u)/$CODEX_LABEL"
else
  echo "[node_exporter] Codex usage textfile collector disabled for node=$NODE"
  launchctl bootout "gui/$(id -u)" "$CODEX_PLIST" >/dev/null 2>&1 || true
  rm -f "$CODEX_TEXTFILE" "$CODEX_TEXTFILE".*.tmp
fi

"$GATEWAY_HEALTH" prometheus "$OPENCLAW_READY_URL" "$NODE" "$GATEWAY_TEXTFILE"
cat >"$GATEWAY_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$GATEWAY_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$GATEWAY_HEALTH</string>
    <string>prometheus</string>
    <string>$OPENCLAW_READY_URL</string>
    <string>$NODE</string>
    <string>$GATEWAY_TEXTFILE</string>
  </array>
  <key>StartInterval</key>
  <integer>60</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/$GATEWAY_LABEL.out.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/$GATEWAY_LABEL.err.log</string>
</dict>
</plist>
EOF
echo "[node_exporter] loading OpenClaw gateway health textfile LaunchAgent $GATEWAY_LABEL"
launchctl bootout "gui/$(id -u)" "$GATEWAY_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$GATEWAY_PLIST"
launchctl kickstart -k "gui/$(id -u)/$GATEWAY_LABEL"

"$THERMAL_COLLECTOR" --node "$NODE" --output "$THERMAL_TEXTFILE"
cat >"$THERMAL_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$THERMAL_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$THERMAL_COLLECTOR</string>
    <string>--node</string>
    <string>$NODE</string>
    <string>--output</string>
    <string>$THERMAL_TEXTFILE</string>
  </array>
  <key>StartInterval</key>
  <integer>60</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/$THERMAL_LABEL.out.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/$THERMAL_LABEL.err.log</string>
</dict>
</plist>
EOF
echo "[node_exporter] loading macOS thermal textfile LaunchAgent $THERMAL_LABEL"
launchctl bootout "gui/$(id -u)" "$THERMAL_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$THERMAL_PLIST"
launchctl kickstart -k "gui/$(id -u)/$THERMAL_LABEL"

sleep 2

if curl -fsS --max-time 5 "http://127.0.0.1:$NODE_EXPORTER_PORT/metrics" | grep -q '^node_cpu_seconds_total'; then
  echo "[node_exporter] local metrics OK: http://127.0.0.1:$NODE_EXPORTER_PORT/metrics"
else
  echo "[node_exporter] local metrics check failed at http://127.0.0.1:$NODE_EXPORTER_PORT/metrics" >&2
  exit 1
fi

cat <<VERIFY
[node_exporter] installed for node=$NODE
[node_exporter] Prometheus target: $NODE_EXPORTER_TARGET
[node_exporter] Prometheus scheme: $NODE_EXPORTER_SCHEME
[node_exporter] textfile directory: $TEXTFILE_DIR
VERIFY
