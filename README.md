# Fleet Node

Standalone macOS fleet telemetry, installed with npm. **Not an OpenClaw plugin.**

This repository contains the TypeScript agent previously developed in
`fleet-node-observability-ts`. It replaces the legacy Python implementation,
which remains available in Git history. The npm package remains
`@unblocklabs/fleet-node`; this source consolidation does not change installed
agents or require a fleet redeployment.

One user-owned package (`@unblocklabs/fleet-node`), one package version (`package.json`),
explicit Node 24.18+ runtime, and independent launchd jobs. No Python runtime, npm runtime
dependencies, custom supervisor, updater daemon, or release-directory manager.

## Design

| launchd job | Responsibility | Schedule |
|---|---|---|
| collector | Pinned OpenTelemetry Collector 0.157.0; six persistent queues | KeepAlive |
| exporter | Pinned node_exporter 1.11.1; loopback host/textfile metrics | KeepAlive |
| heartbeat | Occurrence timestamp and queue observation age | 30 seconds |
| readiness, thermal | Completed OpenClaw readiness probe / macOS pressure | 60 seconds each |
| cron, sessions, codex | Bounded cron snapshot / private session ledger / optional app-server usage | 300 seconds each |

The existing cron schedule also launches a separate, deadline-bound read-only
comparison collector every five minutes. It atomically replaces
`textfiles/node_comparison.prom`; node_exporter and the authenticated Collector
push carry it over the existing transport. No additional service or sudo setup
is required for an installed TypeScript agent.

Comparison reports the manifest at the running Gateway's serving root, the
version of its mapped executable (with matching on-disk inode), stored
`tools.profile`/`tools.exec.mode`, and system TCC records for that exact path.
When the Gateway rewrites its process title, its mapped OpenClaw-owned native
module identifies the serving root; ambiguous or unavailable roots remain
unreadable without hiding independently verified executable evidence.
`unset`, denied, no matching record, unreadable, conflicting and other recorded
values stay distinct. Unreadable databases never imply denial. The private
`state/comparison.json` records executable identity and last successful
observation; executable changes reset successful-observation history. No
permissions, producer configuration or Gateway lifecycle are changed.

These are stored configuration and recorded permissions, not effective tool
access or live capability tests. Manifest versions do not attest to every module
already loaded in memory. The central Node Comparison table masks observations
older than eleven minutes as Stale and preserves every registered inventory row.

Reading macOS's protected TCC database requires Full Disk Access for the agent's
configured Node executable (`config/local.json` → `nodePath`), separately from
the Gateway executable being observed. Approve that exact path in System Settings
→ Privacy & Security → Full Disk Access. The monitor does not need Accessibility
or Screen Recording itself. Verify the next scheduled observation has permission
collection status `ok`; a successful SSH read alone does not prove scheduled access.

All are system LaunchDaemons with `UserName` set to the ordinary node account: they do
not depend on GUI login. An existing repaired Background Codex LaunchAgent is
preserved during updates; this release does not migrate other hosts to that layout.
One hung OpenClaw/SQLite/Codex check cannot block heartbeat.
Subprocesses have deadlines, output limits and whole-process-group cleanup. Session SQL
runs in a killable child, uses read-only WAL-aware sources, and preserves the existing
hashed-identity SQLite ledger. See [session semantics](docs/sessions-contract.md).

The Collector alone sends authenticated telemetry centrally. Schema 3, metric names,
labels, error/freshness semantics, source attributes and exporter names remain compatible
with existing central dashboards/alerts. No central changes are required.

## Paths

- npm prefix: a dedicated directory, e.g. `$HOME/.local/fleet-node-npm`.
- state root: `$HOME/.local/share/fleet-node` (override with `--root`).
- state root contains `config`, `secrets`, `queue`, `state/sessions`, `textfiles`,
  `logs`, `bin` (checksum-pinned native dependencies), and `services`.
- explicit independent Node and npm paths are recorded in `config/local.json`.
- central secret-free intent stays exactly four fields in `config/node.json`:

```json
{
  "config_schema_version": 3,
  "node_label": "bill",
  "telemetry_endpoint": "https://telemetry.example.com",
  "codex_usage_enabled": true
}
```

Credentials are mode 0600 in a private directory. OpenClaw gets only the existing
loopback OTLP endpoint, never the central credential. This is a same-UID trust boundary,
not isolation from other code running as the node account.

## Install

Use a supported, explicitly selected **Node 24.18 or newer within major 24** outside
OpenClaw. Do not rely on shell startup files, `env node`, or a moving Homebrew default.
Run npm and every CLI command through that Node executable. For example:

