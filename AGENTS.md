# Fleet Node Observability

## Start here

Read `README.md` and `docs/central-contract.md`. Operational continuation notes:
`/Users/bek/GitHub Projects/fleet-observability/docs/handoff-2026-09-20.md`.
That handoff is a dated snapshot, not live deployment truth.

This repo owns the macOS node runtime. Its sister repo,
`/Users/bek/GitHub Projects/fleet-observability`, owns fleet identity, credentials,
central ingestion, storage, dashboards, alerts, and deployment orchestration.

## Code map

- `bin/`: executable entry points for installation, configuration, and collection.
- `src/fleet_node_observability/agent.py`: schema-3 config validation, local host
  discovery, Collector configuration, and loopback OpenClaw settings.
- `src/fleet_node_observability/openclaw.py`: guarded OpenClaw config edits and backups.
- `src/fleet_node_observability/commands/`: Python collectors and configuration helpers.
- `src/fleet_node_observability/collectors/`: shell heartbeat/readiness collectors.
- `src/fleet_node_observability/installers/`: macOS service installer.
- `src/fleet_node_observability/{atomic,textfile}.py`: safe file publication helpers.
- `packaging/`: checksummed release build/install scripts; `VERSION` owns package version.
- `tests/`: unit, installer/packaging contract, and Collector integration tests.
- `docs/`: migration, OTLP, textfile metrics, and troubleshooting runbooks.

## Cross-repo contract

OpenClaw sends OTLP to the loopback Collector. The Collector also scrapes local
node_exporter/textfiles and forwards telemetry over authenticated HTTPS to
Charizard. Charizard does not scrape fleet nodes. Keep central credentials out
of OpenClaw config; only the Collector exports centrally.

Schema 3 contains only `config_schema_version`, `node_label`,
`telemetry_endpoint`, and `codex_usage_enabled`. Discover host-specific paths,
accounts, and architecture locally. Coordinate metric/label changes with central
queries, alerts, and tests. Do not restore the pre-migration combined architecture.

## Validation and working boundaries

Python 3.11+; no third-party Python runtime dependencies. From this repo:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
git diff --check
./packaging/build-release.sh --output "$(mktemp -d)"
```

Inspect `git status` first and preserve existing changes and generated artifacts.
Do not treat a skipped integration test or a package version as deployment proof.
Verify installed module versions, local health, and fresh central telemetry.
Bill (`billsmacmini`) is the preferred canary when live testing is authorized.

Default editing scope is the two fleet repos. OpenClaw core/plugin edits, upstream
PRs/merges, service changes, and workload retries require explicit task scope.
Past operational approvals are not blanket authorization for a new session.
Keep secrets, private transcripts, state databases, and credential-bearing output
out of Git and reports. Read remote SQLite using `mode=ro` for investigations.
