# Fleet Node Observability

Node-side telemetry runtime for managed OpenClaw macOS hosts.

It collects OpenClaw telemetry and host health signals, then sends them to the
central fleet observability endpoint. Grafana, Prometheus, Loki, Tempo,
inventory, credentials, and deployment orchestration live in the separate
`fleet-observability` repository.

## What runs on each node

- OpenTelemetry Collector receives OpenClaw logs, metrics, and traces over
  loopback OTLP.
- node_exporter exposes host and textfile metrics on `127.0.0.1:9100`.
- Scheduled collectors report:
  - agent heartbeat
  - OpenClaw readiness
  - macOS thermal state
  - OpenClaw cron schedules
  - Codex usage, when enabled

Only the Collector communicates with the central telemetry endpoint.

## Configuration

Each node receives a centrally managed configuration:

```json
{
  "config_schema_version": 3,
  "node_label": "mini_03",
  "telemetry_endpoint": "https://telemetry.example.com",
  "codex_usage_enabled": true
}
```

See [`examples/node-agent.example.json`](examples/node-agent.example.json).

The node label, telemetry endpoint, and per-node ingest credential must be
provisioned before installation. The installer discovers machine-specific
details such as the node home directory, architecture, Homebrew prefix, and
runtime paths.

## Adding a new node

The central `fleet-observability` repository owns fleet identity, credential
issuance, configuration rendering, activation, and end-to-end verification.
Follow its
[new-node runbook](https://github.com/unblocklabs-ai/fleet-observability/blob/main/docs/add-node.md)
before using the installer below.

This repository begins at the node-local installation boundary: it consumes the
rendered node configuration and protected ingest token produced by the central
workflow.

## Installation

Install a checksummed release:

```bash
release_version="$(cat VERSION)"
sudo ./packaging/install-from-release.sh \
  --tarball "/path/to/fleet-node-observability-${release_version}.tar.gz" \
  --sha256 "/path/to/fleet-node-observability-${release_version}.sha256"
```

Then install the node services:

```bash
sudo /usr/local/fleet-node-observability/bin/install-fleet-node-agent \
  --config /absolute/path/to/node-agent.json \
  --node-user NODE_ACCOUNT \
  --ingest-token-file /absolute/protected/ingest-token
```

The ingest token must be stored in a private regular file. It is never accepted
through command-line arguments or environment variables.

For an existing managed node, follow the
[node migration runbook](docs/migrate-node.md).

## Verification

Check the installed services and local health endpoints:

```bash
sudo launchctl print system/com.unblocklabs.fleet-node-agent
sudo launchctl print system/com.unblocklabs.node-exporter
curl -fsS http://127.0.0.1:13133/
curl -fsS http://127.0.0.1:9100/metrics |
  grep '^fleet_node_agent_heartbeat_timestamp_seconds'
```

A rollout is complete only after current telemetry is also visible centrally.

## Documentation

- [Node and central contract](docs/central-contract.md)
- [Node migration runbook](docs/migrate-node.md)
- [OpenClaw OTLP configuration](docs/openclaw-otel.md)
- [Textfile metrics](docs/textfile-metrics.md)
- [Troubleshooting](docs/troubleshooting.md)

## Development

The Python package requires Python 3.11 or newer and has no third-party runtime
dependencies.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"
python3 -m compileall -q src tests
```

Build a release into a temporary directory:

```bash
./packaging/build-release.sh --output "$(mktemp -d)"
```

See [`CHANGELOG.md`](CHANGELOG.md) for release history.
