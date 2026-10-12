# Bill: official npm installation and legacy cleanup

Updated **2026-09-28 06:46 UTC**. Only Bill changed; Charizard verification was read-only.

## Current state

- Installed `@unblocklabs/fleet-node@0.1.0` from the public npm registry at
  **06:32:24 UTC**, replacing the local canary tarball installation in the same
  user-owned prefix: `/Users/billjohansson/.local/fleet-node-npm`.
- Used the supported explicit Node 24.18.0 and npm 11.16.0 paths under
  `/opt/homebrew/opt/node@24`, without sudo: stop → npm install → prepare → validate → start.
- All 41 protected configuration, credential, queue, durable state, textfile and log
  entries retained bytes, permissions and ownership through that stopped replacement.
  Transient job locks were excluded. Active state remains outside npm/OpenClaw at
  `/Users/billjohansson/.local/share/fleet-node`.
- All eight launchd jobs remain loaded. Collector PID 29948 and exporter PID 29945
  run as UID 501. Post-cleanup CLI validation passed.
- At **06:45:39 UTC**, session SQLite integrity was `ok`, with **1949 identities**
  and original `observed_since=1789873055.218782`. The original 1947 imported identities
  were preserved by the previously verified migration; scheduled collection continues.
- Central verification at **06:45:43 UTC** found fresh heartbeat/readiness/thermal and
  cron/session/Codex snapshots, all success gauges 1 and Codex `error_type=none`.
  The five-minute collectors advanced naturally after the registry installation.
  At 06:44:37 all six queues were zero, no Bill pending/firing stale/queue/readiness
  alerts were present, host samples were 26 seconds old and native OpenClaw RPC samples
  were 5 seconds old. This is bounded verification, not a long soak.

## Removed from Bill

At **06:43:30 UTC**, removed the exact ten user-owned directories and seven stale
textfiles listed in `../ops/bill-cleanup-targets.json`: old OpenClaw-hosted runtime,
old rollouts/hotfixes/quarantines, TS canary staging, Python cache and old exporter
textfiles. Old fleet labels remain disabled/unloaded, and their parked plists are
in the off-device archive. No active process or installed LaunchAgent/LaunchDaemon
referenced the deletion targets at preflight.

Also removed four exact local-canary npm cache keys and the unpublished
`0.1.1-canary-verification` cache content. Kept the official registry cache and its
shared `0.1.0` content. No blanket npm-cache purge.

Remote receipt: `/Users/billjohansson/.local/share/fleet-node/state/retired-cleanup-20260928.json`.
Independent, disabled Vector tooling and unrelated OpenClaw workspaces/configuration
backups were deliberately excluded; they are not fleet-node package installations.

## Recovery archive — on Bek's Mac only

Private directory: `/Users/bek/fleet-node-bill-recovery-20260928.zPBa3G`.

- `retired-fleet-files.tar.gz`: 97,699,840 bytes, mode 0600, 413 readable members.
- SHA256: `f22ab8b0d966e685f9f3bbe5d3eb95e8cf9d6e1702d6c347ed5764d01a82e256`.
- Contains all 19 manifest roots (including inactive root package and original capture
  plist); root coverage and archive checksum verified before deletion.
- `targets.json` and `archive-members.txt` identify exact contents. Archive includes
  private credentials/configuration: do not publish it or place it in this repository.

For recovery, extract into a private staging directory **on Bek's Mac**, inspect exact
required files, then copy only those needed to Bill. Do not overwrite the current queues
or ledger with old snapshots. Returning to the legacy runtime would require an explicit
service/state migration and restoring the archived canary rollback scripts/plists first.
Routine npm rollback instead uses an exact published version and retains current state.

## Remaining operator step

`/usr/local/fleet-node-observability` is inactive **v0.3.3**, root-owned, and still
present. Passwordless sudo is unavailable. Its exact-target cleanup script passed
shell syntax and live read-only preflight; it neither modifies services nor grants
additional privileges. Run:

```sh
ssh -t billsmacmini 'sudo /bin/sh /Users/billjohansson/.local/share/fleet-node/services/finish-legacy-cleanup.sh --remove'
```

Then verify the directory is absent and the npm CLI/status remain healthy. **Full
legacy removal is not complete until this operator step is verified.**

## Incident: separate Codex capture history lost during restart

The independent GUI Codex capture service reused the retired runtime's Collector.
It now uses `/Users/billjohansson/.local/share/fleet-node/bin/otelcol-contrib` (identical
0.157.0 binary, SHA256 `5caadd3c5f99484b70c2cc204422956942ea8151d6ad1baf5f226b10332b2f19`).

At 06:39 UTC the first relocation failed because editing the array element with
`plutil` produced incorrect arguments. Automatic plist recovery restarted the original
service. **That restart cleared the existing approximately 49.3 MB `otel-logs.json`:**
the pre-existing file exporter omitted `append`, whose upstream default is truncate.
The cleanup archive covered the capture plist, not this independent output file. This
was an execution error: its output should have been backed up before any restart.

No recovery copy was found in the inspected home folders or `Backups`; Time Machine
has no destination configured and the Data volume has no APFS snapshots. The old local
capture history has **not** been recovered. Fleet queues, fleet session history and
Codex usage metrics were not affected. No claim is made that this separate local log
is preserved elsewhere.

The plist was subsequently verified structurally with the whole arguments array replaced.
Capture config now sets `exporters.file/codex.append=true`, validated by the actual pinned
binary, so future starts retain output. A bootstrap race while the old process exited
was resolved by retrying after quiescence. PID **32855** is running as Bill on loopback
**4319**. The output remained empty at final inspection: listener readiness and corrected
configuration are verified, but new producer activity has not yet been observed.
Original capture config was separately copied into the private recovery directory.
