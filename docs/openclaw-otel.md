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

## Goal

All fleet nodes should send OpenClaw diagnostics to a configured OTLP endpoint
using a deterministic auth shape:

- `Authorization: Basic <base64(node_label:ingest_token)>`
- `OTEL_EXPORTER_OTLP_HEADERS` encoded with:
  - `Authorization` (same as above)
  - `CF-Access-Client-Id` for off-LAN nodes
  - `CF-Access-Client-Secret` for off-LAN nodes
- optional Cloudflare Access headers in off-LAN mode.

## Command patterns

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

## Expected generated config shape

- `~/.openclaw/openclaw.json` backup is created first (timestamped file).
- Diagnostics keys are enabled:
  - `diagnostics.enabled=true`
  - `diagnostics.otel.enabled=true`
  - `diagnostics.otel.protocol=http/protobuf`
  - `diagnostics.otel.logs=true`
  - `diagnostics.otel.captureContent=true`
  - `diagnostics.otel.headers.Authorization=<basic>`
- Existing unrelated OpenClaw settings are preserved.

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
