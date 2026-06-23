# Release Compatibility

This repo uses semver by default (`VERSION` file).

- `0.1.x`: Initial standalone node-side command surface, collectors,
  installers, docs, examples, tests, and local-first release packaging.
- Public metric and file-path contracts are treated as stable.

## Compatibility policy

- Patch (`0.1.1`): compatible unless release notes say otherwise.
- Minor (`0.2.0`): may add new optional metrics or flags; old defaults should still work.
- Major (`1.0.0`): may change metrics names/labels or launch lifecycle behavior.

## Interface compatibility matrix

The table captures the expected minimum central stack behavior.

| Node release | Central stack requirement | Notes |
|---|---|---|
| 0.1.x | Fleet Alloy config that can consume node_exporter and textfile metrics | No central changes required beyond scrape target acceptance |
| 0.1.x | OpenClaw diagnostics update path in `~/.openclaw/openclaw.json` | Existing keys are preserved before merge |
| 0.1.x | Ingest tokens available per node | Used for Basic auth header generation |
| 0.1.x | off-LAN environments support CF-Access headers | Required by proxy and OTLP headers in off-LAN mode |

## Breaking-change checklist

Before shipping a major or behavior-changing release:

- Publish metric migration notes (names/labels/docs).
- Maintain one compatibility release in between when possible.
- Include install verification commands in release notes:
  - launchctl state
  - collector timestamps
  - OTLP config keys
  - proxy token check

## Release artifacts

Each release tarball should include:

- `VERSION`, `CHANGELOG.md`, `LICENSE`
- `docs/*`
- `examples/*`
- `packaging/*.sh`
- `bin/*`
- `src/fleet_node_observability/*`

Artifacts are version-named and should be deterministic to support offline rollout.
