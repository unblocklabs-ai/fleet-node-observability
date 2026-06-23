#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: cleanup-legacy-shippers [--apply]

Audits a macOS fleet node for deprecated node-local log shippers left over from
the Vector/Promtail/direct-Loki migration. Without --apply, prints what would be
changed. With --apply, unloads known LaunchAgents, renames their plists with a
.disabled-<timestamp> suffix, and stops matching processes.
EOF
}

APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
uid="$(id -u)"
launch_agents_dir="$HOME/Library/LaunchAgents"

labels=(
  "com.unblocklabs.vector.openclaw"
  "dev.vector.agent"
  "ai.unblocklabs.promtail"
  "ai.openclaw.loki-log-shipper"
)

patterns=(
  "vector --config-yaml"
  "promtail"
  "loki-log-shipper"
)

run() {
  if [[ "$APPLY" -eq 1 ]]; then
    "$@"
  else
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
  fi
}

echo "host=$(hostname)"
echo "user=$(id -un)"
echo "mode=$([[ "$APPLY" -eq 1 ]] && echo apply || echo dry-run)"

echo "== matching processes =="
pgrep -laf "vector --config-yaml|promtail|loki-log-shipper" || true

echo "== matching launchctl labels =="
launchctl list | egrep "vector|promtail|loki" || true

echo "== known LaunchAgent cleanup =="
for label in "${labels[@]}"; do
  plist="$launch_agents_dir/$label.plist"
  if launchctl print "gui/$uid/$label" >/dev/null 2>&1; then
    echo "launchctl label present: $label"
    run launchctl bootout "gui/$uid/$label"
  elif launchctl list | awk '{print $3}' | grep -Fxq "$label"; then
    echo "launchctl label listed: $label"
    run launchctl bootout "gui/$uid/$label"
  fi

  if [[ -f "$plist" ]]; then
    disabled="$plist.disabled-$timestamp"
    echo "active plist: $plist"
    run mv "$plist" "$disabled"
  fi
done

echo "== known process cleanup =="
for pattern in "${patterns[@]}"; do
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    echo "matching process for '$pattern': pid=$pid"
    run kill "$pid"
  done < <(pgrep -f "$pattern" || true)
done

echo "== remaining matches =="
pgrep -laf "vector --config-yaml|promtail|loki-log-shipper" || true
launchctl list | egrep "vector|promtail|loki" || true

if [[ "$APPLY" -eq 0 ]]; then
  echo "dry-run complete; rerun with --apply to unload and disable legacy shippers"
else
  echo "cleanup complete"
fi
