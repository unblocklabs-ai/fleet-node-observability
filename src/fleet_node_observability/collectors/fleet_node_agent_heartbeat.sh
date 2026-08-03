#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: fleet-node-agent-heartbeat <textfile-dir> <node-label> <collector-metrics-endpoint> <state-dir>" >&2
  exit 2
fi

TEXTFILE_DIR="$1"
NODE_LABEL="$2"
COLLECTOR_METRICS_ENDPOINT="$3"
STATE_DIR="$4"

if [[ "$TEXTFILE_DIR" != /* || "$TEXTFILE_DIR" == "/" || "$TEXTFILE_DIR" == *"/../"* || "$TEXTFILE_DIR" == *"/.." ]]; then
  echo "textfile directory must be a safe absolute path" >&2
  exit 1
fi
if [[ ! "$NODE_LABEL" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "node label must use normalized fleet label syntax" >&2
  exit 1
fi
if [[ ! -d "$TEXTFILE_DIR" || -L "$TEXTFILE_DIR" ]]; then
  echo "textfile directory must exist and must not be a symlink: $TEXTFILE_DIR" >&2
  exit 1
fi
if [[ ! "$COLLECTOR_METRICS_ENDPOINT" =~ ^(127\.0\.0\.1|localhost):[0-9]+$ ]]; then
  echo "collector metrics endpoint must use loopback host:port syntax" >&2
  exit 1
fi
if [[ "$STATE_DIR" != /* || "$STATE_DIR" == "/" || "$STATE_DIR" == *"/../"* || "$STATE_DIR" == *"/.." || -L "$STATE_DIR" ]]; then
  echo "state directory must be a safe absolute path and not a symlink" >&2
  exit 1
fi
mkdir -p "$STATE_DIR"
chmod 0700 "$STATE_DIR"

TARGET="$TEXTFILE_DIR/fleet_node_agent_heartbeat.prom"
TMP="$(mktemp "$TEXTFILE_DIR/.fleet_node_agent_heartbeat.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

TIMESTAMP="$(date +%s)"
METRICS=""
METRICS_AVAILABLE=0
if ! METRICS="$(curl --fail --silent --show-error --max-time 2 "http://$COLLECTOR_METRICS_ENDPOINT/metrics" 2>/dev/null)"; then
  METRICS=""
fi

QUEUE_LOGS=0
QUEUE_TRACES=0
QUEUE_OPENCLAW_METRICS=0
QUEUE_AGENT_METRICS=0
QUEUE_HOST_METRICS=0
QUEUE_HEARTBEAT=0
SEEN_LOGS=0
SEEN_TRACES=0
SEEN_OPENCLAW_METRICS=0
SEEN_AGENT_METRICS=0
SEEN_HOST_METRICS=0
SEEN_HEARTBEAT=0
if [[ -n "$METRICS" ]]; then
  while IFS=$'\t' read -r signal queued; do
    case "$signal" in
      logs) QUEUE_LOGS="$queued"; SEEN_LOGS=1 ;;
      traces) QUEUE_TRACES="$queued"; SEEN_TRACES=1 ;;
      openclaw_metrics) QUEUE_OPENCLAW_METRICS="$queued"; SEEN_OPENCLAW_METRICS=1 ;;
      agent_metrics) QUEUE_AGENT_METRICS="$queued"; SEEN_AGENT_METRICS=1 ;;
      host_metrics) QUEUE_HOST_METRICS="$queued"; SEEN_HOST_METRICS=1 ;;
      heartbeat) QUEUE_HEARTBEAT="$queued"; SEEN_HEARTBEAT=1 ;;
    esac
  done < <(
    printf '%s\n' "$METRICS" | awk '
      function valid_queue_size(value, number) {
        if (value !~ /^([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$/) return 0
        number = value + 0
        return number >= 0 && number == int(number)
      }
      function emit_if_usable(exporter, signal) {
        if (valid[exporter] && !invalid[exporter]) {
          printf "%s\t%.0f\n", signal, total[exporter]
        }
      }
      /^otelcol_exporter_queue_size\{/ {
        exporter = ""
        if (index($0, "exporter=\"otlp_http/logs\"")) exporter = "logs"
        else if (index($0, "exporter=\"otlp_http/traces\"")) exporter = "traces"
        else if (index($0, "exporter=\"otlp_http/app_metrics\"")) exporter = "app_metrics"
        else if (index($0, "exporter=\"otlp_http/agent\"")) exporter = "agent"
        else if (index($0, "exporter=\"otlp_http/host\"")) exporter = "host"
        else if (index($0, "exporter=\"otlp_http/heartbeat\"")) exporter = "heartbeat"
        if (exporter == "") next
        value = $NF
        if (!valid_queue_size(value)) {
          invalid[exporter] = 1
          next
        }
        valid[exporter] = 1
        total[exporter] += value + 0
      }
      END {
        emit_if_usable("logs", "logs")
        emit_if_usable("traces", "traces")
        emit_if_usable("app_metrics", "openclaw_metrics")
        emit_if_usable("agent", "agent_metrics")
        emit_if_usable("host", "host_metrics")
        emit_if_usable("heartbeat", "heartbeat")
      }
    '
  )
fi
if [[ "$SEEN_LOGS" -eq 1 && "$SEEN_TRACES" -eq 1 && "$SEEN_OPENCLAW_METRICS" -eq 1 &&
      "$SEEN_AGENT_METRICS" -eq 1 && "$SEEN_HOST_METRICS" -eq 1 && "$SEEN_HEARTBEAT" -eq 1 ]]; then
  METRICS_AVAILABLE=1
else
  METRICS_AVAILABLE=0
fi

queue_age() {
  local signal="$1"
  local queued="$2"
  local seen="$3"
  local state_file="$STATE_DIR/queue-oldest-$signal.timestamp"
  local started=0
  if [[ "$seen" -eq 1 ]]; then
    if [[ "$queued" -gt 0 ]]; then
      if [[ -f "$state_file" && ! -L "$state_file" ]]; then
        started="$(sed -n '1p' "$state_file")"
      fi
      if [[ ! "$started" =~ ^[0-9]+$ || "$started" -eq 0 || "$started" -gt "$TIMESTAMP" ]]; then
        started="$TIMESTAMP"
        tmp_state="$(mktemp "$STATE_DIR/.queue-oldest-$signal.XXXXXX")"
        printf '%s\n' "$started" >"$tmp_state"
        chmod 0600 "$tmp_state"
        mv -f "$tmp_state" "$state_file"
      fi
    else
      rm -f "$state_file"
    fi
  elif [[ -f "$state_file" && ! -L "$state_file" ]]; then
    started="$(sed -n '1p' "$state_file")"
  fi
  if [[ "$started" =~ ^[0-9]+$ && "$started" -gt 0 && "$started" -le "$TIMESTAMP" ]]; then
    printf '%s' "$((TIMESTAMP - started))"
  else
    printf '0'
  fi
}

{
  echo '# HELP fleet_node_agent_heartbeat_timestamp_seconds Unix time when the node agent heartbeat was generated.'
  echo '# TYPE fleet_node_agent_heartbeat_timestamp_seconds gauge'
  printf 'fleet_node_agent_heartbeat_timestamp_seconds{node="%s"} %s\n' "$NODE_LABEL" "$TIMESTAMP"
  echo '# HELP fleet_node_agent_queue_metrics_available Whether all six expected local Collector queue exporter samples are present and valid.'
  echo '# TYPE fleet_node_agent_queue_metrics_available gauge'
  printf 'fleet_node_agent_queue_metrics_available{node="%s"} %s\n' "$NODE_LABEL" "$METRICS_AVAILABLE"
  echo '# HELP fleet_node_agent_queue_oldest_age_seconds Seconds since a signal queue was first observed non-empty without a subsequently observed valid zero.'
  echo '# TYPE fleet_node_agent_queue_oldest_age_seconds gauge'
  for mapping in \
    "logs:$QUEUE_LOGS:$SEEN_LOGS" \
    "traces:$QUEUE_TRACES:$SEEN_TRACES" \
    "openclaw_metrics:$QUEUE_OPENCLAW_METRICS:$SEEN_OPENCLAW_METRICS" \
    "agent_metrics:$QUEUE_AGENT_METRICS:$SEEN_AGENT_METRICS" \
    "host_metrics:$QUEUE_HOST_METRICS:$SEEN_HOST_METRICS" \
    "heartbeat:$QUEUE_HEARTBEAT:$SEEN_HEARTBEAT"; do
    signal="${mapping%%:*}"
    remainder="${mapping#*:}"
    queued="${remainder%%:*}"
    seen="${remainder##*:}"
    printf 'fleet_node_agent_queue_oldest_age_seconds{node="%s",signal="%s"} %s\n' \
      "$NODE_LABEL" "$signal" "$(queue_age "$signal" "$queued" "$seen")"
  done
} >"$TMP"
chmod 0644 "$TMP"
mv -f "$TMP" "$TARGET"
trap - EXIT
