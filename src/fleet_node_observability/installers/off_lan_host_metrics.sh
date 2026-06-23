#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: sudo install-off-lan-host-metrics --config <path> [options]

Installs root LaunchDaemons for off-LAN host metrics parity:
node_exporter, token-gated node_exporter proxy, Codex usage textfile,
OpenClaw gateway readiness textfile, and macOS thermal textfile.
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

find_node_exporter() {
  for candidate in /opt/homebrew/bin/node_exporter /usr/local/bin/node_exporter; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  echo "node_exporter is not installed. Install Homebrew node_exporter as the target user first." >&2
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
  chown -R "$USER_NAME" "$runtime_dir"
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
TOKEN_FILE_CLI=""
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
    --fleet-node-exporter-scrape-token-file)
      TOKEN_FILE_CLI="${2:-}"
      shift 2
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
NODE_EXPORTER_BIN="$(find_node_exporter)"

NODE="${NODE_LABEL_CLI:-$(config_value "$CONFIG_PATH" node_label node)}"
USER_NAME="${NODE_USER_CLI:-$(config_value "$CONFIG_PATH" node_user user)}"
USER_HOME="${NODE_HOME_CLI:-$(config_value "$CONFIG_PATH" home node_home)}"
NODE_EXPORTER_PORT="${NODE_EXPORTER_PORT_CLI:-$(config_value "$CONFIG_PATH" node_exporter_port)}"
TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME_CLI:-$(config_value "$CONFIG_PATH" node_exporter_tunnel_hostname)}"
OPENCLAW_READY_URL="${OPENCLAW_READY_URL_CLI:-$(config_value "$CONFIG_PATH" openclaw_ready_url)}"
CODEX_PROFILE="${CODEX_PROFILE_CLI:-$(config_value "$CONFIG_PATH" codex_profile)}"
CODEX_USAGE_INTERVAL_SECS="${CODEX_USAGE_INTERVAL_CLI:-$(config_value "$CONFIG_PATH" codex_usage_interval_secs)}"
CODEX_USAGE_ENABLED="${CODEX_USAGE_ENABLED_CLI:-$(config_value "$CONFIG_PATH" codex_usage_enabled)}"
TEXTFILE_DIR="${TEXTFILE_DIR_CLI:-$(config_value "$CONFIG_PATH" node_exporter_textfile_dir)}"
TOKEN_FILE="${TOKEN_FILE_CLI:-$(config_value "$CONFIG_PATH" fleet_node_exporter_scrape_token_file)}"

if [[ "${NODE:-}" == "" ]]; then
  echo "node label is required; pass --node-label or set node_label in --config." >&2
  exit 1
fi
if [[ "${USER_NAME:-}" == "" ]]; then
  echo "node user is required; pass --node-user or set node_user in --config." >&2
  exit 1
fi
if [[ "${TUNNEL_HOSTNAME:-}" == "" ]]; then
  echo "node_exporter_tunnel_hostname is required for off-LAN." >&2
  exit 1
fi
if [[ "${NODE_HOME:-}" == "" ]]; then
  USER_HOME="/Users/${USER_NAME}"
else
  USER_HOME="$NODE_HOME"
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
if [[ "${TOKEN_FILE:-}" == "" ]]; then
  TOKEN_FILE="/Library/OpenClaw/fleet-node-exporter-scrape-token"
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "off-LAN host metrics installer supports macOS nodes only." >&2
  exit 1
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "This installer must run as root because it writes /Library/LaunchDaemons." >&2
  exit 1
fi
if ! id "$USER_NAME" >/dev/null 2>&1; then
  echo "Target user $USER_NAME does not exist." >&2
  exit 1
fi
if [[ ! -d "$USER_HOME" ]]; then
  echo "Target home $USER_HOME does not exist." >&2
  exit 1
fi
if ! command -v lsof >/dev/null 2>&1; then
  echo "lsof is required to clear stale node_exporter/proxy listeners before install." >&2
  exit 1
fi

