# Repository Structure Plan

Status: historical extraction and `0.1.x` layout record. The repository boundary remains valid, but
separate LAN/off-LAN commands shown below are compatibility surfaces scheduled to converge on one
installer and one outbound OTLP runtime. See `docs/central-integration-plan.md` for the current
product direction.

The current `0.2.0` additions are:

```text
bin/
  install-fleet-node-agent
  configure-openclaw-local-otel
  render-fleet-node-agent-config
  write-fleet-node-agent-secret
src/fleet_node_observability/
  agent.py
  commands/{configure_openclaw_local_otel,render_agent_config,write_agent_secret}.py
  installers/fleet_node_agent.sh
  collectors/fleet_node_agent_heartbeat.sh
examples/node-agent.example.json
```

The older structure and topology-specific examples below are retained as extraction history, not as
the preferred interface.

This document describes the target folder and file structure for
`fleet-node-observability`.

The goal is to keep the node artifact small, installable, testable, and clearly
separate from the central `fleet-observability` control plane.

## Design Principles

- Public commands live in one obvious place.
- Reusable Python logic is importable and tested directly.
- Node-local runtime files are distinct from development and release tooling.
- Central fleet inventory, dashboards, Alloy, Prometheus, Loki, Tempo, and
  Grafana code do not appear in this repo.
- The node package can be installed from a GitHub release tarball without
  requiring the central repo checkout.
- The target node needs macOS built-ins, Homebrew `node_exporter`, Python 3, the
  release artifact, and the installer-verified pinned Collector binary.

## Recommended Top-Level Layout

```text
fleet-node-observability/
  README.md
  LICENSE
  CHANGELOG.md
  VERSION
  .gitignore
  bin/
  src/
    fleet_node_observability/
  tests/
  docs/
  packaging/
  examples/
```

### `bin/`

Stable command surface for operators and central onboarding instructions.

These files should be executable and should remain backward-compatible whenever
possible. They may be shell scripts or thin Python entrypoint wrappers, but the
implementation should live under `src/` when it is non-trivial.

Target files:

```text
bin/
  install-lan-host-metrics
  install-off-lan-host-metrics
  configure-openclaw-otel
  print-otlp-env
  cleanup-legacy-shippers
  collect-codex-usage
  collect-macos-thermal
  openclaw-gateway-health
  fleet-node-exporter-proxy
```

What goes here:

- user-facing install commands.
- user-facing configure/print commands.
- collector commands that LaunchAgents or LaunchDaemons call.
- compatibility wrappers for old command names if needed.

What does not go here:

- shared helper code.
- test fixtures.
- central inventory or generated config.

Suggested mapping from current `fleet-observability` files:

```text
scripts/install-node-exporter.sh          -> bin/install-lan-host-metrics
scripts/install-off-lan-host-metrics.sh   -> bin/install-off-lan-host-metrics
scripts/configure-openclaw-otel.py        -> bin/configure-openclaw-otel
scripts/print-otlp-env.py                 -> bin/print-otlp-env
scripts/cleanup-node-legacy-shippers.sh   -> bin/cleanup-legacy-shippers
scripts/codex-usage-collector.py          -> bin/collect-codex-usage
scripts/collect-macos-thermal.py          -> bin/collect-macos-thermal
scripts/openclaw-gateway-health.sh        -> bin/openclaw-gateway-health
scripts/fleet-node-exporter-proxy.py      -> bin/fleet-node-exporter-proxy
```

### `src/fleet_node_observability/`

Importable Python implementation code. Keep this stdlib-only unless a dependency
is intentionally introduced and documented.

Target structure:

```text
src/fleet_node_observability/
  __init__.py
  config.py
  otlp.py
  textfile.py
  paths.py
  commands/
    __init__.py
    configure_openclaw_otel.py
    print_otlp_env.py
    collect_codex_usage.py
    collect_macos_thermal.py
    node_exporter_proxy.py
  installers/
    lan_host_metrics.sh
    off_lan_host_metrics.sh
    cleanup_legacy_shippers.sh
  collectors/
    openclaw_gateway_health.sh
```

What goes here:

- node-local config loading and validation.
- OTLP header formatting.
- OpenClaw JSON config update logic.
- Codex usage collection logic.
- macOS thermal parsing and Prometheus rendering.
- node_exporter proxy implementation.
- shared textfile atomic-write helpers.
- shared launchd plist rendering helpers if installer complexity grows.

Why not keep everything under `scripts/`:

