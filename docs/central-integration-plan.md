# Central Integration Plan (No Central Repo Edits in This Scope)

This plan defines how this node package should integrate with the existing
central fleet observability stack without editing central sources.

## Phase 1 — Contract freeze

- Use stable metric names and label sets from `docs/textfile-metrics.md`.
- Confirm central scrape config accepts:
  - node_exporter textfile metrics
  - launchd-based collectors
  - `/metrics` proxy path with token header
- Document minimum OTLP header requirements:
  - Basic auth from node label and ingest token
  - optional Cloudflare Access headers for off-LAN

## Phase 2 — Central config alignment

Central operators should publish:

- per-node `node_label`, user, and export token
- optional off-LAN ingress endpoint and tunnel hostname
- collector allow-lists for the expected metric names

Node config can remain simple (`examples/node-config.*`) and generated per machine.

## Phase 3 — Rollout playbook

For each node:

1. Distribute a minimal sanitized node config file.
2. Run LAN or off-LAN install path.
3. Configure OpenClaw OTLP config (or print-env for staging).
4. Run first scrape checks:
   - local `/metrics`
   - node_exporter proxy 403/200 behavior
   - dashboard visibility of `openclaw_gateway_ready`
5. Run fallback cleanup if legacy shippers are present.

## Phase 4 — Upgrade strategy

- Release in patch windows first, then minor, then any major break points.
- Keep compatibility notes in:
  - `CHANGELOG.md`
  - `docs/release-compatibility.md`
- Avoid changing central scrape ownership or alert thresholds in the same release
  as collector contract changes.

## Phase 5 — Verification

During rollout:

- Validate at least one node from each topology (LAN / off-LAN).
- Capture proof:
  - `launchctl` state
  - successful `/metrics` fetch
  - token-protected proxy check
  - successful OTLP helper output check
- Escalate only after config and token checks are complete.

## Responsibilities split

Node repo:
- install scripts
- collectors
- OTLP helper scripts
- textfile metric contracts

Central repo:
- dashboards and alerts
- Alloy transform/targets
- token issuance and revocation
- policy for off-LAN ingress and DNS names
