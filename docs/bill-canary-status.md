# Bill canary — updated 2026-09-28

**Current deployment supersedes historical canary retention below:** Bill was reinstalled
from official npm `@unblocklabs/fleet-node@0.1.0` and user-owned legacy copies were removed.
The root-owned old package still needs an operator sudo cleanup. See
`bill-npm-cleanup.md` for current health, off-device recovery, and the disclosed loss of
the separate Codex capture log during relocation. Historical rollback paths below now
exist only inside the off-device archive; do not run them directly on Bill.

Publication follow-up: the exact Bill-tested artifact is now public on npm as
`@unblocklabs/fleet-node@0.1.0` (`latest`). Public download integrity and a fresh registry
CLI installation passed. See `npm-release-0.1.0.md`; Bill was not reinstalled or restarted.

## Decision: Bill canary passed; ready for staged fleet migration

The operator completed cutover at `2026-09-28T05:54:07Z`. All eight new jobs are loaded;
Collector and exporter run as Bill (UID 501), own only loopback listeners, and the old
seven system jobs plus GUI thermal job remain disabled/unloaded. Main fleet monitoring
has resumed. The unrelated Codex capture Collector remains untouched.

Imported history contains 1947 identities with the original
`observed_since=1789873055.218782`. Heartbeat, readiness and thermal snapshots are fresh;
native OpenClaw metrics/logs/traces are reaching Charizard. Both scheduled five-minute
cycles, ordinary-user npm upgrade/rollback/restart and final central freshness passed.
Bill remains on the original tested `0.1.0` artifact. No other node was migrated.
The failed-attempt history below is retained as evidence, not current status.

## History: failed pre-import attempts and corrections

The operator ran the cutover on September 28. All seven legacy system jobs and the old
GUI thermal job are disabled/unloaded, and their eight plists are retained in `rollback/`.
At this intermediate phase, main fleet monitoring was offline and the TS jobs were not
yet installed. Ports 4318/8888/13133/9100 were free, destination queue/history directories
were empty, and no receipt existed. No queued telemetry or session history was overwritten.

The original quiescence test incorrectly matched PID 1647, an unrelated Codex capture
Collector that reuses the legacy binary but uses its own configuration and output.
Live inspection confirmed it has no storage extensions and only writes under
`~/.openclaw/codex-otel-capture/`; it must remain running. The legacy Collector binary
must also remain on disk for this separate service.

The corrected cutover script matches fleet-specific config/scripts/textfile outputs,
accepts already-parked plists without overwriting them, and verifies old labels are
unloaded and disabled before import. It refuses ambiguous/symlink plist states or an
existing copy receipt; the importer still requires empty new queue/history directories.
The corrected script is staged on Bill with matching local/remote SHA-256
`0513b774188f6a8971f8008c7dfc41114070c62b12aa2d454fc3a34eaed24261`.
The previous script is retained as `bill-cutover.sh.before-quiescence-fix`.
Four focused regression tests passed (first-run, parked-plist resume, unsafe-state
rejection, and legacy-process selection). The operator retried with this correction.
Earlier staging/preflight results below are historical, not a claim that old monitoring
is still running.

### Second attempt: queue import corrected and rehearsed on Bill

The next operator attempt passed the quiescence check but failed at the first queue
copy: Node `fs.cp` with `errorOnExist:true` rejects the prepared destination directory
even when empty. Live inspection confirmed no queue/session files or receipt were
created. The importer now copies children exclusively into that verified empty
directory and preflights source SQLite integrity/metadata and destination queue-age
conflicts before copying. Production paths remain pinned to Bill at the entrypoint.

The corrected importer is staged with matching local/remote SHA-256
`4765a9735a36b361ff19a54e2f4af26aa7e5ffcdbea168cf52e4f31ad27ea4d3`.
The prior helper is retained as `import-bill-state.mjs.before-empty-dir-fix`.
Three real filesystem/SQLite regression tests passed, including forward transfer,
reverse transfer with new observations, WAL data, private files, collision rejection,
and retained pre-return backups. The earlier shell tests mocked state import and did
not catch this bug; these tests execute the actual transfer operation.

