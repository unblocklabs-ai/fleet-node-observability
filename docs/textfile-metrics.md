# Node Textfile Metrics

Collectors write Prometheus textfiles that Fleet Alloy scraping jobs consume.
Avoid changing names/labels unless coordinated through release compatibility.

## OpenClaw gateway

- `openclaw_gateway_ready` (gauge; `1` ready, `0` not ready)
  - labels:
    - `node`
    - `gateway_ready_url`

This repo does not emit `openclaw_gateway_last_ready_check_timestamp_seconds`.
Central dashboards should use the scrape timestamp for freshness checks.

## Codex usage

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
- `fleet_macos_thermal_pressure_level`
- `fleet_macos_thermal_collector_success`
- `fleet_macos_thermal_collected_at_seconds`
- `fleet_macos_thermal_collection_error_info`

## Expected labels

Collectors intentionally emit no `hostname` label or trusted node-identity
assertion. Some published metrics retain the operator-supplied `node` label for
compatibility, but central Alloy and Prometheus configuration derives trusted
node identity during scrape and relabeling.

`account_email` is a deliberate, owner-approved Codex usage label retained for
operator triage. Keep it stable unless the central contract is explicitly
changed.

Use lowercase label names and avoid adding new high-cardinality account
identifiers beyond `account_email`.

## Scrape path contract

- `fleet-node-exporter-proxy` exposes only `/metrics`.
- Proxy enforces `X-Fleet-Scrape-Token` for every request.
- Node-exporter itself may expose normal metrics on port `9100` as needed.

## File locations

Default on macOS package default:

- `/opt/homebrew/var/lib/node_exporter/textfile_collector/`

Installer examples may override with:

- `--node-exporter-textfile-dir`
- `fleet_node_exporter_textfile_dir` in node JSON config.

## Troubleshooting

- If collectors stop updating: check collector schedule and launchctl state.
- If labels vanish: validate script permissions and JSON output rendering.
- If parse fails in central scraping, inspect textfile syntax:
  - one metric per line
  - escaped labels with `\"` and `\\`
  - timestamps optional but accepted.
