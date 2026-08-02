# Troubleshooting

These checks inspect the final node-local runtime. Run them on the node during the later deployment
or incident response; this code-cleanup task performs no host changes.

## Local sources

```bash
curl -fsS http://127.0.0.1:9100/metrics | grep '^node_cpu_seconds_total'
curl -fsS http://127.0.0.1:9100/metrics | grep '^fleet_node_agent_heartbeat_timestamp_seconds'
curl -fsS http://127.0.0.1:13133/
curl -fsS http://127.0.0.1:8888/metrics | grep '^otelcol_exporter_'
```

## Service state

```bash
sudo launchctl print system/com.unblocklabs.node-exporter
sudo launchctl print system/com.unblocklabs.fleet-node-agent
sudo launchctl print system/com.unblocklabs.fleet-node-agent-heartbeat
sudo launchctl print system/com.unblocklabs.openclaw-gateway-health-textfile
sudo launchctl print system/com.unblocklabs.macos-thermal-textfile
```

When `codex_usage_enabled` is true, also probe the capability-gated service:

```bash
sudo launchctl print system/com.unblocklabs.codex-usage-textfile
```

The Codex service is intentionally absent when that schema-3 capability is false.

## Queue and export health

Inspect the Collector self-metrics for queue size, capacity, rejected records, enqueue failures,
send failures, and retry behavior. Compare them with
`fleet_node_agent_queue_oldest_age_seconds`; a recent heartbeat does not prove that every outbound
queue is draining.

## OpenClaw

Inspect `~/.openclaw/openclaw.json` and confirm the OTLP endpoint is loopback, headers are `{}`,
`traces`, `metrics`, and `logs` are true, `logsExporter` is `otlp`, and `captureContent` is false.
Signal-specific endpoint overrides must be absent. Review the timestamped backup before restarting
OpenClaw. Collector health does not prove that OpenClaw emitted or Charizard stored logs, metrics,
and traces.

## Secrets

The schema-3 config and Collector JSON must not contain raw tokens. The authorization-header file
must be mode `0600` inside the node-owned secrets directory. Do not print it during troubleshooting.