USER_UID="$(id -u "$USER_NAME")"
BREW_PREFIX="$(dirname "$(dirname "$NODE_EXPORTER_BIN")")"
OPENCLAW_DIR="$USER_HOME/.openclaw"
SECRET_DIR="$OPENCLAW_DIR/secrets"
LOG_DIR="$USER_HOME/Library/Logs"
RUNTIME_DIR="$OPENCLAW_DIR/fleet-node-observability"
RUNTIME_BIN_DIR="$RUNTIME_DIR/bin"
RUNTIME_SRC_DIR="$RUNTIME_DIR/src"
LAUNCHD_DIR="/Library/LaunchDaemons"
PATH_VALUE="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
HEADER_NAME="X-Fleet-Scrape-Token"

NODE_EXPORTER_LABEL="com.unblocklabs.node-exporter"
PROXY_LABEL="com.unblocklabs.node-exporter-proxy"
CODEX_LABEL="com.unblocklabs.codex-usage-textfile"
GATEWAY_LABEL="com.unblocklabs.openclaw-gateway-health-textfile"
THERMAL_LABEL="com.unblocklabs.macos-thermal-textfile"

NODE_EXPORTER_PLIST="$LAUNCHD_DIR/$NODE_EXPORTER_LABEL.plist"
PROXY_PLIST="$LAUNCHD_DIR/$PROXY_LABEL.plist"
CODEX_PLIST="$LAUNCHD_DIR/$CODEX_LABEL.plist"
GATEWAY_PLIST="$LAUNCHD_DIR/$GATEWAY_LABEL.plist"
THERMAL_PLIST="$LAUNCHD_DIR/$THERMAL_LABEL.plist"

CODEX_COLLECTOR="$RUNTIME_BIN_DIR/collect-codex-usage"
CODEX_TEXTFILE="$TEXTFILE_DIR/codex_usage.prom"
GATEWAY_HEALTH="$RUNTIME_BIN_DIR/openclaw-gateway-health"
GATEWAY_TEXTFILE="$TEXTFILE_DIR/openclaw_gateway_ready.prom"
THERMAL_COLLECTOR="$RUNTIME_BIN_DIR/collect-macos-thermal"
THERMAL_TEXTFILE="$TEXTFILE_DIR/macos_thermal.prom"
PROXY_SCRIPT="$RUNTIME_BIN_DIR/fleet-node-exporter-proxy"
METRICS_INFO="$TEXTFILE_DIR/fleet_node_exporter_install.prom"

run_as_user() {
  /usr/bin/sudo -H -u "$USER_NAME" env HOME="$USER_HOME" PATH="$PATH_VALUE" "$@"
}

write_plist_header() {
  local path="$1"
  local label="$2"
  cat >"$path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>UserName</key>
  <string>$USER_NAME</string>
EOF
}

finish_plist() {
  local path="$1"
  /bin/chown root:wheel "$path"
  /bin/chmod 0644 "$path"
}

bootout_label() {
  local label="$1"
  local plist="$2"
  launchctl bootout "system/$label" >/dev/null 2>&1 || true
  launchctl bootout system "$plist" >/dev/null 2>&1 || true
  launchctl bootout "gui/$USER_UID/$label" >/dev/null 2>&1 || true
}

kill_tcp_listener() {
  local port="$1"
  local pids
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  echo "[off-lan-host-metrics] stopping stale listener(s) on TCP $port: ${pids//$'\n'/ }"
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    kill "$pid" >/dev/null 2>&1 || true
  done <<<"$pids"
  sleep 1
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    kill -9 "$pid" >/dev/null 2>&1 || true
  done <<<"$pids"
}

disable_user_launchagent_plist() {
  local label="$1"
  local plist="$USER_HOME/Library/LaunchAgents/$label.plist"
  if [[ ! -e "$plist" ]]; then
    return 0
  fi
  local disabled="$plist.disabled-$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$plist" "$disabled"
  chown "$USER_NAME" "$disabled"
  echo "[off-lan-host-metrics] archived old user LaunchAgent: $disabled"
}

mkdir -p "$TEXTFILE_DIR" "$OPENCLAW_DIR" "$SECRET_DIR" "$LOG_DIR"
install_runtime_tree "$RUNTIME_DIR" "$RUNTIME_BIN_DIR" "$RUNTIME_SRC_DIR"
chown -R "$USER_NAME" "$TEXTFILE_DIR" "$OPENCLAW_DIR" "$LOG_DIR" "$RUNTIME_DIR"
chmod 0700 "$SECRET_DIR"

