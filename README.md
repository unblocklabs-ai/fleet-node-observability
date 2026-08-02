# Fleet Node Observability

Node-side telemetry for managed OpenClaw macOS hosts. The central Grafana, Alloy, Prometheus, Loki,
Tempo, inventory, credential, and Charizard deployment code lives in the separate central
`fleet-observability` repository.

## Architecture

Every node runs the same local services:

- OpenClaw sends OTLP/HTTP logs, metrics, and traces to `127.0.0.1:4318` with no central credential.
- A pinned OpenTelemetry Collector Contrib process receives OpenClaw telemetry, scrapes loopback
  node_exporter and its own metrics, batches and queues each signal, and sends authenticated
  OTLP/HTTP to one canonical HTTPS endpoint.
- node_exporter listens only on `127.0.0.1:9100` and includes local textfile metrics.
- Scheduled producers emit agent heartbeat, OpenClaw readiness, and macOS thermal metrics. Codex
  usage collection is enabled per node.

Only the Collector makes network requests to Charizard. Physical network location does not change
the node code, protocol, credential, or service layout.

## Node intent

The only accepted configuration is schema 3 with exactly four fields:

```json
{
  "config_schema_version": 3,
  "node_label": "mini_03",
  "telemetry_endpoint": "https://telemetry.example.com",
  "codex_usage_enabled": true
}
```

The central repository owns this intent. The installer resolves the node account, home directory,
architecture, Homebrew prefix, binaries, runtime directories, textfile directory, and loopback
ports locally. Extra or missing fields are rejected.

## Install interface

Installation is intentionally not performed by this repository cleanup. A later deployment step
will provide a schema-3 config and a protected per-node token file to:

```bash
sudo ./bin/install-fleet-node-agent \
  --config /absolute/path/to/node-agent.json \
  --node-user fleet-mini-03 \
  --ingest-token-file /absolute/protected/ingest-token
```

The token file may remain root-owned mode `0600`. Before any managed host write, the root installer
descriptor-opens it with no symlink following, verifies that it is a private, stable, bounded regular
file containing exactly one token, and copies only that token into a mode-`0400` temporary snapshot
owned by the node account. The unprivileged secret writer reads that snapshot, and the root cleanup
trap removes it. Token contents never enter argv, environment variables, generated JSON, or output.
The installed runtime contains only the complete Basic Authorization header in the node-owned
mode-`0600` Collector secret file.

The installer:

1. snapshots and validates the source config before managed writes;
2. resolves and freezes local machine context;
3. downloads or accepts the pinned Collector archive and verifies its SHA-256;
4. renders and validates the complete Collector configuration;
5. installs the final LaunchDaemons and proves Collector health, node_exporter metrics, and a fresh
   heartbeat; and
6. atomically points OpenClaw at loopback OTLP after making a private timestamped backup when an
   existing config is present.

OpenClaw content capture is disabled. Its OTLP headers are replaced with an empty object so stale
central credentials cannot remain in `openclaw.json`.

## Backpressure

Collector Contrib `0.157.0` is pinned by platform URL and SHA-256. All six pipelines are always
rendered: OpenClaw logs, traces, and metrics; host metrics; Collector self-metrics; and heartbeat.
They use gzip, finite retries, bounded persistent byte queues, one queue consumer, and capped request
batches. Queue exhaustion drops the affected signal instead of blocking OpenClaw.

The heartbeat producer emits queue-metric availability and bounded per-signal oldest-backlog-age
gauges so central alerts can distinguish current liveness from delayed replay.

## Repository scope

```text
bin/        Stable operator entrypoints.
src/        Agent rendering, installer, local collectors, and secure writers.
docs/       Current node and central contracts.
examples/   Sanitized schema-3 node intent.
packaging/  Versioned tarball and checksum helpers.
tests/      Unit, security, installer, and packaging contracts.
```

## Development

The Python package has no third-party runtime dependencies. Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"
python3 -m compileall -q src tests
for script in bin/collect-* bin/install-* bin/openclaw-* packaging/*.sh \
  src/fleet_node_observability/collectors/*.sh \
  src/fleet_node_observability/installers/*.sh; do bash -n "$script"; done
```

Build release evidence outside the repository's existing `dist/` directory:

```bash
./packaging/build-release.sh --output "$(mktemp -d)"
```

See [central contract](docs/central-contract.md), [OpenClaw configuration](docs/openclaw-otel.md),
[textfile metrics](docs/textfile-metrics.md), and [troubleshooting](docs/troubleshooting.md).