At `2026-09-28T05:52:33.066Z`, `ops/bill-rehearse-state.mjs` ran the corrected operation
on Bill's Node 24.18.0 using the actual stopped legacy queue and ledger, writing only
to private scratch directories. Forward and reverse transfers both preserved 1947
session identities and `observed_since=1789873055.218782`; all 13 queue entries matched
the source byte-for-byte, and pre-return backup data survived. The production queue
and session destinations remain empty, the new gate remains closed, and monitoring
was still offline pending the operator retry. All eight actual prepared plists passed
`plutil -lint`; the actual installer passed `sh -n`.

Evidence on Bill:
`/Users/billjohansson/fleet-node-ts-canary-20260927/state-rehearsal-9dcWvJ/report.json`.
Scratch evidence remains private and recoverable. The operator subsequently completed
cutover with no npm reinstall or changes on other nodes. If a later error occurs
after a state-copy receipt exists, inspect that phase rather than repeating import.

## Built and reviewed

- Package: `@unblocklabs/fleet-node@0.1.0`, 17 packed files, compiled JS only plus docs/license.
- Artifact: `artifacts/unblocklabs-fleet-node-0.1.0.tgz`.
- SHA-256: `be9e44d4fe76036f3d135d133f0debfbc0c8200dc545f50df94b63c9829a4d51`.
- Strict TypeScript build and 44-test full suite passed, zero skips, including real pinned Collector integration.
- Final packed allowlist validation passed; subsequent live-discovered large OpenClaw manifest fix passed all six relevant provision/packed tests.
- Review fixes and source evidence are in `coverage.md`. Both reference repositories remain unchanged.

## Live ordinary-user npm maintenance passed

At 06:00:07–06:00:46 UTC, the installed CLI and npm ran entirely as Bill (UID 501),
without sudo. A disposable copy of the exact artifact was versioned with npm as
`0.1.1-canary-verification` (same executable code; never published), installed, and
rolled back through npm to the original `0.1.0` artifact. Each change followed
stop → npm install → prepare → validate → start. A final `restart` also succeeded.

- Closed gate remained effective across 12 seconds of actual launchd KeepAlive retry
  before each package replacement; no managed listeners reopened.
- All 42 protected filesystem entries, including config, auth, queues, queue-age
  state, session ledger, textfiles and logs, retained bytes, permissions and ownership
  while stopped through npm replacement and prepare/validate. Transient job locks
  are intentionally excluded.
- Both daemon listeners recovered under new PIDs as UID 501 after each change and
  again after the explicit restart; all eight jobs remained loaded.
- OpenClaw config remained byte-identical, no system plist reinstall was needed,
  and final installed version is `0.1.0`.

Private report on Bill:
`/Users/billjohansson/fleet-node-ts-canary-20260927/maintenance-verification-ZsrkRd/report.json`.
The original tested artifact checksum above remains unchanged. No registry publication,
central deployment, gateway restart, privilege-policy change, or other-node migration.

## Initial staging — before the September 28 attempt

- Prefix: `/Users/billjohansson/.local/fleet-node-npm`.
- External runtime: `/Users/billjohansson/.local/share/fleet-node`.
- Private rollout/rollback directory: `/Users/billjohansson/fleet-node-ts-canary-20260927`.
- Explicit runtime: `/opt/homebrew/opt/node@24/bin/node` (24.18.0).
- Explicit npm CLI: `/opt/homebrew/opt/node@24/lib/node_modules/npm/bin/npm-cli.js` (11.16.0).
- Package copied with matching checksum and installed by Bill's account. Schema-3 config copied from deployed Charizard; existing protected auth reused without printing or rotating it.
- Native pinned dependencies downloaded/verified; production Collector config and service definitions prepared.
- Maintenance gate remains closed. New queues/history are empty for cutover import. No new system jobs bootstrapped.
- Original runtime remains active. No OpenClaw config changes, gateway restart, central changes, or other-node migration.

## Real-source preflight passed

At `2026-09-27T23:06:10.674Z`, the installed packed CLI ran all six collectors against
Bill's actual sources in a private scratch runtime. Heartbeat, readiness, thermal,
cron, sessions, and Codex each exited 0 and reported success 1 (102–1239 ms).
The read-only SQLite backup retained all 1946 session identities and
`observed_since=1789873055.218782`, with a successful integrity check.
OpenClaw configuration and the legacy credential were unchanged; the new gate remained closed.