if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "Missing scrape token file: $TOKEN_FILE" >&2
  echo "Create it with the token configured for Cloudflare/Prometheus, then re-run this installer." >&2
  exit 1
fi
chown "$USER_NAME" "$TOKEN_FILE"
chmod 0600 "$TOKEN_FILE"

cat >"$METRICS_INFO" <<EOF
# HELP fleet_node_exporter_textfile_install_info node_exporter textfile collector install metadata.
# TYPE fleet_node_exporter_textfile_install_info gauge
fleet_node_exporter_textfile_install_info{node="$NODE"} 1
EOF
chown "$USER_NAME" "$METRICS_INFO"

echo "[off-lan-host-metrics] unloading previous fleet supervisors"
for item in \
  "$NODE_EXPORTER_LABEL:$NODE_EXPORTER_PLIST" \
  "$PROXY_LABEL:$PROXY_PLIST" \
  "$CODEX_LABEL:$CODEX_PLIST" \
  "$GATEWAY_LABEL:$GATEWAY_PLIST" \
  "$THERMAL_LABEL:$THERMAL_PLIST"
do
  bootout_label "${item%%:*}" "${item#*:}"
  disable_user_launchagent_plist "${item%%:*}"
done
pkill -u "$USER_NAME" -f 'node_exporter.*127[.]0[.]0[.]1:9100' >/dev/null 2>&1 || true
pkill -u "$USER_NAME" -f 'fleet-node-exporter-proxy[.]py' >/dev/null 2>&1 || true
pkill -u "$USER_NAME" -f 'fleet-node-exporter-proxy$' >/dev/null 2>&1 || true
kill_tcp_listener "$NODE_EXPORTER_PORT"
kill_tcp_listener 19100

write_plist_header "$NODE_EXPORTER_PLIST" "$NODE_EXPORTER_LABEL"
cat >>"$NODE_EXPORTER_PLIST" <<EOF
  <key>ProgramArguments</key>
  <array>
    <string>$NODE_EXPORTER_BIN</string>
    <string>--web.listen-address=127.0.0.1:$NODE_EXPORTER_PORT</string>
    <string>--collector.textfile.directory=$TEXTFILE_DIR</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$USER_HOME</string>
    <key>PATH</key>
    <string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/$NODE_EXPORTER_LABEL.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/$NODE_EXPORTER_LABEL.err.log</string>
</dict>
</plist>
EOF
finish_plist "$NODE_EXPORTER_PLIST"

write_plist_header "$PROXY_PLIST" "$PROXY_LABEL"
cat >>"$PROXY_PLIST" <<EOF
  <key>ProgramArguments</key>
  <array>
    <string>$PROXY_SCRIPT</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$USER_HOME</string>
    <key>PATH</key>
    <string>$PATH_VALUE</string>
    <key>FLEET_NODE_EXPORTER_SCRAPE_TOKEN_FILE</key>
    <string>$TOKEN_FILE</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/$PROXY_LABEL.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/$PROXY_LABEL.err.log</string>
</dict>
</plist>
EOF
finish_plist "$PROXY_PLIST"

if [[ "$CODEX_USAGE_ENABLED" == "1" ]]; then
  run_as_user "$CODEX_COLLECTOR" --node "$NODE" --profile "$CODEX_PROFILE" --format prometheus --output "$CODEX_TEXTFILE"
  write_plist_header "$CODEX_PLIST" "$CODEX_LABEL"
  cat >>"$CODEX_PLIST" <<EOF
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
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$USER_HOME</string>
    <key>PATH</key>
    <string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/$CODEX_LABEL.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/$CODEX_LABEL.err.log</string>
</dict>
</plist>
EOF
  finish_plist "$CODEX_PLIST"
else
  rm -f "$CODEX_TEXTFILE" "$CODEX_TEXTFILE".*.tmp "$CODEX_PLIST"
fi

run_as_user "$GATEWAY_HEALTH" prometheus "$OPENCLAW_READY_URL" "$NODE" "$GATEWAY_TEXTFILE"
write_plist_header "$GATEWAY_PLIST" "$GATEWAY_LABEL"
cat >>"$GATEWAY_PLIST" <<EOF
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
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$USER_HOME</string>
    <key>PATH</key>
    <string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/$GATEWAY_LABEL.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/$GATEWAY_LABEL.err.log</string>
