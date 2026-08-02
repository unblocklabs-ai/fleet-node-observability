# Central Compatibility Contract

This document records the boundary between this node-side repository and the
central `fleet-observability` repository.

## Durable Contract

Every fleet node is an autonomous telemetry client. It runs one node package, uses one configuration
shape, and sends all signals outbound to one canonical HTTPS OTLP/HTTP endpoint on Charizard. The
normal route is through Charizard's Cloudflare Tunnel-backed hostname.

Charizard issues one revocable credential per node. Successful authentication establishes the
canonical `node_label`; client-supplied `node`, `node_label`, `host.name`, or `display_name` values
are not trusted identity. Credential representation may initially remain Basic auth for migration
compatibility, but onboarding exposes one node credential rather than topology-specific secrets.

Physical network location is not part of this interface. An optional local route may change DNS
resolution or the base endpoint, but not the protocol, token, signal shape, or installed services.

## Node Responsibilities

Node-side tooling is responsible for installing or configuring:

- node_exporter and textfile collector directories
- OpenClaw readiness collection
- Codex usage collection when enabled
- macOS thermal pressure collection when available
- OpenClaw OTLP exporter settings
- a node-local collector that scrapes local node_exporter/textfile metrics and exports OTLP
- retry, batching, and a bounded liveness/heartbeat signal for the outbound path
- cleanup of retired local shippers that bypass central Alloy

## Central Responsibilities

The central stack is responsible for:

- fleet inventory and node identity policy
- per-node ingest token lifecycle
- Alloy OTLP authentication and identity normalization
- canonical HTTPS ingest and Cloudflare Tunnel routing
- conversion/storage of pushed OTLP metrics in Prometheus
- Loki, Tempo, and Grafana server configuration
- dashboards, alerts, audits, and live validation

## Stable Interface

The split must preserve these interfaces:

- `node` and `node_label` identify the same stable machine label.
- Charizard derives trusted identity from the authenticated per-node credential.
- Logs, metrics, traces, and heartbeat data use the canonical OTLP/HTTP ingress.
- Host metrics are scraped locally and exported; Charizard does not connect to node_exporter.
- Node-local collectors may continue writing Prometheus textfiles as an internal collector
  interface.
- Metric and label names used by central dashboards are treated as public API.

The rollout field is `telemetry_mode`: `pull` retains legacy host scraping, `dual` pushes to a
canary job while retaining pull, and `push` makes pushed host metrics canonical. It is never derived
from network location. The central runtime inventory renders node config schema 2, whose complete
public shape is:

```json
{
  "config_schema_version": 2,
  "node_label": "mini_03",
  "telemetry_mode": "dual",
  "telemetry_endpoint": "https://telemetry.example.com"
}
```

Central config must not contain the node account, home, OpenClaw path, package-owned paths,
loopback ports, node_exporter target, or textfile directory. `install-fleet-node-agent` receives the
unprivileged account explicitly through `--node-user` for every install, resolves its home with
macOS directory services, detects architecture, selects and verifies the supported Homebrew prefix
that owns node_exporter, derives all internal paths locally, and freezes the complete resolved
config before managed filesystem changes. A node-local textfile-directory override is available
for an intentionally nonstandard node_exporter setup.

Unversioned rich node config remains a temporary migration input. Its local account, home, path,
and loopback values are assertions against node-derived context, not central controls; a mismatch
fails closed.

Direct helper use with schema 2 resolves the current unprivileged account, its real home, local
architecture, and supported Homebrew prefix. It must fail under root rather than derive root-owned
runtime paths; root provisioning uses the installer with explicit context.

The current unified installer uses the node repository's hardened OpenClaw JSON writer. It points
OpenClaw at loopback, replaces the complete header object with `{}`, and keeps the central
authorization header in the node Collector's mode-`0600` secret file. A native OpenClaw config
command is not yet part of this contract: `v2026.4.29` proves the command floor only, not a compatible
runtime/diagnostics-otel plugin pair. Any future native patch must use
`--replace-path diagnostics.otel.headers`, reconcile the existing timestamped-backup guarantee,
resolve the current `captureContent=true` privacy behavior (recommended target: `false`), restart
OpenClaw, and prove actual OTLP receipt through Charizard before adoption.

## Current Compatibility Surface

The `0.1.x` release still supports direct LAN node_exporter scraping, off-LAN per-node tunnels and
token proxies, direct LAN OTLP, and Cloudflare Access headers for remote OTLP. These behaviors are
maintained only until the push path passes dual-run validation. New design work must not add further
topology-specific behavior.

## Versioning

Node release notes should call out:

- added or removed collectors
- changed metric names or labels
- changed LaunchDaemon or LaunchAgent paths
- changed OpenClaw config keys
- changed authentication/header behavior
- changed local scrape-to-OTLP behavior or heartbeat semantics
- minimum compatible central stack version
