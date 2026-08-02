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

The `0.2.0` node runtime and schema-v2 contract are implemented locally, but Phase 4 stopped at the
evidence boundary: there has been no canary install, OpenClaw restart, or proof that real OpenClaw
logs, metrics, and traces traverse the local Collector and arrive at Charizard. Nothing in this
repository is production-cut-over or approved for deletion of the `0.1.x` rollback paths.

The `0.2.0` implementation gives every node the same contract:

- one installer and one configuration shape;
- one Charizard-issued, revocable node credential;
- one canonical HTTPS OTLP/HTTP endpoint on Charizard, normally reached through Cloudflare Tunnel;
- OpenClaw logs, metrics, and traces sent to the loopback node agent; and
- one node agent that forwards those signals, locally scraped host metrics, self-metrics, and
  heartbeat through the authenticated Charizard endpoint.

Charizard will not scrape nodes in the target model. Nodes will not expose a fleet monitoring port,
run a per-node exporter tunnel, or choose behavior from `lan` versus `off_lan`. A future local route
may use split DNS or an endpoint override, but it must preserve the same protocol, credential, and
node runtime.

See [`docs/central-integration-plan.md`](docs/central-integration-plan.md) for the migration plan.

The agent is OpenTelemetry Collector Contrib `0.157.0`, pinned by platform URL and SHA-256. It is
the only node process that contacts Charizard. OpenClaw sends OTLP/HTTP to `127.0.0.1:4318`; the
agent scrapes local node_exporter and Collector self-metrics, then batches, gzips, persists, retries,
and exports each signal through the same authenticated HTTPS base endpoint.

Central state renders the versioned, secret-free intent file shown in
`examples/node-agent.example.json`. Its only fields are `config_schema_version`, `node_label`,
`telemetry_mode`, and `telemetry_endpoint`. The node account and every machine path are resolved
locally by the installer. Put the raw node credential in a separate mode-`0600` file owned by and
readable by the node account, then install:

```bash
chmod 0600 /absolute/path/to/ingest-token
sudo ./bin/install-fleet-node-agent \
  --config /absolute/path/to/node-agent.json \
  --node-user fleet-mini-03 \
  --ingest-token-file /absolute/path/to/ingest-token
```

`--node-user` is required for every unified install, including temporary legacy rich config. The
installer selects and verifies the Homebrew installation that owns (or will install)
`node_exporter`, then defaults the textfile directory to
`<that-prefix>/var/lib/node_exporter/textfile_collector`. Use the node-local
`--node-exporter-textfile-dir` override only when node_exporter is intentionally configured with a
different directory.

The installer validates the pinned Collector config, starts the LaunchDaemons, proves local health,
node_exporter, and heartbeat, and then rewrites OpenClaw to loopback OTLP with a timestamped backup.
It never accepts a token in argv or the environment. Review the backup and restart OpenClaw after
installation.

The OpenClaw rewrite still uses this repository's hardened JSON writer: it validates object shape,
creates a timestamped mode-`0600` pre-edit backup by default, writes atomically with fsync, sends
OpenClaw only to loopback, and replaces `diagnostics.otel.headers` with an empty object so the
central credential remains Collector-only. Keep that writer for the current pre-cutover runtime.
The available native `openclaw config` command is not yet a proven replacement: OpenClaw
`v2026.4.29` is only the known command-feature floor, not evidence that the installed runtime and
diagnostics-otel plugin pair emit compatible telemetry. A future native patch must use
`--replace-path diagnostics.otel.headers`; merge semantics must not leave stale central headers.
Its backup behavior must also be reconciled with the current timestamped-backup contract.

Current code sets `diagnostics.otel.captureContent=true`. Whether message/tool content may be
captured is an unresolved privacy decision; the recommended default is `false` before fleet
delegation unless the owner explicitly accepts content capture. Do not change writers or delegate
the rollout until the chosen setting is documented and tested. The release gate also requires an
OpenClaw restart followed by proof of actual OTLP receipt, not merely valid JSON or a healthy local
Collector.

The installer proves that `--node-user` is an unprivileged local account, obtains its real home
from macOS directory services, detects the local architecture, derives the OpenClaw and package
paths, and freezes that complete resolved config before making changes.
Root writes only protected staging files and system LaunchDaemon plists; runtime binaries, config,
credentials, queue/state directories, logs, and textfile paths are installed as the node user. This
keeps a node-user path replacement from turning an installer race into a privileged filesystem
write. The contract tests exercise the privilege boundary and symlink rejection, but a real
path-swap race still needs final validation in a disposable macOS VM before production rollout.

Existing unversioned rich config remains a temporary compatibility input. When the unified
installer receives it, `node_user`, `node_home`, OpenClaw/runtime paths, loopback endpoints, and the
textfile path are assertions against locally resolved values; any mismatch fails closed. Version 2
does not permit those fields, so central inventory cannot become the owner of node filesystem
layout.

The shipped render, secret, and OpenClaw helpers also accept the canonical v2 file when run directly
as the intended unprivileged node account. They derive that account's directory-service home, local
architecture, and selected supported Homebrew prefix. They fail clearly under root; root automation
must use the unified installer so account context remains explicit.

Codex usage collection has one supported data path: the installed `codex app-server` methods
`account/read` and `account/rateLimits/read`, under a bounded timeout. Codex alone owns login and
token refresh. Node code does not read or write Codex OAuth files, call private ChatGPT endpoints,
or reconstruct usage from session transcripts.

`telemetry_mode` is rollout state, not network location:

- `pull`: OpenClaw uses the local agent; the preserved legacy host-metric service is restored and
  the push heartbeat LaunchDaemon is removed.
- `dual`: the preserved legacy host-metric service remains canonical, while host metrics and the
  node heartbeat are also pushed under canary jobs.
- `push`: pushed host metrics use the canonical job and node_exporter is rebound to loopback. The
  exact prior system LaunchDaemon or user LaunchAgent is retained as a rollback artifact.

Move only one step at a time. After central parity and rollback evidence is accepted, a push node
may retire its legacy scrape proxy explicitly with `--retire-legacy-pull`. Per-node Cloudflare
tunnel deletion remains a central operator cutover action. Pull and dual fail closed if no legacy
artifact can be restored; a preserved loopback service also requires its scrape proxy. Explicit
retirement records a durable marker and disables automatic rollback until an operator manually
restores the retired artifacts. Reinstalling the same mode is idempotent and does not replace the
saved rollback copy.

### Backpressure contract

The Collector uses a 96 MiB memory limit, gzip, and finite exponential retries. In `pull`, its four
base disk-backed queues total about 192 MiB: logs 96 MiB, traces 48 MiB, OpenClaw metrics 32 MiB,
and agent self-metrics 16 MiB. In `dual` and `push`, host metrics add 48 MiB and heartbeat adds
8 MiB, producing six queues totaling about 248 MiB. Each queue has one consumer to cap replay
concurrency. This is a bounded drain, not a precise requests-per-second limiter. When capacity or
retry age is exhausted, the affected signal is dropped instead of blocking OpenClaw; this can
include OpenClaw logs and traces. Queue capacity/size and rejection metrics are exported by the
agent itself.
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
- the unified node-local metrics-to-OTLP collector and installer during pre-cutover validation
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
