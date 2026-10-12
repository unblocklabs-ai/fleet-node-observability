#!/bin/sh
# Remove only Bill's inactive root-owned legacy package; no service/policy changes.
# Verified recovery archive on Bek's Mac:
# /Users/bek/fleet-node-bill-recovery-20260928.zPBa3G/retired-fleet-files.tar.gz
# SHA256 f22ab8b0d966e685f9f3bbe5d3eb95e8cf9d6e1702d6c347ed5764d01a82e256
set -eu
umask 077
case "${1:---check}" in --check|--remove) ;; *) exit 2;; esac
[ "$(/usr/bin/uname -s)" = Darwin ]
[ "$(/usr/bin/id -u billjohansson)" = 501 ]
[ -d /usr/local/fleet-node-observability ]
[ ! -L /usr/local/fleet-node-observability ]
[ "$(cd /usr/local/fleet-node-observability && /bin/pwd -P)" = /usr/local/fleet-node-observability ]
[ "$(/usr/bin/stat -f %u /usr/local/fleet-node-observability)" = 0 ]
[ -f /usr/local/fleet-node-observability/VERSION ]
[ ! -L /usr/local/fleet-node-observability/VERSION ]
[ "$(/bin/cat /usr/local/fleet-node-observability/VERSION)" = 0.3.3 ]
[ -f /Users/billjohansson/.local/share/fleet-node/state/retired-cleanup-20260928.json ]
[ -f /Users/billjohansson/.local/share/fleet-node/services/enabled ]
processes=$(/bin/ps -axo command=)
if printf '%s\n' "$processes" | /usr/bin/grep -F '/usr/local/fleet-node-observability/' >/dev/null; then
  echo 'Legacy package is referenced by a process; refusing removal.' >&2
  exit 1
fi
for label in com.unblocklabs.fleet-node-agent com.unblocklabs.node-exporter com.unblocklabs.fleet-node-agent-heartbeat com.unblocklabs.openclaw-gateway-health-textfile com.unblocklabs.macos-thermal-textfile com.unblocklabs.openclaw-cron-schedule-textfile com.unblocklabs.codex-usage-textfile; do
  if /bin/launchctl print system/$label >/dev/null 2>&1; then exit 1; fi
  [ ! -e /Library/LaunchDaemons/$label.plist ]
  /bin/launchctl print-disabled system | /usr/bin/grep -F "\"$label\" => disabled" >/dev/null
done
for plist in /Library/LaunchDaemons/*.plist /Library/LaunchAgents/*.plist /Users/billjohansson/Library/LaunchAgents/*.plist; do
  [ -f "$plist" ] || continue
  content=$(/usr/bin/plutil -convert xml1 -o - "$plist")
  if printf '%s\n' "$content" | /usr/bin/grep -F /usr/local/fleet-node-observability >/dev/null; then
    echo "Launch plist still references legacy package: $plist" >&2
    exit 1
  fi
done
/usr/bin/curl --max-time 5 -fsS http://127.0.0.1:13133/ >/dev/null
if [ "${1:---check}" = --check ]; then
  echo 'Verified inactive root-owned v0.3.3 package; removal preflight passed.'
  exit 0
fi
[ "$(/usr/bin/id -u)" = 0 ] || { echo 'This last root-owned directory requires sudo.' >&2; exit 1; }
/bin/rm -rf /usr/local/fleet-node-observability
[ ! -e /usr/local/fleet-node-observability ]
echo 'Removed /usr/local/fleet-node-observability. Recovery archive remains on Bek’s Mac. Active npm package and state untouched.'