Evidence: `/Users/billjohansson/fleet-node-ts-canary-20260927/preflight-bTBAmS/report.json`
on Bill, generated by `ops/bill-preflight.mjs`. This proves real-source collection,
not scheduled execution or ingestion through the new Collector. No scratch telemetry
was exported, and the private scratch directory is retained for inspection.

The September 27 SSH check still found no cutover marker or new Collector launchd job;
the original fleet Collector remained running as PID 640. `sudo -n true` still required
a password. Further live migration verification is blocked on the operator step below.

## Completed operator step — do not rerun after successful state import

Bill's existing sudo policy requires a password. The operator completed this command;
it is retained here for the migration record, not as a pending action:

```sh
ssh -t billsmacmini 'sudo /bin/sh /Users/billjohansson/fleet-node-ts-canary-20260927/bill-cutover.sh'
```

This reviewed, Bill-only script recoverably disables/moves the seven old system plists and
one old GUI thermal plist, verifies old processes/listeners are gone, preserves queued
telemetry and session ledger with SQLite backup, installs the new ordinary-user jobs,
and opens their gate. The old package/runtime files remain intact. If it fails, stop and
inspect the exact phase rather than repeating copies blindly.

Emergency cutover rollback (also passworded, independent of the new npm CLI):

```sh
ssh -t billsmacmini 'sudo /bin/sh /Users/billjohansson/fleet-node-ts-canary-20260927/bill-rollback.sh'
```

## Live acceptance checklist

- [x] New services/listeners run as UID 501; legacy fleet jobs remain disabled/unloaded.
- [x] All six collectors succeed; five-minute jobs progressed from run 2 at 05:59 to run 3 at 06:04, exit 0, without manual collection.
- [x] All 1947 imported observations preserved unchanged; one new source-confirmed observation collected after maintenance (1948 total). Original observed_since retained.
- [x] Actual authenticated host/application/self/heartbeat metrics, native logs and native traces received by unchanged Charizard services.
- [x] No-sudo npm upgrade, rollback and explicit restart passed; gate and external state preservation verified under real launchd KeepAlive.
- [x] Final post-maintenance local and central freshness passed; no pending/firing Bill stale, queue or readiness alerts.

Final recurrence receipt on Bill:
`/Users/billjohansson/fleet-node-ts-canary-20260927/final-recurrence-report.json`
(06:05:53Z): sessions occurrence 06:04:07.992, cron/Codex 06:04:09, all success 1,
run count 3, exit 0. Central query at 06:05:57Z confirmed the new session/cron snapshots,
all collector success gauges and a heartbeat only 16 seconds old, with no relevant alerts.
An initial verifier incorrectly required exact equality to the paused legacy ledger;
the legitimate new observation required checking preservation plus source-confirmed additions,
not treating a growing ledger as corruption. No collector change was required.

### Boundaries for fleet rollout

This is a successful bounded macOS ARM64 canary, not a multi-day soak or reboot test.
The cold start waits for launchd's existing intervals (up to five minutes for the first
cron/session/Codex run); initial stale alerts cleared after those scheduled snapshots.
Repeat account/runtime/path/service/queue preflight for each future node; do not copy
Bill's hardcoded cutover scripts unchanged. Intel macOS was not tested live here.
Do not remove Bill's retained legacy Collector binary: the independent Codex capture
service still uses it. At canary acceptance, no cleanup, registry publication, commit
or push had been performed; subsequent npm publication is recorded above.

Detailed independent evidence: `bill-collectors-verification.md` and
`bill-central-verification.md`. Install and ordinary npm update/rollback commands are
in `../README.md`; recoverable migration requirements are in `migration.md`.

Deployed Charizard read-only verification: repository clean at
`b354fb9953ddfa35bc9e38a4b6d7023311c36221`, server version 0.1.11. Runtime Alloy
uses Basic auth username for canonical identity, retains the session labels and six
source/queue conventions, and stores the current baseline Bill metrics successfully.
