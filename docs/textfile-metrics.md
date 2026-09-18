# Node Textfile Metrics

Local producers write Prometheus textfiles into the selected Homebrew node_exporter directory. The
node-local Collector scrapes node_exporter on loopback and exports the resulting metrics through its
authenticated OTLP/HTTP connection.

## Always scheduled

- `fleet_node_agent_heartbeat_timestamp_seconds{node}` records occurrence time every 30 seconds.
- `fleet_node_agent_queue_metrics_available{node}` records whether all six expected Collector queue
  exporter samples are present and valid.
- `fleet_node_agent_queue_oldest_age_seconds{node,signal}` reports seconds since a signal queue was
  first observed non-empty without a subsequently observed valid zero.
- `openclaw_gateway_ready{node,gateway_ready_url}` checks the loopback readiness endpoint every 60
  seconds.
- `openclaw_gateway_check_timestamp_seconds{node}` records each completed probe, including failed
  probes. Treat readiness as unknown when this timestamp is absent, older than 120 seconds, or
  more than 30 seconds in the future; re-scraping a stale textfile does not renew readiness.
- macOS thermal metrics report pressure availability, level, collection success, collection time,
  and a bounded error label every 60 seconds.
- OpenClaw cron schedule collection runs locally every five minutes. It exports collector
  freshness, enabled/disabled totals, counts grouped by identical schedule, and bounded per-job
  identity, next-run, latest-duration, running, normalized status, and consecutive-error fields. It
  never exports job payloads, trigger commands, or error messages. This replaces the former
  cross-node `cron_pressure.prom` snapshot.
- `openclaw_cron_job_last_run_timestamp_seconds` is the latest attempted run, not
  the latest successful completion. `openclaw_cron_job_running_since_timestamp_seconds`
  exists only while a run is recorded as active. Missing timestamps remain absent,
  never epoch zero. Run timestamps retain millisecond precision.

## Capability-gated

When `codex_usage_enabled` is true, Codex usage collection runs every five minutes through the
installed `codex app-server` methods `account/read` and `account/rateLimits/read`. Codex owns login
and token refresh. The collector does not read OAuth files, call private web endpoints, or infer usage
from transcripts.
Window durations are source-reported: primary can be weekly, and a missing secondary window is
unavailable, not zero usage. Prefer the `codex` entry in `rateLimitsByLimitId`, falling back to the
legacy `rateLimits` object. Use `time() - codex_usage_collected_at_seconds` for collection age;
the former constant-zero snapshot-age metric has been retired.

All node labels in textfiles are client claims. Charizard authentication remains authoritative.
Keep labels bounded and coordinate metric or label changes with central dashboard and alert tests.
