# Recoverable migration — Bill only

## Before cutover

1. Record current package/module hashes, service definitions, listeners, schema-3 config,
   session ledger counts/observed-since, Collector version and queue metrics. Inspect the
   deployed Charizard auth normalization, allowlist, alerts and dashboards read-only.
2. Install the packed npm artifact into Bill's **new dedicated user-owned prefix**;
   initialize the new external state root with Bill's existing secret-free schema-3
   config and `--authorization-file` pointing at the old protected header. Never print
   or put tokens in arguments/environment. Keep Node explicit and independent of OpenClaw.
3. Prepare pinned binaries, Collector config, and system definitions. Leave the new gate
   closed. Build/review/tests/packed validation precede this operation.
4. Retain the old `/usr/local/fleet-node-observability` and
   `~/.openclaw/fleet-node-observability` trees intact. Create a private rollback directory
   outside both npm and `.openclaw`. No credential rotation or central deployment is needed.

## One-time privileged operation

An operator must run the reviewed exact cutover script with sudo on Bill. No stored
password, new sudoers rule, or broader privilege is required or appropriate.

- Disable and boot out the seven old system labels (`com.unblocklabs.fleet-node-agent`,
  `node-exporter`, `fleet-node-agent-heartbeat`, `openclaw-gateway-health-textfile`,
  `macos-thermal-textfile`, `openclaw-cron-schedule-textfile`, `codex-usage-textfile`).
- Bill also has an older GUI thermal LaunchAgent; survey and disable it recoverably.
  Do not touch watchdogs, OpenClaw gateway, Codex app-server, or unrelated agents.
- Move only those surveyed exact plist files into the rollback directory. Verify
  launchd cannot relaunch them, their processes are gone, and ports 4318/8888/13133/9100
  are free. Killing an old process without disabling/bootout is unsafe.
- Copy stopped Collector queue files (including all storage metadata) into the new
  queue directory, preserving names. Copy queue-age timestamps and back up the session
  ledger consistently using SQLite backup. Do not copy a live SQLite main file alone.
  Preserve `metadata.observed_since` and hashed identities; do not reset history.
- Bootstrap the fixed new system LaunchDaemons using the generated installer. They
  still wait behind the closed gate until the ordinary user validates and starts them.

## Verify before declaring success

- All processes use Bill's UID. Only new labels own the loopback listeners.
- Existing OpenClaw loopback config and empty headers remain unchanged; preserve its
  explicitly enabled content-capture choice. No gateway restart is needed if unchanged.
- Fresh heartbeat persists while independent checks run; all six queue samples available.
- Fresh readiness, thermal, cron, sessions and optional Codex metrics retain labels/errors.
- Session legacy `observed_since` is unchanged, dedup identities/counts do not regress,
  new scheduled observations arrive and read-only source access remains intact.
- Collector accepts and sends native OpenClaw logs, metrics and traces; check errors and
  queues. Verify actual authenticated samples in Charizard Prometheus/Loki/Tempo.
- Exercise ordinary-user stop/start/restart, and packed npm upgrade/rollback with unchanged
  credentials, config, queued data and session state. Verify scheduled checks resume.

Do not migrate any other node. Record exact version/hash, timestamps, comparisons, and
limitations. Keep recovery assets until the canary is accepted.

## Cutover rollback (different from npm version rollback)

Close the new gate and verify quiescence first. With one-time recovery sudo, boot out and
disable the exact new labels. Back up old queue/state directories before copying **current**
new queue/state back to the legacy runtime (do not discard observations/queued records
created after cutover). Same Collector version and session schema make this compatible.
Restore old plists to their original exact paths, ownership and modes, re-enable and
bootstrap their original domains. Verify old listeners and fresh central signals. Keep
both new and old states and rollback backups recoverable; never run both telemetry paths.

After acceptance, separate explicitly scoped cleanup can archive legacy runtime/logs and
remove disabled definitions. This task does not broadly delete Bill's old installations.
