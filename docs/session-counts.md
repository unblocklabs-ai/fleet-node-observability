# Daily session counts

The node collector exports a bounded 30-day snapshot every five minutes. All and cron-only
charts count unique **session windows with a start timestamp**, across agent namespaces on
each named fleet node. Repeated turns within one session do not increase the count. Cron is
the `agent:<agent>:cron:<job>` namespace (including isolated runs); children are not inferred
to be cron. Buckets use America/New_York midnight, including DST offsets.

The source is each agent's `session_windows` table, opened with SQLite `mode=ro`. No
transcript/body queries. A private, mode-0600 ledger stores only hashed identity, day and
cron boolean, pruning after 30 days. It preserves observed starts after source cleanup.
`started_at` is not replaced with `created_at`/`updated_at`: migration/import timestamps
would put old sessions on false dates.

Limits: backfill covers retained, dated metadata only. Undated windows are reported separately.
Sessions removed before the first collection or created and deleted between five-minute
polls can be missed. This is operational observed activity, not a complete audit ledger.
Today remains partial. Errors publish failure with no counts; dashboards gate success and
occurrence age (ten minutes, maximum future skew 30 seconds), not just scrape freshness.

## Add to existing managed nodes

Use GitHub-reviewed source transferred from the central checkout. Do not rerun the full
node installer just to add this collector. As root, run:

```
python3 /absolute/source/src/fleet_node_observability/commands/install_session_collector.py
```

This derives the account, Python, runtime and textfile paths from the existing managed cron
collector plist. It adds one module and `com.unblocklabs.openclaw-sessions-textfile`, taking
backups under `/var/tmp/fleet-sessions-before-*`. It does not upgrade the whole node package,
change credentials, or restart OpenClaw, node_exporter or the OTLP Collector. Record the
module SHA separately from the root package VERSION. A full installation includes this service.

Verify both local `openclaw_sessions_collector_success == 1` and fresh central metrics,
including the preserved `session_day` / `session_kind` labels. Check all 60 rows per node and
that each cron count is <= the corresponding all count. Validate a subsequent scheduled run.
Rollback: boot out only the new session service, restore any backed-up module/plist, and
move its `.prom` file outside the textfile directory. Preserve the private ledger for recovery.
