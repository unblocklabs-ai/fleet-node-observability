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
username. Client attributes such as `node`, `node_label`, and `host.name` are
claims, never trusted identity. The lower-cardinality `account_domain` field is also a client claim,
not an authoritative account or node identity.

The node config is secret-free. OpenClaw has no central credential configured; the Collector is the
only managed process configured to read the protected authorization-header file.

The operational trust boundary is the dedicated single-user node account, not process isolation
within that account. OpenClaw and the Collector share one UID, so same-UID code can read the per-node
Collector credential or inject telemetry through the unauthenticated loopback OTLP receiver. Each
credential is independently revocable to bound fleet-wide impact. A dedicated Collector service
account is future hardening and is outside the current contract.

## Signal contract

The node always exports:

- OpenClaw logs, traces, and metrics received over loopback OTLP/HTTP;
- host and textfile metrics scraped from loopback node_exporter;
- Collector self-metrics; and
- an occurrence-timestamp heartbeat with queue health.

Raw logs received from OpenClaw are subject only to two low-severity structured routine-success
filters before export: successful gateway authentication and successful tool-policy removal. There
are no body-prefix filters; task output, status updates, near misses, WARN-or-higher records, and
failure variants are retained. QMD and Codex source noise remains until upstream provides stable
structured discriminators for capture-off telemetry.

Every signal has a bounded `fleet.signal.source` value. Charizard assigns storage job names and
canonical node labels from authenticated identity plus that source. Charizard does not connect to a
node monitoring port.

Metric and label names consumed by central dashboards and alerts are a cross-repository API. Any
change requires coordinated tests in both repositories.
