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
from network location. The central runtime inventory is schema 4 and renders the same secret-free
node config consumed by `install-fleet-node-agent`.

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
