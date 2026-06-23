# Fleet Node Observability

Node-side observability tooling for OpenClaw fleet hosts.

This repository is intended to hold the code that runs on each managed Mac mini:
installers, host-health collectors, node-local textfile metrics, and helpers for
configuring OpenClaw OTLP export to the central fleet observability stack.

The central observability stack remains separate. Grafana, Alloy, Prometheus,
Loki, Tempo, dashboards, alerts, fleet inventory, and Charizard deployment logic
belong in the central `fleet-observability` repository.

## Scope

This repo should contain:

- node_exporter installation and verification scripts
- macOS LaunchDaemon or LaunchAgent installers for node-local collectors
- OpenClaw OTLP environment/config helpers
- off-LAN host metrics setup helpers
- legacy shipper cleanup helpers
- node-local collectors for OpenClaw readiness, Codex usage, thermal pressure,
  and other host-health textfile metrics
- tests for node-side behavior and install output

This repo should not contain:

- Grafana dashboards or provisioning
- central Alloy configuration
- Prometheus, Loki, or Tempo server configuration
- central fleet inventory or raw secrets
- Charizard deployment scripts
- live dashboard audit artifacts

## Quick Start

Prepare a sanitized node config, then install host metrics and configure OpenClaw
OTLP separately:

```bash
cp examples/node-config.lan.example.json /tmp/node-config.lan.json
./bin/install-lan-host-metrics --config /tmp/node-config.lan.json
FLEET_INGEST_TOKEN=<token> ./bin/configure-openclaw-otel --config /tmp/node-config.lan.json
```

For off-LAN host metrics:

```bash
cp examples/node-config.off-lan.example.json /tmp/node-config.off-lan.json
sudo ./bin/install-off-lan-host-metrics --config /tmp/node-config.off-lan.json
FLEET_INGEST_TOKEN=<token> ./bin/configure-openclaw-otel --config /tmp/node-config.off-lan.json
```

## Planning Docs

The first extraction pass is cataloged in
[`docs/extraction-catalog.md`](docs/extraction-catalog.md).

The proposed long-term folder layout is documented in
[`docs/repository-structure.md`](docs/repository-structure.md).

## Compatibility Contract

The node tools must emit data that the central stack can rely on:

- stable `node` and `node_label` values
- OpenClaw OTLP logs, metrics, and traces authenticated with central Basic auth
- optional Cloudflare Access headers for off-LAN OTLP
- node_exporter textfile metrics for fleet state, OpenClaw gateway readiness,
  Codex usage, and macOS thermal pressure
- bounded labels suitable for Prometheus and Loki

Any breaking change to emitted metric names, label names, OTLP headers, or file
paths should be versioned and documented before central dashboards or alerts
depend on it.

## Repository Layout

```text
bin/        Stable operator command surface.
src/        Stdlib-only implementation package and installer bodies.
docs/       Operator docs, design notes, and compatibility contracts.
examples/   Sanitized node config examples.
packaging/  Local-first release artifact helpers.
tests/      Unit and contract tests for node-side behavior.
```

## Development

No runtime package manager is required yet. Keep scripts portable to the target
Mac mini environment unless a dependency is deliberately introduced and
documented.

Expected local checks after extraction:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"
python3 -m py_compile $(find src tests -name "*.py" -print)
bash -n bin/* packaging/*.sh src/fleet_node_observability/collectors/*.sh src/fleet_node_observability/installers/*.sh
```
