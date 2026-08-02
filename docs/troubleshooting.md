# Troubleshooting

The LAN/off-LAN, proxy, and Cloudflare Access checks below apply to the `0.1.x` compatibility paths.

## Unified agent checks (0.2.0)

```bash
launchctl print system/com.unblocklabs.fleet-node-agent
curl -fsS http://127.0.0.1:13133/
curl -fsS http://127.0.0.1:8888/metrics | grep -E 'otelcol_exporter_queue_(size|capacity)'
curl -fsS http://127.0.0.1:9100/metrics | grep fleet_node_agent_heartbeat_timestamp_seconds
```

The Collector config and full Authorization header live below
`~/.openclaw/fleet-node-observability/`; the header file and its directory must remain `0600` and
`0700`. Do not print its contents. Collector errors are in that runtime's `logs/` directory.

If a queue fills during an outage, inspect queue size/capacity, send-failure, and rejected-item
metrics. One consumer per exporter deliberately limits replay concurrency after recovery. The
heartbeat has a separate queue and five-minute retry age; a stale occurrence timestamp must alert
even if an old queued heartbeat is delivered later.
Use `fleet_node_agent_queue_oldest_age_seconds` to distinguish a continuously non-empty queue from
a brief spike, and treat `fleet_node_agent_queue_metrics_available == 0` as loss of the local
measurement rather than an empty queue.

## Installation and launchctl issues

### Command exits with permission denied

- Confirm running with `sudo` for installer paths under `/Library` and LaunchDaemons.
- Check file ownership (`root:wheel`) and executable bit on installed scripts.

### Node-exporter is not exporting textfiles

```bash
launchctl print gui/$(id -u)/com.unblocklabs.node-exporter
launchctl print system/com.unblocklabs.node-exporter
ls -l /opt/homebrew/var/lib/node_exporter/textfile_collector
```

### Collector not running

```bash
launchctl list | grep -E "node-exporter|fleet"
launchctl print gui/$(id -u)/ | grep -i "collect"
```

If a collector exits quickly, inspect launch log files and rerun collector script manually.

## OTLP / OpenClaw config issues

### Unified OpenClaw configuration is not a receipt test

The current unified path uses the repository's atomic JSON writer and timestamped backup contract.
Do not substitute a native `openclaw config` patch solely because the command exists in
`v2026.4.29`; that version is only the command floor, not runtime/plugin compatibility proof. A
future native patch must replace `diagnostics.otel.headers` as a whole with
`--replace-path diagnostics.otel.headers` so stale central credentials cannot survive a merge.

After any approved configuration change:

1. Review the timestamped backup and resulting `diagnostics.otel` object without printing secrets.
2. Confirm the approved `captureContent` value; prefer `false` until content capture is explicitly
   accepted.
3. Restart OpenClaw.
4. Generate a known diagnostic event.
5. Prove the expected signals were received by the local Collector and then stored by Charizard
   under the authenticated node identity.

A healthy Collector endpoint, valid OpenClaw JSON, or successful config command is not sufficient
evidence of telemetry receipt.

### OTLP headers not applied

- Verify source token:

```bash
printenv FLEET_INGEST_TOKEN
```

- Re-run print helper and inspect output:

```bash
FLEET_INGEST_TOKEN=... ./bin/print-otlp-env --config /tmp/node-config.lan.json
```

### off-LAN 403 from cloud endpoint

- Ensure `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` are set, or pass
  `--cf-access-client-id` and `--cf-access-client-secret`.
- Confirm endpoint URL is HTTPS and matches central access policy.

## Textfile scrape issues

### `/metrics` returns 403

- Revisit proxy token:

```bash
cat /Library/OpenClaw/fleet-node-exporter-scrape-token
curl -fsS -H "X-Fleet-Scrape-Token: <token>" "http://127.0.0.1:19100/metrics"
```

### Missing metric families

- Confirm collector schedule and config:

```bash
ls "$HOME/Library/LaunchAgents/com.unblocklabs.openclaw-gateway-health-textfile.plist" \
  /Library/LaunchDaemons/com.unblocklabs.openclaw-gateway-health-textfile.plist 2>/dev/null
grep -R "fleet_macos_thermal" -n /opt/homebrew/var/lib/node_exporter/textfile_collector
```

## Collector-specific checks

- Codex usage:
  - confirm `codex` is on the LaunchAgent `PATH` and starts `codex app-server`;
  - run `collect-codex-usage --format prometheus` as the node user and inspect
    `codex_collector_success` plus `error_type`;
  - update Codex if `account/read` or `account/rateLimits/read` is unavailable.
- macOS thermal:
  - verify hardware support and permission state.
- OpenClaw readiness:
  - ensure OpenClaw is running and the ready endpoint responds.

## Fast rollback

- Keep last-known-good backups of:
  - `~/.openclaw/openclaw.json`
  - generated LaunchDaemon plist files
  - `/Library/OpenClaw/fleet-node-exporter-scrape-token`

Then restore and restart launch services to revert to the prior state.
