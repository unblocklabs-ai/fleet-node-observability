# Install LAN Node Observability

Use these steps to install the LAN (same-subnet) node tooling on a Mac mini.

Status: current `0.1.x` compatibility runbook. The selected product direction replaces this and the
off-LAN installer with one push-only installer. Do not extend this runbook with new LAN-specific
behavior.

## What it configures

- Installs `node_exporter` (Homebrew) when missing.
- Ensures the textfile collector directory exists.
- Schedules host-health collectors (Codex usage, macOS thermal, OpenClaw readiness).
- Writes install metadata for repeatable re-runs.

## Prerequisites

- macOS with user account for the node service.
- Homebrew and Python 3 available on the node.
- A node config file.

## Quick Start

1. Copy a config file and replace placeholders.

```bash
cp examples/node-config.lan.example.json /tmp/node-config.lan.json
```

2. Run the installer as the node user. Explicit flags can override config
   values during rollout.

```bash
./bin/install-lan-host-metrics \
  --config /tmp/node-config.lan.json \
  --node-label mini-03 \
  --node-user fleet-mini-03 \
  --node-exporter-port 9100 \
  --openclaw-ready-url "http://127.0.0.1:18789/readyz" \
  --node-exporter-textfile-dir "/opt/homebrew/var/lib/node_exporter/textfile_collector"
```

3. Validate basic health:

```bash
launchctl list | grep node_exporter
curl -fsS http://127.0.0.1:9100/metrics | head
```

## Rollback and cleanup

- Re-run the installer after changing config.
- To remove legacy shippers separately, run:

```bash
./bin/cleanup-legacy-shippers --apply
```

## Notes

- Keep config and runtime files out of logs and VCS when they contain local paths
  or sensitive operational details.
- Do not install this on nodes that are already managed entirely by central
  Alloy shipping for host metrics.