- The old central repo used `scripts/` because it mixed operator commands,
  validators, generators, and migration tools.
- This repo has a smaller product boundary. A command/API split will make tests,
  release packaging, and compatibility clearer.

### `src/fleet_node_observability/config.py`

Owns node-local configuration.

Responsibilities:

- parse `--config` JSON.
- merge config values with explicit CLI flags.
- validate required fields for LAN and off-LAN modes.
- keep defaults in one place.
- avoid reading private central `fleet/nodes.json`.

Expected config fields:

```json
{
  "node_label": "mini_03",
  "user": "fleet-mini-03",
  "home": "/Users/fleet-mini-03",
  "network": "off_lan",
  "node_exporter_port": 9100,
  "node_exporter_tunnel_hostname": "node-exporter-mini-03.example.net",
  "openclaw_ready_url": "http://127.0.0.1:18789/readyz",
  "openclaw_service_name": "openclaw_gateway",
  "otlp_http_endpoint": "https://loki-ingest.example.com",
  "codex_usage_enabled": true,
  "codex_profile": "default",
  "codex_usage_interval_secs": 300
}
```

### `src/fleet_node_observability/otlp.py`

Owns OpenClaw OTLP auth/config helpers.

Responsibilities:

- normalize node labels.
- build `Authorization: Basic ...`.
- percent-encode `OTEL_EXPORTER_OTLP_HEADERS`.
- require Cloudflare Access headers for off-LAN mode.
- avoid client-side `node`, `node_label`, `host.name`, or `display_name`
  resource identity injection.

Moved/reimplemented from:

- selected pieces of `scripts/fleetlib.py`.
- `scripts/print-otlp-env.py`.
- `scripts/configure-openclaw-otel.py`.

### `src/fleet_node_observability/textfile.py`

Shared Prometheus textfile helpers.

Responsibilities:

- label escaping.
- atomic textfile writes.
- common metric rendering helpers.

Used by:

- Codex usage collector.
- macOS thermal collector.
- OpenClaw gateway health collector if rewritten in Python later.

### `src/fleet_node_observability/installers/`

Installer implementation files.

The current installer scripts are shell-heavy because they interact with macOS,
Homebrew, launchctl, chown/chmod, lsof, and curl. Keeping the install bodies as
shell is reasonable. Put them here and expose stable wrappers from `bin/`.

Target files:

```text
src/fleet_node_observability/installers/
  lan_host_metrics.sh
  off_lan_host_metrics.sh
  cleanup_legacy_shippers.sh
```

### `src/fleet_node_observability/collectors/`

Non-Python collector implementation files.

Target files:

```text
src/fleet_node_observability/collectors/
  openclaw_gateway_health.sh
```

If this collector is later rewritten in Python, move the implementation to
`commands/` or a dedicated module and leave `bin/openclaw-gateway-health` as the
stable executable.

## `tests/`

Tests should mirror product boundaries, not the old central migration history.

Target structure:

```text
tests/
  fixtures/
    node-config-lan.json
    node-config-off-lan.json
    codex/
  test_config.py
  test_otlp.py
  test_openclaw_otel_config.py
  test_codex_usage_collector.py
  test_macos_thermal_collector.py
  test_node_exporter_proxy.py
  test_installers_contract.py
  test_cleanup_legacy_shippers_contract.py
```

What goes here:

- unit tests for Python modules.
- static contract tests for shell installers.
- fixtures for node-local config.
- command-output tests for OTLP helper commands.
- metric-name and label stability tests.

What does not go here:

- Grafana dashboard tests.
- Prometheus rule tests.
- Alloy transform tests.
- Loki/Tempo/Grafana live validators.
- central migration audit tests.

## `docs/`

Human-facing operator and developer documentation.

Current docs:

```text
docs/
  central-contract.md
  extraction-catalog.md
  repository-structure.md
```

Recommended docs after extraction:

```text
docs/
  install-lan-node.md
  install-off-lan-node.md
  openclaw-otel.md
  textfile-metrics.md
  release-compatibility.md
  troubleshooting.md
```

What goes here:

- install and upgrade workflows.
- node-local metric contracts.
- OpenClaw OTLP config examples.
- release compatibility matrix with central stack versions.
- manual verification commands that run on the node.

What does not go here:

- full Charizard deployment docs.
- dashboard audit results.
- central migration reports.

## `examples/`

Checked-in, sanitized example inputs.

Target files:

