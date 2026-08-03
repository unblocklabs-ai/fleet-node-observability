# Changelog

## 0.2.1 - 2026-08-02

- Fixed release installation permissions so the unprivileged node service account can traverse the
  installed release root while the root installer copies runtime helpers.

## 0.2.0 - 2026-08-02

- Added one node installer and one exact schema-3 intent containing node label, canonical HTTPS
  telemetry endpoint, and Codex usage capability. Accounts, paths, architecture, package locations,
  and loopback settings are resolved locally.
- Dropped only two low-severity structured routine-success log shapes before queueing or network
  transfer: successful gateway authentication and successful tool-policy removal. Body-prefix
  filtering is absent, so task output, status updates, near misses, WARN-or-higher records, and
  failure variants remain intact. QMD and Codex source noise is deferred until upstream supplies
  stable structured discriminators for capture-off telemetry.
- Consolidated host and heartbeat collection onto one 30-second node_exporter scrape with filtered,
  independently queued pipelines; reduced Collector self-scraping to 30 seconds and made queue
  inspection a single metrics pass.
- Removed the unused `fleet.claimed_node` resource attribute and the Codex account-email metric
  label. Authenticated node identity remains authoritative; `account_domain` is a lower-cardinality
  client claim.
- Added a pinned-Collector runtime smoke test for log filtering and shared scrape routing, expanded
  all-exporter queue-parser coverage, and documented the trusted single-user node-account boundary.
- Pinned OpenTelemetry Collector Contrib 0.157.0 for Intel and Apple Silicon
  macOS, with SHA-256 verification and config validation before launch.
- Routed OpenClaw through a loopback-only OTLP/HTTP receiver so the Collector is
  the only node process that owns central telemetry network requests.
- Added gzip, signal-specific batching, bounded disk-backed queues, finite
  retries, one-consumer replay caps, a memory limiter, health endpoint, and
  Collector self-metrics.
- Added an occurrence-timestamp heartbeat with an independent scrape pipeline
  and queue so log pressure cannot hide node-agent liveness.
- Moved the central credential into one protected full-Authorization-header
  file; OpenClaw configuration contains neither the credential nor Cloudflare
  Access headers.
- Reduced Codex usage collection to the supported `codex app-server`
  `account/read` and `account/rateLimits/read` methods. The collector no longer
  reads or writes Codex OAuth files, calls a private ChatGPT endpoint, or
  reconstructs account usage from session transcripts.
- Kept the hardened OpenClaw JSON writer, replacing the complete header object, making private
  timestamped backups, writing atomically, and disabling content capture.
- Removed the unreleased transition states and the older topology-specific installers, direct
  central OpenClaw exporter, public exporter tunnel support, scrape credential and proxy, and
  migration-only cleanup commands from the active release.

## 0.1.3 - 2026-07-07

- Hardened off-LAN root writes by staging runtime replacement before rename and
  atomically replacing root-written textfile and cron backup files after
  immediate path revalidation.
- Hardened release installs to reject symlinked install targets or parents and
  stage release contents in a sibling directory before renaming into place.
- Standardized Prometheus label escaping across Python collectors, shell
  collectors, and installers: escape backslash, quote, newline, tab, and CR;
  strip remaining C0 controls and DEL.

## 0.1.2 - 2026-07-07

- Hardened off-LAN installer user-home path handling for root-created runtime,
  secret, log, and cron backup paths.
- Escaped newline/tab/CR and stripped other control characters from installer
  Prometheus label values.
- Escaped C0 control characters in OpenClaw gateway health JSON and stripped
  unsupported C0 controls from Prometheus labels.
- Documented exact macOS thermal textfile metric labels.

## 0.1.1 - 2026-07-07

- Hardened `configure-openclaw-otel` writes so OpenClaw config, backups, and
  temporary files are written with `0600` permissions and atomic fsync/replace.
- Hardened off-LAN installer managed-path validation against symlinked parents
  and revalidated listener ownership before force-killing stale ports.
- Accepted central-rendered boolean spellings for `--codex-usage-enabled` in
  LAN and off-LAN installers.
- Tightened node_exporter proxy token checks with constant-time comparison and
  duplicate token-header rejection.
- Escaped control characters in OpenClaw gateway health JSON and Prometheus
  labels.
- Fixed OTLP example config/docs consistency, removed unused textfile/path
  helpers, and clarified node identity/account email textfile label policy.

## 0.1.0 — 2026-06-23

- Initial docs-first scaffold for a node-only observability package.
- Added practical onboarding and troubleshooting documentation:
  - `docs/install-lan-node.md`
  - `docs/install-off-lan-node.md`
  - `docs/openclaw-otel.md`
  - `docs/textfile-metrics.md`
  - `docs/release-compatibility.md`
  - `docs/troubleshooting.md`
  - `docs/central-integration-plan.md`
- Added sanitized example config inputs:
  - `examples/node-config.lan.example.json`
  - `examples/node-config.off-lan.example.json`
  - `examples/openclaw-otel.env.example`
- Added local-first release packaging scripts:
  - `packaging/build-release.sh`
  - `packaging/install-from-release.sh`
- Added project metadata:
  - `VERSION`
  - `LICENSE` (MIT)
