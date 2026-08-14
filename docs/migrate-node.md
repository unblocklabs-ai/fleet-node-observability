# Node migration runbook

This runbook is only for replacing legacy telemetry on an existing managed
node. For a brand-new fleet member, use the central
[new-node runbook](https://github.com/unblocklabs-ai/fleet-observability/blob/main/docs/add-node.md).

Migrate one node at a time. Keep it in central maintenance until the new runtime is healthy and
Charizard has received current telemetry from it. A temporary logging gap is acceptable; the old
pull-based services are not retained as a fallback.

## 1. Survey and prepare centrally

Before changing the node, record its account, home directory, architecture, OpenClaw and Codex
locations, legacy fleet directories, legacy LaunchAgents or LaunchDaemons, and listeners on the new
runtime's loopback ports. Confirm that the node label and display name are correct in Charizard's
fleet state. Add a missing node or move an active node to maintenance before rollout.

Issue or rotate one per-node ingest credential, apply the resulting central state, and recreate or
reload central Alloy. Transfer the credential directly into a private node-side file without
printing it or placing it in argv, shell history, logs, or Git. Copy the rendered schema-3 node
config and the checksummed release artifact into a private rollout directory owned by the node
account.

## 2. Retire conflicting legacy services

The new node_exporter owns `127.0.0.1:9100`. Before installation, boot out old fleet LaunchAgents or
LaunchDaemons that own node_exporter, gateway-health, thermal, Codex-usage, or Vector collection.
Rename their plist files with a timestamped `.disabled-fleet-node-cutover-*` suffix so the initial
cutover is recoverable. Confirm that no old process still owns port 9100.

Do not keep the legacy and new telemetry paths running together. They duplicate collection, create
ambiguous failures, and can send the same records twice.

## 3. Install the release

Verify the release checksum, install it at `/usr/local/fleet-node-observability`, then run the root
installer with the rendered config, managed node account, and protected token file:

```bash
sudo /usr/local/fleet-node-observability/bin/install-fleet-node-agent \
  --config /absolute/private-rollout/node-agent.json \
  --node-user NODE_ACCOUNT \
  --ingest-token-file /absolute/private-rollout/ingest-token
```

Delete the rollout copy of the token immediately after a successful install. Review the timestamped
OpenClaw configuration backup, confirm its OTLP endpoint is `http://127.0.0.1:4318` with empty
headers, then restart the OpenClaw gateway. When the restart runs through non-login SSH, supply an
explicit PATH containing the managed account's npm-global, local-bin, FNM default-runtime,
Homebrew, and system directories; do not assume shell startup files resolved `node`.

The installer must report that the official `diagnostics-otel` extension is installed or already
ready. Configuration alone is insufficient: after restart, confirm the Collector accepted and sent
log records before using Loki receipt as the final proof.

## 4. Verify the node

Prove the long-running services and local sources rather than relying only on installer output:

```bash
sudo launchctl print system/com.unblocklabs.fleet-node-agent
sudo launchctl print system/com.unblocklabs.node-exporter
curl -fsS http://127.0.0.1:13133/
curl -fsS http://127.0.0.1:9100/metrics | grep '^fleet_node_agent_heartbeat_timestamp_seconds'
```

Also inspect the scheduled gateway-health, thermal, and, when enabled, Codex usage textfiles. A
scheduled one-shot service may be stopped between intervals; a fresh successful metric is the
relevant evidence. If Codex usage is enabled, confirm the generated metric reports
`error_type="none"`. Its LaunchDaemon PATH includes Homebrew plus the managed account's
`~/.npm-global/bin`, `~/.local/bin`, and FNM default-runtime locations.

## 5. Verify Charizard and activate

On Charizard, verify that the authenticated node has a current heartbeat, current host metrics,
recent logs, and successful Codex collection when enabled. Inspect Collector queue and export-health
metrics for backlog or repeated send failures. Only then move the node lifecycle to active, apply
the new central runtime revision, and recreate or reload services that consume it.

## 6. Remove obsolete files

After end-to-end verification, delete only the surveyed legacy fleet directories, the disabled
legacy plist files, and the private rollout directory. Do not use broad globs or recursive deletion
against a home directory. Confirm that the installed release, active node runtime under
`~/.openclaw/fleet-node-observability`, and current OpenClaw configuration remain in place.

Record the release version, node label, central revision, cleanup paths, and verification evidence
before starting the next node.
