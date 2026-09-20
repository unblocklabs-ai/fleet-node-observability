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
- Collector self-metrics;
- a bounded local OpenClaw cron schedule snapshot; and
- thirteen calendar months of daily/weekly/monthly session-start snapshots
  (all / cron / noncron, America/New_York); and
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

Session metrics prefer `session_nodes.entry_json.sessionStartedAt`, joined by session key and
current session ID and checked against the JSON session ID, then `session_windows.started_at`.
They never use unverified `created_at`/`updated_at` or run counts. The lifecycle timestamp has
precedence, matching OpenClaw's session-creation resolver without its activity-time fallback.
The collector reads each agent SQLite database in `mode=ro`. A private hashed-ID ledger retains
the current calendar month and twelve previous months (at most 200,000 identities). It
deduplicates starts, reconciles corrected dates, and preserves observations through later session cleanup. Historical counts
are limited to retained dated metadata; missing start timestamps are excluded and reported as
`openclaw_sessions_undated`. Collection every five minutes cannot guarantee capture of sessions
created and deleted between collections. No transcripts, session IDs, or session keys leave the node.
`openclaw_sessions_daily`, `openclaw_sessions_weekly`, and `openclaw_sessions_monthly`
share bounded `session_day` (period-start ISO midnight with New York UTC offset) and
`session_kind` (`all` / `cron` / `noncron`) labels. Weeks begin Monday; months begin on the first.
At most 1,404 count series per node; all = cron + noncron. Each value is a period total
in the latest snapshot, not an increment to sum across scrapes. The whole thirteen-month
snapshot is re-exported, so broad Prometheus retention changes are unnecessary.
Pre-preservation periods with no retained observations export NaN, not zero. Other old
periods are observed lower bounds, not proof of complete historical coverage. Current periods
and the first retained partial week are also partial. `openclaw_sessions_retained_from_seconds`
identifies the retention boundary; `openclaw_sessions_observed_since_seconds` identifies when
preservation began, not uninterrupted polling coverage.
Cron means a session key in the `agent:<agent>:cron:<job>` namespace, not every scheduled run or
subagent spawned by a cron session. Noncron is not necessarily human-initiated. Successful scans
emit observed zero for empty days since preservation began;
failed scans omit counts, and dashboards suppress snapshots older than ten minutes.
