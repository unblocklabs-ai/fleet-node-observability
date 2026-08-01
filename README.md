# Fleet Node Observability

Node-side observability tooling for OpenClaw fleet hosts.

This repository is intended to hold the code that runs on each managed Mac mini:
installers, host-health collectors, node-local textfile metrics, and helpers for
configuring OpenClaw OTLP export to the central fleet observability stack.

The central observability stack remains separate. Grafana, Alloy, Prometheus,
Loki, Tempo, dashboards, alerts, fleet inventory, and Charizard deployment logic
belong in the central `fleet-observability` repository.

## Unified Agent (0.2.0 pre-cutover)

The current `0.1.x` implementation preserves separate LAN and off-LAN install and transport paths
for compatibility with production. Those paths are transitional technical debt.

The `0.2.0` implementation gives every node the same contract:

- one installer and one configuration shape;
- one Charizard-issued, revocable node credential;
- one canonical HTTPS OTLP/HTTP endpoint on Charizard, normally reached through Cloudflare Tunnel;
- OpenClaw logs, metrics, and traces pushed to that endpoint; and
- a node-local collector that scrapes local `node_exporter` and textfile metrics and pushes them over
  the same OTLP path.

Charizard will not scrape nodes in the target model. Nodes will not expose a fleet monitoring port,
run a per-node exporter tunnel, or choose behavior from `lan` versus `off_lan`. A future local route
may use split DNS or an endpoint override, but it must preserve the same protocol, credential, and
node runtime.

See [`docs/central-integration-plan.md`](docs/central-integration-plan.md) for the migration plan.

The agent is OpenTelemetry Collector Contrib `0.157.0`, pinned by platform URL and SHA-256. It is
the only node process that contacts Charizard. OpenClaw sends OTLP/HTTP to `127.0.0.1:4318`; the
agent scrapes local node_exporter and Collector self-metrics, then batches, gzips, persists, retries,
and exports each signal through the same authenticated HTTPS base endpoint.

Central state renders a secret-free config like `examples/node-agent.example.json`. Put the raw
node credential in a separate mode-`0600` file, then install:

```bash
chmod 0600 /absolute/path/to/ingest-token
sudo ./bin/install-fleet-node-agent \
  --config /absolute/path/to/node-agent.json \
  --ingest-token-file /absolute/path/to/ingest-token
```

The installer validates the pinned Collector config, starts the LaunchDaemons, proves local health,
node_exporter, and heartbeat, and then rewrites OpenClaw to loopback OTLP with a timestamped backup.
It never accepts a token in argv or the environment. Review the backup and restart OpenClaw after
installation.

`telemetry_mode` is rollout state, not network location:

- `pull`: OpenClaw uses the local agent; legacy host-metric pull remains canonical.
- `dual`: host metrics are also pushed under a canary job while legacy pull remains canonical.
- `push`: pushed host metrics use the canonical job and node_exporter is rebound to loopback.

Move only one step at a time. After central parity and rollback evidence is accepted, a push node
may retire its legacy scrape proxy explicitly with `--retire-legacy-pull`. Per-node Cloudflare
tunnel deletion remains a central operator cutover action.

### Backpressure contract

The Collector uses a 96 MiB memory limit, gzip, finite exponential retries, and six disk-backed
queues capped at about 248 MiB total: logs 96 MiB, traces 48 MiB, OpenClaw metrics 32 MiB, host
metrics 48 MiB, agent self-metrics 16 MiB, and heartbeat 8 MiB. Each queue has one consumer to cap
replay concurrency. This is a bounded drain, not a precise requests-per-second limiter. When a queue
or retry age is exhausted, the affected low-priority telemetry is dropped instead of blocking
OpenClaw; queue capacity/size and rejection metrics are exported by the agent itself.
Queue-side batching caps serialized outbound requests at 1 MiB for logs/traces, 512 KiB for
OpenClaw/host metrics, 256 KiB for agent metrics, and 64 KiB for heartbeat, with a one-second flush.
The heartbeat textfile also emits a bounded per-signal
`fleet_node_agent_queue_oldest_age_seconds` gauge, plus a local queue-metrics availability gauge,
so an operator can distinguish a continuously backed-up queue from a transient sample.

## Scope

This repo should contain:

- node_exporter installation and verification scripts
- macOS LaunchDaemon or LaunchAgent installers for node-local collectors
- OpenClaw OTLP environment/config helpers
- the unified node-local metrics-to-OTLP collector and installer after the next refactor
- transitional LAN/off-LAN compatibility helpers until the push cutover is complete
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

## Current Release Quick Start

The following commands describe the topology-specific `0.1.x` rollback release. They remain
available during the `0.2.0` dual-run window; they are not the target onboarding interface.

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

The current release must continue emitting data that the central stack can rely on during migration:

- stable `node` and `node_label` values
- OpenClaw OTLP logs, metrics, and traces authenticated with central Basic auth
- optional Cloudflare Access headers for off-LAN OTLP
- node_exporter textfile metrics for fleet state, OpenClaw gateway readiness,
  Codex usage, and macOS thermal pressure
- bounded labels suitable for Prometheus and Loki

The target stable interface is one authenticated OTLP/HTTP push contract for OpenClaw and host
telemetry. Metric meaning and bounded labels must survive the transport migration.

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
