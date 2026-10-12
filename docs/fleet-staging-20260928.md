# Fleet migration staging record

**Status update, 2026-09-28 14:31 UTC:** the operator subsequently ran all 11
cutovers successfully. Read-only live acceptance verified the npm services, preserved
history, scheduled checks and central receipt. See
[post-cutover verification](fleet-cutover-verification-20260928.md).
Old services are disabled/unloaded; recovery files remain. **Do not rerun the cutover
commands below.** The remaining text records the earlier preparation state.

**2026-09-28, 14:09 UTC.** User authorized preparation only. All 11 other active
nodes have official npm `@unblocklabs/fleet-node@0.1.0`, private external configuration,
pinned dependencies, reviewed cutover/rollback scripts, and passing real-source checks.
**No cutover, sudo, launchctl mutation, OpenClaw configuration edit/restart, or central
change was executed.** Bill was already migrated and was not changed here.

Fresh Charizard desired-state inventory lists Mika and Peter in maintenance; neither
was staged. Cherry has no current observability inventory entry and was not onboarded.
Fred and fredflint are SSH aliases for the same machine: only `fred` was staged.

## What preparation verified

- Existing target npm prefix, runtime state root, and staging directory were absent
  before creation on every node. Existing legacy services/configuration were inventoried.
- Package installed from public npm with lifecycle scripts disabled, using exact 0.1.0.
- Every new gate remains closed, all eight new labels are absent, and production queue
  and session destinations remain empty. State is copied only after the operator's
  cutover stops the old writers.
- Six actual collectors succeeded per node; Bridger intentionally ran five because
  its centrally configured Codex capability is disabled. Checks wrote only scratch
  textfiles and SQLite snapshots; no scratch Collector was started or telemetry sent.
- SQLite integrity and original observed-since were retained; source OpenClaw config,
  old Collector config and credentials remained byte-identical during preflight.
- All surveyed old service plists remained byte-identical, old jobs loaded, and old
  daemon PIDs unchanged in the final check. Existing Collector health endpoints pass.
- Cutover `--check` passed on all 11 after the final guard update. It checks exact
  account/paths, service hashes, configuration, credentials, package version, ledger,
  empty destinations, closed gate, and absence of installed new services. Drift fails
  before service mutation; inspect rather than bypassing the guard.
- Seven existing migration/rollback safety tests passed, including real filesystem/WAL
  state transfer. Three staged-shell tests passed for six/seven-job variants, UID 502,
  Theo's excluded proxy, failed precheck, and hung legacy-process refusal. These are
  not proof that all 11 live cutovers have completed.

Private local evidence is in `artifacts/fleet-staging-20260928/`, ignored by Git.
Each host directory has plan, preflight report, generated scripts and final staging
verification; each remote staging directory has the corresponding plan/report/scripts.

## Per-node details

| Node | SSH alias | Account UID | Explicit Node | Scratch sessions before → after |
| --- | --- | --- | --- | --- |
| Pearl | pearlperelel | 501 | Homebrew node@24 24.18.0 | 2712 → 2712 |
| Vera | veraperelel | 501 | Homebrew node@24 24.19.0 | 1341 → 1341 |
| James | james-fga | 501 | Dedicated 24.21.0 | 346 → 346 |
| Rocky | rockys-mini-2 | 501 | Homebrew node@24 24.21.0 | 454 → 454 |
| Fiona | fionaperelel | 501 | Homebrew node@24 24.20.0 | 343 → 343 |
| Fred | fred | **502** | Dedicated 24.21.0 | 441 → 441 |
| Bridger | bridger | 501 | Dedicated 24.21.0; Codex disabled | 232 → 232 |
| Fred Farrynheight | fredfarryn | 501 | Dedicated 24.21.0 | 231 → 231 |
| Theo | mac-thesis-theo | 501 | Homebrew node@24 24.18.0 | 2287 → 2287 |
| Ivy | ivywillow | 501 | Dedicated 24.21.0 | 987 → 987 |
| Alfie | alfie | 501 | Dedicated 24.21.0 | 1546 → 1548 |

