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
if METRICS="$(curl --fail --silent --show-error --max-time 2 "http://$COLLECTOR_METRICS_ENDPOINT/metrics" 2>/dev/null)"; then
  METRICS_AVAILABLE=1
fi

queue_size() {
  local exporter="$1"
  printf '%s\n' "$METRICS" | awk -v expected="$exporter" '
    /^otelcol_exporter_queue_size\{/ && index($0, "exporter=\"" expected "\"") { total += $NF }
    END { printf "%.0f\n", total + 0 }
  '
}

queue_age() {
  local signal="$1"
  local exporter="$2"
  local state_file="$STATE_DIR/queue-oldest-$signal.timestamp"
  local queued=0
  local started=0
  if [[ "$METRICS_AVAILABLE" -eq 1 ]]; then
    queued="$(queue_size "$exporter")"
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
  echo '# HELP fleet_node_agent_queue_metrics_available Whether the local Collector queue metrics endpoint was readable.'
  echo '# TYPE fleet_node_agent_queue_metrics_available gauge'
  printf 'fleet_node_agent_queue_metrics_available{node="%s"} %s\n' "$NODE_LABEL" "$METRICS_AVAILABLE"
  echo '# HELP fleet_node_agent_queue_oldest_age_seconds Seconds since a signal queue was first continuously observed non-empty.'
  echo '# TYPE fleet_node_agent_queue_oldest_age_seconds gauge'
  for mapping in \
    'logs:otlp_http/logs' \
    'traces:otlp_http/traces' \
    'openclaw_metrics:otlp_http/app_metrics' \
    'agent_metrics:otlp_http/agent' \
    'host_metrics:otlp_http/host' \
    'heartbeat:otlp_http/heartbeat'; do
    signal="${mapping%%:*}"
    exporter="${mapping#*:}"
    printf 'fleet_node_agent_queue_oldest_age_seconds{node="%s",signal="%s"} %s\n' \
      "$NODE_LABEL" "$signal" "$(queue_age "$signal" "$exporter")"
  done
} >"$TMP"
chmod 0644 "$TMP"
mv -f "$TMP" "$TARGET"
trap - EXIT