```sh
FLEET_NODE=/opt/homebrew/opt/node@24/bin/node
FLEET_NPM=/opt/homebrew/opt/node@24/lib/node_modules/npm/bin/npm-cli.js
FLEET_PREFIX="$HOME/.local/fleet-node-npm"
FLEET_ROOT="$HOME/.local/share/fleet-node"
FLEET_CLI="$FLEET_PREFIX/lib/node_modules/@unblocklabs/fleet-node/dist/cli.js"

"$FLEET_NODE" "$FLEET_NPM" install --global --prefix "$FLEET_PREFIX" \
  --ignore-scripts /absolute/path/unblocklabs-fleet-node-0.1.4.tgz

"$FLEET_NODE" "$FLEET_CLI" init --root "$FLEET_ROOT" \
  --config /absolute/private/node-agent.json \
  --node "$FLEET_NODE" --npm-cli "$FLEET_NPM" --prefix "$FLEET_PREFIX" \
  --openclaw /absolute/path/to/openclaw --codex /absolute/path/to/codex \
  --token-file /absolute/private/ingest-token
"$FLEET_NODE" "$FLEET_CLI" prepare --root "$FLEET_ROOT"
sudo /bin/sh "$FLEET_ROOT/services/install-system.sh"
"$FLEET_NODE" "$FLEET_CLI" start --root "$FLEET_ROOT"
```

`prepare` downloads upstream archives, verifies hardcoded SHA-256 pins, validates the
Collector configuration, and generates service definitions. It never bootstraps services
itself. For offline installation supply `--collector-archive` and `--exporter-archive`.
Disabled Codex jobs exit before calling Codex; installing all fixed labels once allows
later capability changes without more sudo.

For an existing managed node, first follow [migration](docs/migration.md). Do not run
old and new services together: they bind the same ports and would duplicate telemetry.

OpenClaw is optional for agent operation. For a **new** producer, explicitly run
`configure-openclaw` after reviewing its configuration. This backs up and atomically sets
loopback OTLP while preserving `captureContent`; it does not install OpenClaw extensions,
change authentication, or restart the gateway. Verify the official diagnostics exporter
is available in that OpenClaw installation before claiming logs/traces/metrics work.

## Update and rollback — ordinary user, npm owns versions

Keep a copy of the known-good npm tarball. These are ordinary npm version changes, not
custom release slots. With the variables above:

```sh
"$FLEET_NODE" "$FLEET_CLI" stop --root "$FLEET_ROOT" &&
"$FLEET_NODE" "$FLEET_NPM" install --global --prefix "$FLEET_PREFIX" \
  --ignore-scripts /absolute/path/new-version.tgz &&
"$FLEET_NODE" "$FLEET_CLI" prepare --root "$FLEET_ROOT" &&
"$FLEET_NODE" "$FLEET_CLI" validate --root "$FLEET_ROOT" &&
"$FLEET_NODE" "$FLEET_CLI" start --root "$FLEET_ROOT"
```

Once published, an exact `@unblocklabs/fleet-node@VERSION` works in place of the tarball.
Do not use a floating version for a controlled fleet change. **Do not skip `stop`:** npm
updates are non-atomic and launchd KeepAlive can restart a killed process immediately.
The stable external launcher checks a durable gate twice, before package entry; `stop`
closes it and verifies old jobs and their descendants are gone. Failed npm/validation
leaves monitoring stopped, not running a partial installation.

Rollback uses the same sequence, replacing `new-version.tgz` with the saved prior tarball.
If the failed npm operation removed the CLI, the gate is already closed: reinstall the
prior tarball with the explicit Node/npm paths, then prepare/validate/start. **Never
restore old queues or session history for an npm rollback**—retain current external state.
Native pins must remain queue-format-compatible for rollback; a future incompatible
state-format change requires a separate, explicit migration.

`restart` performs the same gate-close/quiesce/gate-open cycle, without sudo. Long-running
jobs recover on launchd's next throttled retry; checks resume on their own intervals
(up to five minutes). A loaded one-shot need not have a running PID between checks.

## Native tool evidence (opt-in)

After an approved canary/rollout, enable the existing five-minute native evidence
collector by writing `v1` to private `config/evidence.enabled` in the runtime root.
It discovers managed Codex homes across agents and forwards complete, best-effort
redacted tool outputs through the existing authenticated pipeline. It does not
collect messages, prompts or tool arguments. Other harnesses and compressed
archives remain unsupported. Removing the marker disables future runs without
deleting checkpoints or history. See [the evidence contract](docs/evidence-contract.md).

## Verify

```sh
"$FLEET_NODE" "$FLEET_CLI" --version
"$FLEET_NODE" "$FLEET_CLI" status --root "$FLEET_ROOT"
"$FLEET_NODE" "$FLEET_CLI" validate --root "$FLEET_ROOT"
curl -fsS http://127.0.0.1:13133/
curl -fsS http://127.0.0.1:9100/metrics
```

Check completed occurrence timestamps, not just process existence: heartbeat/readiness
must be within 120 seconds and not over 30 seconds future; cron/session snapshots within
600 seconds with success 1. Missing Codex windows are unknown, never zero. Queue-age
metrics mean continuous nonempty observation age, not the age of a particular record.
Finally check authenticated Bill metrics, logs and traces in Charizard's stores; Collector
health alone is not delivery proof.

## Development / packaging

With the supported Node directory first in this development shell's PATH:

```sh
npm ci
npm test
npm pack
```

`tests/packed.test.mjs` exercises compiled tarball installation and state-preserving npm
upgrade/rollback in a disposable prefix. Native macOS dependency/service validation is
separate from mock subprocess tests. No registry publication, fleet-wide deployment,
or production-readiness claim is implied by a passing unit suite.
