# Node Textfile Metrics

Collectors write Prometheus textfiles that the current node_exporter/Prometheus scrape path consumes.
Avoid changing names/labels unless coordinated through release compatibility.

In `0.1.x`, central Prometheus obtains these metrics by scraping node_exporter. In `0.2.0`, the
node-local Collector scrapes the same loopback node_exporter/textfile surface and exports the
resulting metrics to Charizard over OTLP/HTTP. The textfile format and metric meaning remain stable;
only transport and trusted identity assignment move.

## Node-agent heartbeat

- `fleet_node_agent_heartbeat_timestamp_seconds`
  - gauge value: Unix occurrence time when the node created the heartbeat
  - bounded label: `node` (treated as a client claim; Charizard auth remains authoritative)

The heartbeat is generated every 30 seconds, scraped through a dedicated receiver, and exported
through its own 8 MiB persistent queue with a five-minute retry age. Central freshness compares the
metric value to current time; delayed replay of an old sample therefore cannot masquerade as a
current heartbeat.

The same producer reads the Collector's loopback self-metrics and emits:

- `fleet_node_agent_queue_metrics_available{node}`
- `fleet_node_agent_queue_oldest_age_seconds{node,signal}`

The `signal` label is restricted to six configured pipelines. Age starts when a queue is first
continuously observed non-empty, survives a Collector restart in a protected local state file, and
resets only after that queue is observed empty. This is an operational backlog-age estimate, not an
exact age for every record.

## OpenClaw gateway

- `openclaw_gateway_ready` (gauge; `1` ready, `0` not ready)
  - labels:
    - `node`
    - `gateway_ready_url`

This repo does not emit `openclaw_gateway_last_ready_check_timestamp_seconds`. Agent liveness is
covered by the occurrence-timestamp heartbeat; gateway readiness retains its existing metric.

## Codex usage

The collector starts the installed `codex app-server` with a bounded timeout
and reads only `account/read` and `account/rateLimits/read`. Codex owns login,
token refresh, and its account API. This collector never reads or writes
`auth.json`, calls private ChatGPT endpoints, or derives account state from
session transcripts. The `source` label is therefore `app_server`; failures
remain visible through `codex_collector_success=0` and a bounded `error_type`.

Core metrics:

- `codex_collector_success`
- `codex_usage_snapshot_age_seconds`
- `codex_usage_collected_at_seconds`
- `codex_usage_primary_used_percent`
- `codex_usage_primary_remaining_percent`
- `codex_usage_primary_window_minutes`
- `codex_usage_primary_resets_at_seconds`
- `codex_usage_secondary_used_percent`
- `codex_usage_secondary_remaining_percent`
- `codex_usage_secondary_window_minutes`
- `codex_usage_secondary_resets_at_seconds`
- `codex_credits_has_credits`
- `codex_credits_unlimited`
- `codex_credits_balance`

Codex labels:

- `node`
- `profile`
- `account_domain`
- `account_email`
- `plan_type`
- `source`
- `error_type`

## macOS thermal

- `fleet_macos_thermal_pressure_available`
  - labels:
    - `node`
    - `node_label`
    - `source`
- `fleet_macos_thermal_pressure_level`
  - labels:
    - `node`
    - `node_label`
    - `source`
    - `pressure`
- `fleet_macos_thermal_collector_success`
  - labels:
    - `node`
    - `node_label`
    - `source`
- `fleet_macos_thermal_collected_at_seconds`
  - labels:
    - `node`
    - `node_label`
    - `source`
- `fleet_macos_thermal_collection_error_info`
  - labels:
    - `node`
    - `node_label`
    - `source`
    - `error`

## Expected labels

Collectors intentionally emit no `hostname` label or trusted node-identity assertion. Some
published metrics retain the operator-supplied `node` label for compatibility. Current central
Prometheus derives trusted labels during scrape/relabeling; the push path must instead derive them
from the credential authenticated by central Alloy.

`account_email` is a deliberate, owner-approved Codex usage label retained for
operator triage. Keep it stable unless the central contract is explicitly
changed.

Use lowercase label names and avoid adding new high-cardinality account
identifiers beyond `account_email`.

## Current scrape path contract

- `fleet-node-exporter-proxy` exposes only `/metrics`.
- Proxy enforces `X-Fleet-Scrape-Token` for every request.
- Node-exporter itself may expose normal metrics on port `9100` as needed.

These proxy and token requirements are transitional. In `push`, the unified installer binds
node_exporter to loopback and does not expose it to Charizard or Cloudflare.

## File locations

Default when node_exporter is selected from `/opt/homebrew`:

- `/opt/homebrew/var/lib/node_exporter/textfile_collector/`

Default when node_exporter is selected from `/usr/local`:

- `/usr/local/var/lib/node_exporter/textfile_collector/`

The unified installer selects and validates one supported Homebrew installation before freezing
config, uses that same prefix for the node_exporter binary or installation, and derives the
textfile path from it. A node with an intentionally nonstandard node_exporter configuration may
override the path locally with:

- `--node-exporter-textfile-dir`

Schema 2 central config cannot set this path. The older topology-specific installers and their
unversioned configs retain their existing compatibility flags during the migration window.

## Troubleshooting

- If collectors stop updating: check collector schedule and launchctl state.
- If labels vanish: validate script permissions and JSON output rendering.
- If parse fails in central scraping, inspect textfile syntax:
  - one metric per line
  - escaped labels with `\"` and `\\`
  - timestamps optional but accepted.