Dedicated runtimes are at `ACCOUNT_HOME/.local/share/fleet-node-runtime`, outside npm
and OpenClaw. The official Node 24.21.0 ARM64 archive SHA256 was checked locally and
remotely: `bed7eea5325e1108f32ce5228ddd6a5f0f08a499ee42aa7442aea583702f6057`.
No existing Node/Homebrew selection was changed. Theo's separate loopback19100 proxy
forwards to the same loopback9100 endpoint; its files and service remain untouched.

## Commands — run individually from Bek's Mac

These commands **perform the migration**. Stop on an error and inspect that phase;
do not blindly rerun a state import. Up to five minutes may pass before scheduled
cron/session/Codex snapshots resume. Keep old files until central verification passes.

```sh
# Pearl
ssh -t pearlperelel 'sudo /bin/sh /Users/pearlperelel/.local/share/fleet-node-migration-20260928/cutover.sh'
# Vera
ssh -t veraperelel 'sudo /bin/sh /Users/veraperelel/.local/share/fleet-node-migration-20260928/cutover.sh'
# James
ssh -t james-fga 'sudo /bin/sh /Users/james/.local/share/fleet-node-migration-20260928/cutover.sh'
# Rocky
ssh -t rockys-mini-2 'sudo /bin/sh /Users/rocky/.local/share/fleet-node-migration-20260928/cutover.sh'
# Fiona
ssh -t fionaperelel 'sudo /bin/sh /Users/fionaperelel/.local/share/fleet-node-migration-20260928/cutover.sh'
# Fred
ssh -t fred 'sudo /bin/sh /Users/fredflint/.local/share/fleet-node-migration-20260928/cutover.sh'
# Bridger
ssh -t bridger 'sudo /bin/sh /Users/bridger/.local/share/fleet-node-migration-20260928/cutover.sh'
# Fred Farrynheight
ssh -t fredfarryn 'sudo /bin/sh /Users/fredfarryn/.local/share/fleet-node-migration-20260928/cutover.sh'
# Theo
ssh -t mac-thesis-theo 'sudo /bin/sh /Users/thesis-theo/.local/share/fleet-node-migration-20260928/cutover.sh'
# Ivy
ssh -t ivywillow 'sudo /bin/sh /Users/ivywillow/.local/share/fleet-node-migration-20260928/cutover.sh'
# Alfie
ssh -t alfie 'sudo /bin/sh /Users/alfie/.local/share/fleet-node-migration-20260928/cutover.sh'
```

Cutover disables/boots out exactly the inventoried legacy labels, parks original
plists, waits for listeners/writers to stop, transfers queues and SQLite state as the
ordinary account, installs new ordinary-user system services, then opens the gate.
No legacy package/runtime deletion is included: recoverable cleanup follows acceptance.

## Recovery and acceptance

Every staged directory also contains `rollback.sh`. After inspecting a failed phase,
use the same SSH/sudo command with **`rollback.sh` instead of `cutover.sh`** to return to
legacy supervision. It closes the new gate, disables/unloads new jobs, waits for quiescence,
copies *current* queues/ledger back when import completed (retaining the pre-return
legacy state), then restores original plists. Do not blindly restore an older snapshot.

After each cutover verify all eight new jobs loaded as the right UID, old labels absent,
Collector health and loopback listeners, preserved history/observed-since, scheduled
snapshot advancement, queue coverage/progress and fresh authenticated metrics/logs/traces
on Charizard. npm updates and version rollback thereafter use stop → exact-version npm
install → prepare → validate → start without sudo; retain current external state.

## Pre-existing legacy alerts — not staging regressions

Central check at 14:08 UTC found all active heartbeat ages under 46 seconds. Fred still
has missing host/self/cron/Codex signals and corresponding alerts active since about
08:15–08:18 UTC, before staging. Its actual unchanged Collector PID is 62754; local
queue storage is approximately 84 MB, with large agent/host queues. Do not discard those
queues during migration. A raw readiness series alone is not proof that its freshness
requirements are satisfied. New scratch checks passed, but delivery/backlog recovery
must be verified after cutover; migration is not claimed to fix this yet.

Ivy's legacy Codex sample reports `app_server_timeout` and success0, with its stale
alert active since 14:02:52 UTC (before Ivy preparation/preflight). The new scratch
Codex check succeeded at 14:05:27. Preserve this distinction and verify scheduled new
collection after cutover. Unrelated historical business-cron failure alerts were not
modified. No central alert policy or credential was changed.
