#!/usr/bin/env sh
set -eu

mode="${1:-status}"
ready_url="${2:-http://127.0.0.1:18789/readyz}"
node="${3:-unknown}"
output_path="${4:-}"
timeout_secs="${OPENCLAW_GATEWAY_HEALTH_TIMEOUT_SECS:-5}"

usage() {
  cat <<'USAGE'
Usage: openclaw-gateway-health [status|ready-heartbeat|prometheus] [ready_url] [node] [output_path]

Checks the local OpenClaw readiness endpoint. In prometheus mode, emits the
openclaw_gateway_ready textfile metric and writes it atomically when output_path
is provided.
USAGE
}

if [ "$mode" = "-h" ] || [ "$mode" = "--help" ]; then
  usage
  exit 0
fi

case "$mode" in
  status|ready-heartbeat|prometheus)
    ;;
  *)
    printf 'invalid mode: %s\n\n' "$mode" >&2
    usage >&2
    exit 2
    ;;
esac

if command -v curl >/dev/null 2>&1; then
  curl_bin="$(command -v curl)"
else
  curl_bin="/usr/bin/curl"
fi

json_escape() {
  python3 - "$1" <<'PY'
import json
import sys

print(json.dumps(sys.argv[1])[1:-1], end="")
PY
}

emit() {
  message="$1"
  severity="$2"
  issue_type="$3"
  issue_group="$4"
  event_type="$5"
  ready="$6"

  printf '{"message":"%s","severity":"%s","component":"openclaw_gateway_health","issue_type":"%s","issue_group":"%s","event_type":"%s","gateway_ready":%s,"gateway_ready_url":"%s"}\n' \
    "$(json_escape "$message")" \
    "$severity" \
    "$issue_type" \
    "$issue_group" \
    "$event_type" \
    "$ready" \
    "$(json_escape "$ready_url")"
}

escape_label() {
  python3 - "$1" <<'PY'
import sys

value = sys.argv[1]
out = []
for char in value:
    codepoint = ord(char)
    if char == "\\":
        out.append("\\\\")
    elif char == "\n":
        out.append("\\n")
    elif char == "\t":
        out.append("\\t")
    elif char == "\r":
        out.append("\\r")
    elif char == '"':
        out.append('\\"')
    elif codepoint < 0x20 or codepoint == 0x7f:
        continue
    else:
        out.append(char)
print("".join(out), end="")
PY
}

emit_prometheus() {
  ready="$1"
  tmp_path=""
  content="# HELP openclaw_gateway_ready Whether the local OpenClaw gateway readiness endpoint is healthy.
# TYPE openclaw_gateway_ready gauge
openclaw_gateway_ready{node=\"$(escape_label "$node")\",gateway_ready_url=\"$(escape_label "$ready_url")\"} $ready
"
  if [ -n "$output_path" ]; then
    mkdir -p "$(dirname "$output_path")"
    tmp_path="$(mktemp "${output_path}.XXXXXX")"
    printf '%s' "$content" >"$tmp_path"
    mv "$tmp_path" "$output_path"
  else
    printf '%s' "$content"
  fi
}

if "$curl_bin" -fsS --max-time "$timeout_secs" -o /dev/null "$ready_url" >/dev/null 2>&1; then
  if [ "$mode" = "prometheus" ]; then
    emit_prometheus "1"
    exit 0
  fi
  if [ "$mode" = "ready-heartbeat" ]; then
    emit "openclaw gateway ready heartbeat" "info" "none" "none" "gateway_ready_heartbeat" "true"
  else
    emit "openclaw gateway readiness ok" "info" "none" "none" "gateway_ready" "true"
  fi
  exit 0
fi

if [ "$mode" = "prometheus" ]; then
  emit_prometheus "0"
  exit 0
fi

if [ "$mode" = "ready-heartbeat" ]; then
  exit 0
fi

emit "openclaw gateway readiness failed" "error" "gateway_not_ready" "gateway_health" "gateway_unready" "false"
exit 1
