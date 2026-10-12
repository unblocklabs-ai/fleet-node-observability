# Bill collector verification — 2026-09-28 UTC

Independent read-only SSH observations on `billsmacmini`, 05:55–05:59:51 UTC.
No collector was manually invoked; no service, configuration, source database or
credential was changed. No raw session identities, cron descriptions, account
emails or transcripts were exported. Package version: **0.1.0**.

## Scheduled execution

The bootstrap occurred behind the closed maintenance gate. At 05:56:42 all three
five-minute jobs had one launchd run, exit 0, and no new snapshot yet. Their next
scheduled invocation published successful snapshots without manual collection:

| Collector | Occurrence timestamp | launchd runs | Last exit | Success |
|---|---|---:|---:|---:|
| Sessions | 2026-09-28 05:59:07.588 UTC | 2 | 0 | 1 |
| Cron | 2026-09-28 05:59:08 UTC | 2 | 0 | 1 |
| Codex | 2026-09-28 05:59:08 UTC | 2 | 0 | 1 |

All three had completed by the 05:59:11 observation; no lingering PID was reported.
Their stdout/stderr log files were all zero bytes at 05:59:51. These observations
establish the first scheduled run, not indefinite future health.

## Sessions: preserved history and full calendar reconciliation

- New and stopped legacy ledgers both contained **1,947** unique observations:
  **97 cron**, **1,850 noncron**, dated 2026-04-29 through 2026-09-28.
- Exact `observed_since` retained: **1789873055.218782**. Both ledgers are mode 0600.
- Compared hashed identities and `(day, cron)` internally: **zero missing legacy
  identities, zero changed observations, zero duplicate additions** after the
  scheduled TS collection.
- Independently queried both live source databases using SQLite `mode=ro` with
  bounded execution. Both had active WAL files. Their **1,904** dated retained
  unique rows had **zero undated rows** and **zero date/kind disagreements** with
  the new ledger. **43** source-absent historical observations remained preserved.
- Independently aggregated the ledger using Python calendar dates and
  `ZoneInfo("America/New_York")`: **all 1,389 exported count values matched**;
  no missing/unexpected values. Distribution: 1,179 daily, 171 weekly, 39 monthly.
- **852 `NaN` values** correctly preserve unknown pre-preservation history.
  Both `-04:00` and `-05:00` period-start offsets are present and correct.
  Every grouping sums to 1,947 all / 97 cron / 1,850 noncron.
- Labels remain exactly `node`, `node_label`, `session_day`, `session_kind`.

The retained legacy session module SHA-256 is
`ba0c69391b604c94353eb339747481d323a00493998e1bde31e4b8e912d447ed`,
matching the v0.3.7/v0.3.8 source used for the port.

## Cron: compatibility with the paused legacy snapshot

The scheduled TS snapshot contains **11 jobs: 9 enabled, 2 disabled**. Compared
all **63 non-collection-timestamp series**, including complete labels and values,
against the paused legacy snapshot: **zero missing, added or changed series**.
Metric names and bounded label schema are unchanged. It includes 10 last-attempt
timestamps, 10 latest durations, 9 next-run timestamps, and 11 error counters.
Collection time advanced to the scheduled occurrence rather than retaining the
legacy timestamp.

## Optional Codex usage

The scheduled app-server collection succeeded. Its **8 non-collection-timestamp
series** retain the legacy metric names and labels, with no added/missing series.
Only primary used/remaining percentages changed versus the older snapshot.
The current API-reported primary window is **10,080 minutes**, with **4% used /
96% remaining**. Secondary-window samples remain **absent**, not fabricated zero.
No full-email label is present; labels remain `node`, `profile`, `account_domain`,
`plan_type`, `source`, plus `error_type` on collector success.

## Installed artifact check and boundaries

The installed compiled modules exactly match the reviewed local build:

| Module | SHA-256 |
|---|---|
| sessions.js | `fb4954e6b4ae862ee7fad970ecc79e152e7bd14efedfe12c1dba159f4f9c247d` |
| cron.js | `36aad77fa5f901efbd2dc5d9e45619940ad615901f8fdbebddc96e3949e76ddd` |
| codex.js | `191cc2993570dcd20bede3af9aac9230b5a0a5df22ddbb877bec2107bfa589e1` |

This report establishes local scheduled behavior and preservation. It does not
independently establish central ingestion, subsequent npm upgrade/rollback, or
post-maintenance scheduling; those require separate canary evidence. Cron was
compared to the paused legacy snapshot, not a second manually triggered gateway
RPC. Codex was observed through its scheduled output, not a second account query.

## Parent follow-up after npm rollback and restart

Read-only verification at 06:05:53 UTC confirmed each five-minute launchd job advanced
to run 3, exit 0: sessions at 06:04:07.992, cron and Codex at 06:04:09, all success 1.
All 1947 legacy identities and their day/kind remained intact. One additional identity
was verified against the live read-only session source, bringing the ledger to 1948;
observed_since remained 1789873055.218782. The receipt is
`/Users/billjohansson/fleet-node-ts-canary-20260927/final-recurrence-report.json` on Bill.
