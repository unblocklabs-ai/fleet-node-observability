# Changelog

## 0.2.0 - Unreleased pre-cutover implementation

- Added one topology-independent `install-fleet-node-agent` entrypoint and node
  configuration shape for `pull`, `dual`, and `push` rollout states.
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
- Kept the `0.1.x` LAN/off-LAN commands as documented rollback compatibility.

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
