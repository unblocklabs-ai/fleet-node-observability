#!/bin/sh
# Emergency recovery for this exact canary; not routine npm version rollback.
set -eu
umask 077
[ "$(/usr/bin/id -u)" -eq 0 ] || { echo 'Run with sudo on Bill' >&2; exit 1; }
[ "$(/usr/bin/id -u billjohansson)" -eq 501 ] || exit 1
rollout=/Users/billjohansson/fleet-node-ts-canary-20260927
root=/Users/billjohansson/.local/share/fleet-node
node=/opt/homebrew/opt/node@24/bin/node
# Emergency recovery must work even if npm removed or broke the new CLI.
if [ -e "$root/services/enabled" ]; then
  [ -f "$root/services/enabled" ] && [ ! -L "$root/services/enabled" ] || exit 1
  /bin/mv "$root/services/enabled" "$rollout/rollback/new-enabled-before-return-$(/bin/date +%s)"
fi
for job in collector exporter heartbeat readiness thermal cron sessions codex; do
  /bin/launchctl disable system/com.unblocklabs.fleet-node-ts.$job
  if /bin/launchctl print system/com.unblocklabs.fleet-node-ts.$job >/dev/null 2>&1; then /bin/launchctl bootout system/com.unblocklabs.fleet-node-ts.$job; fi
done
attempt=0
while /usr/sbin/lsof -nP -iTCP:9100 -iTCP:4318 -iTCP:8888 -iTCP:13133 -sTCP:LISTEN >/dev/null 2>&1 || /usr/bin/pgrep -u 501 -f '/Users/billjohansson/.local/fleet-node-npm/|/Users/billjohansson/.local/share/fleet-node/' >/dev/null; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 30 ] || { echo 'New runtime did not quiesce; do not restore queues yet.' >&2; exit 1; }
  /bin/sleep 1
done
if [ -e "$rollout/cutover-state-preserved.json" ] && [ ! -e "$rollout/rollback-state-preserved.json" ]; then
  /usr/bin/sudo -u billjohansson /usr/bin/env HOME=/Users/billjohansson "$node" "$rollout/import-bill-state.mjs" --reverse
fi
for label in com.unblocklabs.fleet-node-agent com.unblocklabs.node-exporter com.unblocklabs.fleet-node-agent-heartbeat com.unblocklabs.openclaw-gateway-health-textfile com.unblocklabs.macos-thermal-textfile com.unblocklabs.openclaw-cron-schedule-textfile com.unblocklabs.codex-usage-textfile; do
  source=$rollout/rollback/$label.plist
  target=/Library/LaunchDaemons/$label.plist
  if [ -f "$source" ]; then
    [ ! -e "$target" ] || exit 1
    /bin/mv "$source" "$target"
    /usr/sbin/chown root:wheel "$target"
    /bin/chmod 644 "$target"
  fi
  /bin/launchctl enable system/$label
  if ! /bin/launchctl print system/$label >/dev/null 2>&1; then /bin/launchctl bootstrap system "$target"; fi
done
target=/Users/billjohansson/Library/LaunchAgents/com.unblocklabs.macos-thermal-textfile.plist
if [ -f "$rollout/rollback/gui-macos-thermal-textfile.plist" ]; then
  [ ! -e "$target" ] || exit 1
  /bin/mv "$rollout/rollback/gui-macos-thermal-textfile.plist" "$target"
  /usr/sbin/chown billjohansson:staff "$target"
fi
/bin/launchctl enable gui/501/com.unblocklabs.macos-thermal-textfile
if ! /bin/launchctl print gui/501/com.unblocklabs.macos-thermal-textfile >/dev/null 2>&1; then /bin/launchctl bootstrap gui/501 "$target"; fi
echo 'Legacy telemetry restored; new runtime and pre-return state retained for recovery. Verify central freshness.'
