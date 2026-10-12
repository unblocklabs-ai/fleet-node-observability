#!/bin/sh
# One-time, explicitly Bill-only sudo step. Preserves all old runtime files.
set -eu
umask 077
[ "$(/usr/bin/id -u)" -eq 0 ] || { echo 'Run with sudo on Bill' >&2; exit 1; }
[ "$(/usr/bin/id -u billjohansson)" -eq 501 ] || exit 1
[ "$(/usr/bin/uname -s)" = Darwin ] || exit 1
rollout=/Users/billjohansson/fleet-node-ts-canary-20260927
root=/Users/billjohansson/.local/share/fleet-node
node=/opt/homebrew/opt/node@24/bin/node
cli=/Users/billjohansson/.local/fleet-node-npm/lib/node_modules/@unblocklabs/fleet-node/dist/cli.js
[ -d "$rollout/rollback" ] && [ ! -L "$rollout/rollback" ] || exit 1
[ ! -e "$root/services/enabled" ] && [ ! -L "$root/services/enabled" ] || { echo 'New gate must be closed' >&2; exit 1; }
[ ! -e "$rollout/cutover-state-preserved.json" ] && [ ! -L "$rollout/cutover-state-preserved.json" ] || { echo 'Cutover already copied state; inspect before retry' >&2; exit 1; }
# A failed pre-copy attempt may already have parked some or all old plists.
# Accept exactly one regular, non-symlink copy; never replace recovery evidence.
check_parked_plist() {
  if [ -e "$1" ] || [ -L "$1" ]; then
    [ -f "$1" ] && [ ! -L "$1" ] && [ ! -e "$2" ] && [ ! -L "$2" ]
  else
    [ -f "$2" ] && [ ! -L "$2" ]
  fi
}
old_gui=/Users/billjohansson/Library/LaunchAgents/com.unblocklabs.macos-thermal-textfile.plist
check_parked_plist "$old_gui" "$rollout/rollback/gui-macos-thermal-textfile.plist" || exit 1
/usr/bin/sudo -u billjohansson /usr/bin/env HOME=/Users/billjohansson "$node" "$cli" validate --root "$root"
for label in com.unblocklabs.fleet-node-agent com.unblocklabs.node-exporter com.unblocklabs.fleet-node-agent-heartbeat com.unblocklabs.openclaw-gateway-health-textfile com.unblocklabs.macos-thermal-textfile com.unblocklabs.openclaw-cron-schedule-textfile com.unblocklabs.codex-usage-textfile; do
  source=/Library/LaunchDaemons/$label.plist
  saved=$rollout/rollback/$label.plist
  check_parked_plist "$source" "$saved" || { echo "Unexpected plist state: $label" >&2; exit 1; }
done
for label in com.unblocklabs.fleet-node-agent com.unblocklabs.node-exporter com.unblocklabs.fleet-node-agent-heartbeat com.unblocklabs.openclaw-gateway-health-textfile com.unblocklabs.macos-thermal-textfile com.unblocklabs.openclaw-cron-schedule-textfile com.unblocklabs.codex-usage-textfile; do
  /bin/launchctl disable system/$label
  if /bin/launchctl print system/$label >/dev/null 2>&1; then /bin/launchctl bootout system/$label; fi
  if [ -f /Library/LaunchDaemons/$label.plist ]; then
    /bin/mv /Library/LaunchDaemons/$label.plist "$rollout/rollback/$label.plist"
  fi
done
/bin/launchctl disable gui/501/com.unblocklabs.macos-thermal-textfile
if /bin/launchctl print gui/501/com.unblocklabs.macos-thermal-textfile >/dev/null 2>&1; then /bin/launchctl bootout gui/501/com.unblocklabs.macos-thermal-textfile; fi
if [ -f "$old_gui" ]; then /bin/mv "$old_gui" "$rollout/rollback/gui-macos-thermal-textfile.plist"; fi
# Match fleet configuration, collectors and outputs, not the shared otelcol binary.
# Bill's separate codex-otel-capture service uses that binary with its own config.
old_process_pattern='/Users/billjohansson/\.openclaw/fleet-node-observability/(config/collector[.]json|python/|bin/(fleet-node-agent-heartbeat|openclaw-gateway-health))|/opt/homebrew/var/lib/node_exporter/textfile_collector|/Users/billjohansson/bin/collect-macos-thermal[.]py'
attempt=0
while /usr/sbin/lsof -nP -iTCP:9100 -iTCP:4318 -iTCP:8888 -iTCP:13133 -sTCP:LISTEN >/dev/null 2>&1 || /usr/bin/pgrep -u 501 -f "$old_process_pattern" >/dev/null; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 30 ] || { echo 'Old runtime did not quiesce; do not copy queues. Recovery plists retained.' >&2; exit 1; }
  /bin/sleep 1
done
for label in com.unblocklabs.fleet-node-agent com.unblocklabs.node-exporter com.unblocklabs.fleet-node-agent-heartbeat com.unblocklabs.openclaw-gateway-health-textfile com.unblocklabs.macos-thermal-textfile com.unblocklabs.openclaw-cron-schedule-textfile com.unblocklabs.codex-usage-textfile; do
  if /bin/launchctl print system/$label >/dev/null 2>&1; then echo "Old job still loaded: $label" >&2; exit 1; fi
  /bin/launchctl print-disabled system | /usr/bin/grep -F "\"$label\" => disabled" >/dev/null || { echo "Old job not disabled: $label" >&2; exit 1; }
done
if /bin/launchctl print gui/501/com.unblocklabs.macos-thermal-textfile >/dev/null 2>&1; then echo 'Old GUI thermal job still loaded' >&2; exit 1; fi
/bin/launchctl print-disabled gui/501 | /usr/bin/grep -F '"com.unblocklabs.macos-thermal-textfile" => disabled' >/dev/null || { echo 'Old GUI thermal job not disabled' >&2; exit 1; }
/usr/bin/sudo -u billjohansson /usr/bin/env HOME=/Users/billjohansson "$node" "$rollout/import-bill-state.mjs"
/bin/sh "$root/services/install-system.sh"
/usr/bin/sudo -u billjohansson /usr/bin/env HOME=/Users/billjohansson "$node" "$cli" start --root "$root"
/usr/bin/touch "$rollout/cutover-complete"
/usr/sbin/chown billjohansson:staff "$rollout/cutover-complete"
echo 'Bill cutover complete. Old files retained. Verify fresh local and central signals next.'
