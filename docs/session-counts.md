# Daily session counts

The node collector exports thirteen calendar months (current month plus twelve previous)
of daily, Monday-weekly, and calendar-monthly snapshots every five minutes. All, cron-only,
and exclude-cron
charts count unique **session windows with a start timestamp**, across agent namespaces on
each named fleet node. Repeated turns within one session do not increase the count. Cron is
the `agent:<agent>:cron:<job>` namespace (including isolated runs); children are not inferred
to be cron. Buckets use America/New_York midnight, including DST offsets.

The sources are each agent's `session_windows` and `session_nodes` tables, opened with SQLite `mode=ro`. No
transcript/body queries. A private, mode-0600 ledger stores only hashed identity, day and
cron boolean, pruning before the first retained month. It preserves observed starts after source cleanup.
Use the identity-matched `entry_json.sessionStartedAt` first, then `session_windows.started_at`.
The metadata join must match both session key and current session ID; the JSON session ID
must also match. This prevents reset/replacement metadata from dating an older window.
Never blindly use `created_at`/`updated_at`: OpenClaw's creation resolver may fall back to
activity time. Existing ledger rows are reconciled by identity, including corrections out
of the retention range; observations whose source disappeared are retained until expiry.

The initial collector incorrectly called every missing `started_at` row undated. Version
0.3.6 fixes that and the precedence of the two start representations. Do not use the initial
rollout's excluded count as evidence that the source stores lack dates.

Version 0.3.7 expands retention and publishes `openclaw_sessions_daily`,
`openclaw_sessions_weekly`, and `openclaw_sessions_monthly`, each with `all`, `cron`,
and `noncron` kinds. The existing `session_day` label is the calendar period start.
Noncron = all - cron; it is not a human-only classification. Weekly/monthly values sum
daily counts, never repeated scrapes. Maximum count cardinality is 1,404 series per node.
Because each fresh snapshot includes the full retained history, no increase to global
Prometheus retention is needed. For backfilled history in Grafana, keep range end at Now;
an older range end queries the snapshot available then, subject to Prometheus retention.

Limits: backfill covers retained, dated metadata only. Undated windows are reported separately.
Before preservation began, an empty period is unknown and exports NaN (a chart gap), not
zero. Older nonempty periods are observed lower bounds. The current period, periods before
preservation, and weeks crossing the retention boundary are partial. No claim of gap-free
polling is made. No historical transcripts or session identifiers leave the node.
Sessions removed before the first collection or created and deleted between five-minute
polls can be missed. This is operational observed activity, not a complete audit ledger.
Today remains partial. Errors publish failure with no counts; dashboards gate success and
occurrence age (ten minutes, maximum future skew 30 seconds), not just scrape freshness.

## Add to existing managed nodes

Use source fetched from GitHub. Do not rerun the full node installer just to add this
collector. Resolve the runtime paths from the existing managed cron collector plist.
Back up its `collect_openclaw_cron_schedule.py`, verify that its hash matches the reviewed
pre-change module, then atomically install the new session module followed by the updated
cron module. Both are owned by the existing managed account. The cron module invokes the
session collector after publishing its own result, regardless of cron RPC success.

This reuses the existing five-minute service, without root access or a new LaunchDaemon.
It does not upgrade the whole node package, change credentials, or restart OpenClaw,
node_exporter or the OTLP Collector. Record module SHAs separately from root package VERSION.

Verify both local `openclaw_sessions_collector_success == 1` and fresh central metrics,
including the preserved `session_day` / `session_kind` labels. Check each calendar bucket,
all = cron + noncron for known values, and unknown values remaining NaN. Validate a subsequent scheduled run.
Rollback: restore the backed-up cron module and move `openclaw_sessions.prom` outside the
textfile directory. Preserve the session module and private ledger for recovery.
