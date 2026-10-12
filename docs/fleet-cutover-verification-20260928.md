# Fleet cutover verification — 2026-09-28

**Result:** all 11 operator-run cutovers are operational on official npm
`@unblocklabs/fleet-node@0.1.0`. Final central snapshot: **14:29:53 UTC**;
targeted follow-up checks completed at **14:31 UTC**. This is post-cutover acceptance,
not a long-running soak or proof of delivery of every historical telemetry sample.

## Scope and method

- Read-only SSH checks of the 11 nodes and Charizard; no remediation, restarts,
  configuration changes, synthetic telemetry or legacy deletion during verification.
- Bill's previous migration is not reverified here. Mika/Peter remain excluded as
  maintenance nodes; Cherry remains unenrolled. Theo's separate exporter proxy was
  not modified.
- Compared actual launchd jobs/PID ownership, CLI validation, loopback listeners,
  scheduled textfile timestamps, read-only SQLite identities/integrity, and central
  Prometheus/Loki/Tempo results. Waited for natural scheduled recovery, rather than
  replacing failed production textfiles with successful manual probes.
- The Loki skill informed aggregate `count_over_time` queries. Only log counts and
  safe trace metadata were returned; no raw log bodies, transcripts or credentials.

## Per-node acceptance

All rows passed runtime validation, all eight new launchd jobs loaded with the gate
open, Collector health, preserved history, scheduled collection and central metrics.
Collector/exporter processes run as each ordinary node account: UID 502 on Fred,
UID 501 elsewhere. The old fleet jobs are disabled/unloaded with their plists parked.

| Node | Cutover UTC | Preserved session identities | Latest inspected identities |
| --- | --- | ---: | ---: |
| Pearl | 14:16:23 | 2713 | 2714 |
| Vera | 14:16:37 | 1341 | 1341 |
| James | 14:17:16 | 346 | 346 |
| Rocky | 14:17:30 | 454 | 454 |
| Fiona | 14:17:37 | 343 | 343 |
| Fred | 14:18:05 | 441 | 441 |
| Bridger | 14:18:16 | 232 | 232 |
| Fred Farrynheight | 14:18:29 | 231 | 231 |
| Theo | 14:18:47 | 2288 | 2288 |
| Ivy | 14:19:00 | 987 | 987 |
| Alfie | 14:19:21 | 1550 | 1552 |

**10,926 original identities preserved:** zero missing identities and zero changed
day/cron classifications. Every ledger retains its original `observed_since` and
passes SQLite integrity checks. Counts may grow after the snapshot. OpenClaw config
and legacy Collector config hashes still match the guarded pre-cutover originals.

## Central receipt and scheduling

At 14:29:53 UTC, all 11 nodes had readiness, thermal, cron and session success gauges
equal to 1. All 10 Codex-enabled nodes had success 1 and `error_type=none`; Bridger's
centrally configured Codex-disabled exception is preserved.

- Heartbeat occurrence ages: 14–52 seconds.
- Readiness occurrence ages: 16–76 seconds; thermal: 14–74 seconds.
- Cron occurrence ages: 32–207 seconds; sessions: 32–211 seconds.
- Enabled Codex occurrence ages: 31–196 seconds.
- Host samples were at most 22 seconds old; Collector self-metrics at most 31 seconds.
- Native OpenClaw metrics and post-cutover native traces were confirmed for all 11.
  Theo did not expose the RPC-specific family used for the initial comparison;
  its native memory family was freshly received, 8.7 seconds old at 14:31:09 UTC.
- Post-cutover native log counts were positive for Pearl, Vera, James, Ivy, Alfie,
  Theo and Fred Farrynheight. Rocky, Fiona, Fred and Bridger had no matching logs
  in this observation window: their log delivery remains unexercised, not proven
  failed. No artificial business activity was generated to force logs.
- No pending/firing alerts matched the checked stale-signal, queue or gateway-not-ready
  categories for these 11 nodes. This is not a claim that all business-cron or other
  unrelated alerts are clear.

Charizard's inspected deployment was clean at
`b354fb9953ddfa35bc9e38a4b6d7023311c36221`, server version `0.1.11`.
No central code, policy, credential or service was changed.

## Transient failures and backlog

Pearl/Rocky's initial Codex `app_server_timeout` results and Fred's cron
`command_failed` result recovered on their next scheduled runs. Ivy's pre-existing
legacy Codex failure also recovered. The successful scheduled samples reached central.

Fred's substantial pre-existing host/self backlog drained. All six local queues were
zero at 14:31:06 UTC; successful agent exports advanced from 39,719 to 61,598 points,
and host exports to 430,394 points by the earlier final local snapshot. Native spans
were also received centrally. The new Collector error log had 14 deadline-timeout
entries but no matches for `Dropping data`, `Permanent error`, or `no more retries`;
exposed receiver failed/refused counters were zero. This supports recovery, not an
end-to-end accounting guarantee for every old queued sample.

Small transient batches appeared in some queue snapshots (James app metrics, Theo
host metrics, Fred host metrics). A nonzero instantaneous queue is not by itself a
backlog failure; fresh exports and absence of sustained-queue alerts were verified.

## Recovery and next step

Legacy code/state and per-node `fleet-node-migration-20260928` recovery directories
remain on these 11 machines. They are inactive, **not deleted**. No cleanup is implied
by acceptance. Any later cleanup should inventory exact targets, archive recoverably,
and preserve the current external queues, configuration and session ledger.

Routine npm updates/version rollback follow the README's ordinary-user flow:
`stop` → exact-version npm install → `prepare` → `validate` → `start`.
Do not bypass the maintenance gate or restore an old state snapshot for npm rollback.

Private evidence remains under ignored `artifacts/fleet-staging-20260928/`:
per-node `post-cutover-initial.json` and `post-cutover-cycle.json`, targeted
`post-cutover-final.json` files, `central-post-cutover-final.json`, and Fred/Charizard
final supplements. Verification scripts are `ops/fleet-verify.mjs` and
`ops/fleet-central-verify.py`.