</dict>
</plist>
EOF
finish_plist "$GATEWAY_PLIST"

run_as_user "$THERMAL_COLLECTOR" --node "$NODE" --output "$THERMAL_TEXTFILE"
write_plist_header "$THERMAL_PLIST" "$THERMAL_LABEL"
cat >>"$THERMAL_PLIST" <<EOF
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
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$USER_HOME</string>
    <key>PATH</key>
    <string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/$THERMAL_LABEL.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/$THERMAL_LABEL.err.log</string>
</dict>
</plist>
EOF
finish_plist "$THERMAL_PLIST"

echo "[off-lan-host-metrics] bootstrapping LaunchDaemons"
launchctl bootstrap system "$NODE_EXPORTER_PLIST"
launchctl bootstrap system "$PROXY_PLIST"
if [[ "$CODEX_USAGE_ENABLED" == "1" ]]; then
  launchctl bootstrap system "$CODEX_PLIST"
fi
launchctl bootstrap system "$GATEWAY_PLIST"
launchctl bootstrap system "$THERMAL_PLIST"

sleep 2

METRICS_TMP="$(mktemp)"
if ! curl -fsS --max-time 5 -o "$METRICS_TMP" "http://127.0.0.1:$NODE_EXPORTER_PORT/metrics"; then
  rm -f "$METRICS_TMP"
  echo "local node_exporter metrics check failed." >&2
  exit 1
fi
if ! grep -q '^node_cpu_seconds_total' "$METRICS_TMP"; then
  rm -f "$METRICS_TMP"
  echo "local node_exporter metrics check failed." >&2
  exit 1
fi
rm -f "$METRICS_TMP"

if curl -fsS --max-time 5 "http://127.0.0.1:19100/metrics" >/dev/null 2>&1; then
  echo "proxy accepted a request without $HEADER_NAME; refusing install." >&2
  exit 1
fi

TOKEN="$(awk -F= 'NF > 1 { print $2; next } { print $1 }' "$TOKEN_FILE" | head -1 | tr -d '\"'\''[:space:]')"
METRICS_TMP="$(mktemp)"
if ! curl -fsS --max-time 5 -H "X-Fleet-Scrape-Token: $TOKEN" -o "$METRICS_TMP" "http://127.0.0.1:19100/metrics"; then
  rm -f "$METRICS_TMP"
  unset TOKEN
  echo "token-gated proxy metrics check failed." >&2
  exit 1
fi
if ! grep -q '^node_cpu_seconds_total' "$METRICS_TMP"; then
  rm -f "$METRICS_TMP"
  unset TOKEN
  echo "token-gated proxy metrics check failed." >&2
  exit 1
fi
rm -f "$METRICS_TMP"
unset TOKEN

CRON_BACKUP_DIR="$OPENCLAW_DIR/backups"
mkdir -p "$CRON_BACKUP_DIR"
chown "$USER_NAME" "$CRON_BACKUP_DIR"
CURRENT_CRON="$(crontab -u "$USER_NAME" -l 2>/dev/null || true)"
FILTERED_CRON="$(printf '%s\n' "$CURRENT_CRON" | grep -Ev 'fleet-host-metrics-ensure[.]sh|fleet-host-textfiles-refresh[.]sh' || true)"
if [[ "$CURRENT_CRON" != "$FILTERED_CRON" ]]; then
  BACKUP="$CRON_BACKUP_DIR/crontab-before-off-lan-host-metrics-$(date +%Y%m%d%H%M%S)"
  printf '%s\n' "$CURRENT_CRON" >"$BACKUP"
  chown "$USER_NAME" "$BACKUP"
  printf '%s\n' "$FILTERED_CRON" | crontab -u "$USER_NAME" -
  echo "[off-lan-host-metrics] removed temporary fleet host-metrics cron lines; backup: $BACKUP"
fi

cat <<VERIFY
[off-lan-host-metrics] installed for node=$NODE
[off-lan-host-metrics] tunnel hostname: $TUNNEL_HOSTNAME
[off-lan-host-metrics] local node_exporter: http://127.0.0.1:$NODE_EXPORTER_PORT/metrics
[off-lan-host-metrics] token-gated proxy: http://127.0.0.1:19100/metrics
[off-lan-host-metrics] textfile directory: $TEXTFILE_DIR
VERIFY
