# Central and Node Contract

The node is an autonomous outbound telemetry client. Charizard exposes one canonical HTTPS
OTLP/HTTP endpoint and issues one independently revocable credential for each stable node label.

## Public node intent

Central renders exactly:

```json
{
  "config_schema_version": 3,
  "node_label": "mini_03",
  "telemetry_endpoint": "https://telemetry.example.com",
  "codex_usage_enabled": true
}
```

Central does not own accounts, home directories, paths, architecture, package locations, loopback
ports, or node_exporter settings. The node installer derives those facts locally.

## Identity and authentication

The Collector sends Basic authentication whose username is the normalized stable node label and
whose password is the per-node token. Charizard derives canonical identity from the authenticated
username. Client attributes such as `fleet.claimed_node`, `node`, `node_label`, and `host.name` are
claims, never trusted identity.

The node config is secret-free. OpenClaw has no central credential; only the Collector reads the
protected authorization-header file.

## Signal contract

The node always exports:

- OpenClaw logs, traces, and metrics received over loopback OTLP/HTTP;
- host and textfile metrics scraped from loopback node_exporter;
- Collector self-metrics; and
- an occurrence-timestamp heartbeat with queue health.

Every signal has a bounded `fleet.signal.source` value. Charizard assigns storage job names and
canonical node labels from authenticated identity plus that source. Charizard does not connect to a
node monitoring port.

Metric and label names consumed by central dashboards and alerts are a cross-repository API. Any
change requires coordinated tests in both repositories.
