# OpenClaw OTLP Configuration

`openclaw-otel` tooling prepares OpenClaw local diagnostic shipping in a way
that is compatible with central Alloy identity normalization.

Status: the commands below describe the `0.1.x` compatibility surface. In `0.2.0`,
`configure-openclaw-local-otel --config <node-agent.json>` writes only the loopback endpoint and no
headers. `install-fleet-node-agent` runs it after proving the Collector is healthy. The Collector,
not OpenClaw, owns the Charizard credential and network connection.

The unified installer derives `~/.openclaw/openclaw.json` from the real home of its explicit
`--node-user`; schema 2 central config cannot choose or override that path. The commands below remain
documentation for the older topology-specific compatibility surface.

When `configure-openclaw-local-otel` is used directly with schema 2, it derives the current
unprivileged account's real home and fails under root. Root provisioning must go through
`install-fleet-node-agent --node-user <account>`.

## Current writer and evidence stop

The retained, locally tested config-mutation path is the repository-owned writer used by
`configure-openclaw-local-otel`. It still hardcodes `captureContent=true`; if the owner selects the
recommended `false`, the writer and its tests must change before any canary. It:

- rejects non-object `diagnostics` and `diagnostics.otel` values;
- preserves unrelated OpenClaw settings;
- creates a timestamped, mode-`0600` backup of an existing config unless explicitly disabled;
- writes the replacement JSON atomically, fsyncs it, and leaves mode `0600`;
- points OpenClaw at the loopback Collector; and
- replaces `diagnostics.otel.headers` with `{}`, keeping the Charizard credential only in the
  Collector's protected authorization-header file.

Phase 4 did not prove that a native `openclaw config` patch is equivalent. Keep the custom writer
until all of these gaps are closed:

- A compatible OpenClaw runtime and diagnostics-otel plugin pair is identified and exercised.
  `v2026.4.29` is only the floor for the required config command; it does not prove that telemetry
  is emitted by the runtime/plugin pair.
- A native patch uses `--replace-path diagnostics.otel.headers`. An ordinary merge is insufficient
  because it may retain a stale Authorization or Cloudflare header.
- Native backup behavior is proved equivalent to the existing timestamped pre-edit mode-`0600`
  backup, or a reviewed replacement/rollback contract is adopted explicitly.
- `diagnostics.otel.captureContent` receives an owner-approved privacy decision. Current behavior is
  `true`; the recommended pre-delegation default is `false` because captured content can include
  message or tool payloads.
- OpenClaw is restarted after the change, a diagnostic event is generated, and actual logs,
  metrics, and traces are observed through the local Collector and at authenticated Charizard
  storage. Config acceptance and Collector health alone are not receipt evidence.

This is a release and delegation gate. It does not authorize a runtime change in this phase.

## Unified `0.2.0` target

OpenClaw sends OTLP/HTTP to the node-local Collector on `127.0.0.1:4318` with no central headers.
The Collector alone reads the protected Charizard Authorization header and owns the outbound HTTPS
connection. Physical LAN/off-LAN location does not change this protocol or credential boundary.

## `0.1.x` compatibility goal

All fleet nodes should send OpenClaw diagnostics to a configured OTLP endpoint
using a deterministic auth shape:

- `Authorization: Basic <base64(node_label:ingest_token)>`
- `OTEL_EXPORTER_OTLP_HEADERS` encoded with:
  - `Authorization` (same as above)
  - `CF-Access-Client-Id` for off-LAN nodes
  - `CF-Access-Client-Secret` for off-LAN nodes
- optional Cloudflare Access headers in off-LAN mode.

## `0.1.x` compatibility commands

Generate environment variables only:

```bash
export FLEET_INGEST_TOKEN="TOKEN"
./bin/print-otlp-env --config /tmp/node-config.lan.json
```

Write OpenClaw runtime config in place:

```bash
export FLEET_INGEST_TOKEN="TOKEN"
./bin/configure-openclaw-otel --config /tmp/node-config.lan.json
```

For off-LAN deployments, include access headers:

```bash
export FLEET_INGEST_TOKEN="TOKEN"
export CF_ACCESS_CLIENT_ID="..."
export CF_ACCESS_CLIENT_SECRET="..."
./bin/print-otlp-env --config /tmp/node-config.off-lan.json
```

The same values can be passed explicitly:

```bash
export FLEET_INGEST_TOKEN="TOKEN"
./bin/configure-openclaw-otel \
  --node-label mini-03 \
  --network off_lan \
  --endpoint https://loki-ingest.example.com \
  --service-name openclaw_gateway \
  --cf-access-client-id "$CF_ACCESS_CLIENT_ID" \
  --cf-access-client-secret "$CF_ACCESS_CLIENT_SECRET"
```

## Current compatibility config shape

- When `~/.openclaw/openclaw.json` already exists, a timestamped backup is created before editing it;
  a first-time config creation has no source file to back up.
- Diagnostics keys are enabled:
  - `diagnostics.enabled=true`
  - `diagnostics.otel.enabled=true`
  - `diagnostics.otel.protocol=http/protobuf`
  - `diagnostics.otel.logs=true`
  - `diagnostics.otel.captureContent=true`
  - `diagnostics.otel.headers.Authorization=<basic>`
- Existing unrelated OpenClaw settings are preserved.

The `captureContent=true` line above records current behavior, not the recommended target. Resolve
the privacy decision and prefer `false` before delegating or installing `0.2.0` on fleet nodes.

## Compatibility rules

- Do not inject `node`, `node_label`, `host.name`, or `display_name` as resource
  attributes in this layer.
- Keep header shape stable unless coordinated with central Alloy transforms and
  dashboards.
- Keep token material out of shell history. Prefer temporary environment
  variables, config files without token material, and explicit `unset` after use.
  `--token` remains available for controlled automation, but it can appear in
  shell history and process listings.
- Do not add new behavior keyed by `lan` or `off_lan`. The next breaking node release should accept
  one endpoint and one node credential regardless of location.

## Validation

```bash
cat ~/.openclaw/openclaw.json | jq '.diagnostics'
```

```bash
openclaw --version
```

```bash
grep -R "diagnostics.otel" -n "$HOME/.openclaw/openclaw.json"
```