```text
examples/
  node-config.lan.example.json
  node-config.off-lan.example.json
  openclaw-otel.env.example
  launchd/
    com.example.fleet-node-exporter.plist.example
```

What goes here:

- fake node labels and endpoints.
- sample config shape.
- non-secret examples.

What does not go here:

- real tokens.
- real Cloudflare Access credentials.
- private hostnames unless intentionally public.

## `packaging/`

Release and install artifact support.

Target files:

```text
packaging/
  build-release.sh
  install-from-release.sh
  checksums.sh
  homebrew/
    fleet-node-observability.rb
```

What goes here:

- scripts that assemble release tarballs.
- checksum generation.
- optional Homebrew formula template.
- installer bootstrap scripts.

This folder should not be needed to run the node tools from a checkout.

## Files To Avoid

Do not introduce these into the node repo:

```text
fleet/nodes.json
fleet/secrets/
grafana/
alloy/
prometheus/
loki/
tempo/
docker-compose.yml
current-migration-status.html
output/
.runtime/
.playwright-cli/
.playwright-mcp/
```

Those are central-control-plane or local runtime artifacts.

## First Extraction Milestone Layout

The first practical milestone does not need every target directory. It should be
small and useful:

```text
fleet-node-observability/
  README.md
  VERSION
  bin/
    collect-codex-usage
    collect-macos-thermal
    openclaw-gateway-health
    fleet-node-exporter-proxy
  src/
    fleet_node_observability/
      __init__.py
      textfile.py
      commands/
        collect_codex_usage.py
        collect_macos_thermal.py
        node_exporter_proxy.py
      collectors/
        openclaw_gateway_health.sh
  tests/
    test_codex_usage_collector.py
    test_macos_thermal_collector.py
    test_node_exporter_proxy.py
```

After that passes, add OTLP helpers, then installers.

## Final Extraction Milestone Layout

The final migration target should look like this:

```text
fleet-node-observability/
  README.md
  LICENSE
  CHANGELOG.md
  VERSION
  bin/
    install-lan-host-metrics
    install-off-lan-host-metrics
    configure-openclaw-otel
    print-otlp-env
    cleanup-legacy-shippers
    collect-codex-usage
    collect-macos-thermal
    openclaw-gateway-health
    fleet-node-exporter-proxy
  src/
    fleet_node_observability/
      __init__.py
      config.py
      otlp.py
      textfile.py
      paths.py
      commands/
        __init__.py
        configure_openclaw_otel.py
        print_otlp_env.py
        collect_codex_usage.py
        collect_macos_thermal.py
        node_exporter_proxy.py
      installers/
        lan_host_metrics.sh
        off_lan_host_metrics.sh
        cleanup_legacy_shippers.sh
      collectors/
        openclaw_gateway_health.sh
  tests/
    fixtures/
      node-config-lan.json
      node-config-off-lan.json
    test_config.py
    test_otlp.py
    test_openclaw_otel_config.py
    test_codex_usage_collector.py
    test_macos_thermal_collector.py
    test_node_exporter_proxy.py
    test_installers_contract.py
    test_cleanup_legacy_shippers_contract.py
  docs/
    central-contract.md
    extraction-catalog.md
    repository-structure.md
    install-lan-node.md
    install-off-lan-node.md
    openclaw-otel.md
    textfile-metrics.md
    release-compatibility.md
    troubleshooting.md
  examples/
    node-config.lan.example.json
    node-config.off-lan.example.json
    openclaw-otel.env.example
  packaging/
    build-release.sh
    install-from-release.sh
```

## Compatibility Wrapper Policy

The old central repo command names should not be the long-term public API, but
compatibility wrappers can reduce migration risk.

Possible temporary wrappers:

```text
scripts/install-node-exporter.sh        -> bin/install-lan-host-metrics
scripts/install-off-lan-host-metrics.sh -> bin/install-off-lan-host-metrics
scripts/configure-openclaw-otel.py      -> bin/configure-openclaw-otel
scripts/print-otlp-env.py               -> bin/print-otlp-env
```

If wrappers are added, keep them thin and mark them as compatibility shims in
the docs.

## Why This Structure

This layout separates:

- operator command surface: `bin/`
- reusable implementation: `src/fleet_node_observability/`
- tests and contracts: `tests/`
- install docs: `docs/`
- sanitized examples: `examples/`
- release plumbing: `packaging/`

That makes it easier to build a small release artifact while still keeping the
source tree maintainable.
