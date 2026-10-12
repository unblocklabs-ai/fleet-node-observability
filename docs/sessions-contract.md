# Session history compatibility

The implementation ports the session collector from reference node tag `v0.3.8`
(the unchanged v0.3.7 session module), not that repository's older `main` checkout.
Local source metadata is read using SQLite read-only handles, including active WAL.
No transcript contents, session IDs, or agent namespaces leave the node.

The private ledger remains SQLite with the original `starts(identity, day, cron)`
and `metadata(key, value)` schema. Identity is SHA-256 of the agent namespace,
NUL, and session ID. `observed_since` and source-absent observations are retained.
Migration must use a consistent SQLite backup of the existing ledger, never a
database-only copy while a WAL writer is active. Keep the old ledger recoverable;
never roll back newer observations by restoring an older backup over them.

Start dates prefer identity-matched lifecycle metadata, then the window start;
creation and update times are not substitutes. Corrections happen before pruning.
Retention is the current New York calendar month plus twelve previous months.
Daily, Monday-weekly and calendar-monthly gauges preserve existing names and labels;
`session_day` is period-start midnight with the correct DST offset. Empty history
before preservation began remains `NaN`, not an invented zero.

Session SQL runs in a separate Node subprocess with a 30-second deadline and
2-second SQLite busy timeout. This allows a hard kill even during synchronous
native SQL. Cron and heartbeat run in different launchd jobs: neither a failed
cron RPC nor a locked session database can block heartbeat collection. Failure
replaces the snapshot with fresh occurrence time and success zero, without counts.
