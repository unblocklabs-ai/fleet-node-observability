# Central Compatibility Contract

This document records the boundary between this node-side repository and the
central `fleet-observability` repository.

## Node Responsibilities

Node-side tooling is responsible for installing or configuring:

- node_exporter and textfile collector directories
- OpenClaw readiness collection
- Codex usage collection when enabled
- macOS thermal pressure collection when available
- OpenClaw OTLP exporter settings
- off-LAN access headers when the node uses Cloudflare Access
- cleanup of retired local shippers that bypass central Alloy

## Central Responsibilities

The central stack is responsible for:

- fleet inventory and node identity policy
- per-node ingest token lifecycle
- Alloy OTLP authentication and identity normalization
- Prometheus scrape configuration
- Loki, Tempo, and Grafana server configuration
- dashboards, alerts, audits, and live validation

## Stable Interface

The split should preserve these interfaces:

- `node` and `node_label` identify the same stable machine label.
- OTLP uses `Authorization=Basic ...` generated from the node label and central
  ingest token.
- Off-LAN OTLP also carries `CF-Access-Client-Id` and
  `CF-Access-Client-Secret`.
- Host metrics are exposed through node_exporter.
- Node-local collectors write Prometheus textfile metrics for central scraping.
- Metric and label names used by central dashboards are treated as public API.

## Versioning

Node release notes should call out:

- added or removed collectors
- changed metric names or labels
- changed LaunchDaemon or LaunchAgent paths
- changed OpenClaw config keys
- changed authentication/header behavior
- minimum compatible central stack version
