# Push-Only Central Integration Plan

This plan moves the node package and Charizard from topology-specific push/pull paths to one
outbound OTLP/HTTP contract. The `0.2.0` implementation and central schema-v2 shadow path are ready
locally; publication, canary installation, Cloudflare changes, and production cutover are not.

## Target

- Charizard exposes one canonical HTTPS OTLP/HTTP endpoint through Cloudflare Tunnel.
- Charizard registers each node and returns one independently revocable ingest credential.
- Every node installs the same runtime and configuration regardless of physical location.
- OpenClaw pushes logs, metrics, and traces directly.
- A node-local collector scrapes `127.0.0.1` node_exporter and its textfile metrics, then pushes them
  through the same authenticated OTLP pipeline.
- Charizard derives trusted node identity from authentication and never scrapes a fleet node.
- A local Charizard route, if later required, changes only DNS or endpoint routing. It does not
  introduce a LAN mode in node code.

## Phase 1: freeze and measure the current contract

- Inventory every metric family and label consumed by dashboards and alerts.
- Record current scrape cadence, freshness, offline-alert timing, and resource use.
- Keep the `0.1.x` LAN/off-LAN installers stable while the replacement is developed.
- Do not add new topology-specific configuration or per-node tunnels.

## Phase 2: stabilize one central ingress

- Choose a telemetry-oriented canonical hostname rather than a signal-specific name such as
  `loki-ingest`.
- Prove OTLP/HTTP logs, metrics, and traces through the Cloudflare-backed route.
- Reuse the existing per-node Basic credential initially if that minimizes migration risk.
- Ensure the authenticated credential, not resource attributes supplied by the node, determines
  canonical `node_label`.
- Define rotation, revocation, retry, rate-limit, and rejected-auth observability behavior.

## Phase 3: build one node runtime — locally complete

- Replace public LAN/off-LAN entrypoints with one installer and one node configuration.
- Keep node_exporter and current textfile collectors initially to avoid changing metric production
  and transport simultaneously.
- Add a local collector/exporter that scrapes node_exporter only on loopback and exports OTLP/HTTP.
- Send a bounded heartbeat or last-seen signal suitable for central offline detection.
- Do not require Cloudflare Access credentials, exporter scrape tokens, LAN addresses, tunnel
  hostnames, or a `network` selector in the durable node configuration.
- Use OpenTelemetry Collector Contrib 0.157.0 with a loopback OTLP receiver, local Prometheus
  scrapes, signal-specific bounded persistent queues, gzip, finite retries, a memory limiter,
  byte-capped outbound requests, self-metrics, and an independent occurrence-timestamp heartbeat
  queue.

## Phase 4: dual-run without double-counting — next production boundary

Use at least one LAN node and one currently remote node, including Theo:

1. Run the existing central Prometheus pull and the new node-side OTLP push simultaneously.
2. Write pushed canary series to distinguishable jobs or labels during comparison.
3. Compare metric families, label sets, values, timestamps, staleness, collector errors, and
   resource consumption.
4. Exercise token rotation/revocation, node restart, temporary network loss, Charizard restart, and
   Cloudflare/Internet interruption.
5. Require existing dashboards and alerts to produce equivalent results before cutover.

## Phase 5: cut over and retire compatibility paths

- Move dashboards and alerts to push-derived host metrics and heartbeat semantics.
- Stop generating LAN HTTP-SD and off-LAN Prometheus scrape jobs.
- Remove per-node exporter tunnels, token proxies, and exporter scrape credentials.
- Remove topology selection from installers and generated node configuration.
- Close unnecessary node monitoring listeners and direct Charizard LAN ingest exposure after the
  rollback window is accepted.
- Retain one bounded rollback release and document the end date for its compatibility paths.

## Acceptance criteria

- LAN and remote nodes use the same release, services, configuration shape, endpoint contract, and
  credential type.
- Moving between networks requires no reinstall or credential rotation.
- All expected OpenClaw and host metric families arrive through authenticated OTLP/HTTP.
- Trusted identity, labels, freshness, and dashboard meaning match the existing system.
- Offline detection meets the existing alerting objective without relying on Prometheus scrape
  `up` for a fleet node.
- No node exposes an inbound fleet monitoring service or has a per-node public tunnel hostname.
- Failure and rollback behavior has been demonstrated on one LAN and one remote node.

## Repository boundary

Node repository:

- unified installer and runtime
- local collectors and node_exporter integration
- OTLP export, retry, and heartbeat behavior
- node-side configuration and compatibility tests

Central repository:

- node registration, lifecycle, and credential issuance/revocation
- canonical ingress and Cloudflare Tunnel configuration
- authenticated identity normalization and OTLP metric storage
- dashboards, alerts, migration comparison, and live validation
