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
- macOS thermal metrics report pressure availability, level, collection success, collection time,
  and a bounded error label every 60 seconds.

## Capability-gated

When `codex_usage_enabled` is true, Codex usage collection runs every five minutes through the
installed `codex app-server` methods `account/read` and `account/rateLimits/read`. Codex owns login
and token refresh. The collector does not read OAuth files, call private web endpoints, or infer usage
from transcripts.

All node labels in textfiles are client claims. Charizard authentication remains authoritative.
Keep labels bounded and coordinate metric or label changes with central dashboard and alert tests.
