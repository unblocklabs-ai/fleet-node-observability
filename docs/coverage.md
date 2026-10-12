# Implementation and validation ledger

## Verified reference sources

- Node main 3f9eadf2dca45f10088871a01839ada72962aed1: README, central contract, complete agent/config/openclaw/atomic/textfile implementations; collector and installer contracts mapped by agents.
- Node newer revision de03ccc0642ec9a6370aef01ee3116e8079b3f4a: AGENTS, session collector/tests; module SHA-256 ba0c69391b604c94353eb339747481d323a00493998e1bde31e4b8e912d447ed matches live Bill.
- Central b354fb9953ddfa35bc9e38a4b6d7023311c36221: AGENTS, README, Alloy, alert/dashboard consumers/tests. Deployed Charizard matches this commit, version 0.1.11, clean worktree.
- Target absent (including symlink) before mkdir and git init; both reference worktrees initially clean.

## Work remaining

- [x] Concrete collector ports and behavioral tests.
- [x] Explicit Node runtime, pinned dependencies, stable launchd gate and npm update/rollback.
- [x] Packed CLI disposable integration, state preservation and subprocess/SQLite boundaries.
- [x] Independent agent review and meaningful fixes.
- [x] Recoverable Bill migration (operator completed the one-time privileged cutover).
- [x] Bill local and central signal parity, queues, schedules, and ordinary-user maintenance.

No dated deployment note is treated as live evidence. No central changes or other-node migrations authorized.

## Review and validation evidence

- Three implementation agents mapped the old node/central implementations and consumers before porting.
- Independent cross-module review fixed runtime/state nesting under replaceable packages (including symlinks), repeated termination interrupting process-tree cleanup, omitted symlinked session agent directories, wrong-root launchd PID adoption, and stale disabled Codex textfiles.
- Emergency Bill rollback closes the external gate and bootouts exact labels without depending on a working npm CLI.
- Supported Node 24.21.0 was downloaded from nodejs.org and verified against its published SHA-256. Bill's explicit Node 24.18.0/npm 11.16.0 also work; generic Homebrew node on Bill is broken and is not used.
- Full strict build and 44 tests passed with zero skips, including real pinned Collector filtering/routing and packed real npm upgrade/rollback. Subsequent packed allowlist test passed independently.
- Native prepare validated pinned Collector 0.157.0, node_exporter 1.11.1 and generated launchd plist syntax. No local system services were bootstrapped.
- Bill preflight found old session ledger count 1946, observed_since 1789873055.21878, six queue sizes zero, healthy central heartbeat/readiness/thermal/cron/sessions/Codex, and an extra old GUI thermal job to retire.
- Bill staging found a real 132851-byte OpenClaw package.json; discovery safety limit raised to 1 MiB with a regression. Initialization failed before creating state; old services were unaffected.
- Installed packed CLI real-source preflight on Bill passed all six collectors at 2026-09-27T23:06:10.674Z in a private scratch runtime (exit 0, success 1 each). Read-only SQLite backup and collection preserved 1946 session identities and observed_since 1789873055.218782; integrity check passed. OpenClaw config and legacy auth were unchanged, new gate remained closed, and no scratch telemetry was exported. Evidence: `/Users/billjohansson/fleet-node-ts-canary-20260927/preflight-bTBAmS/report.json` on Bill. Scheduled execution and new-Collector ingestion remain unverified.
- September 28 operator cutover stopped before state copy: an overbroad process-path check mistook unrelated Codex capture PID 1647 for the fleet Collector. Old jobs were disabled/unloaded and eight plists retained; fleet ports are free, destination state empty, main fleet monitoring offline. Corrected process matching and pre-copy resume were independently reviewed; disposable first-run/resume/rejection flows and legacy process detection are covered in `tests/cutover.test.mjs`. Capture config has no storage extensions and writes only under its own directory; retain the shared legacy Collector binary.
- Second operator attempt exposed `fs.cp` EEXIST on the prepared empty queue directory. Nothing copied; fixed by exclusive per-child copy. Added three passing real filesystem/SQLite migration tests, covering forward and reverse transfer, WAL data, unsafe destination rejection and preserved pre-return state. Prior shell tests mocked import and missed this defect. Independent review prompted pre-copy source ledger/integrity and destination queue-age checks.
- Bill real-state scratch rehearsal at 2026-09-28T05:52:33.066Z passed forward and reverse transfer on Node 24.18.0: 1947 identities, unchanged observed_since 1789873055.218782, all 13 queue entries preserved byte-for-byte. Production destinations remained empty and new gate closed. Evidence: `/Users/billjohansson/fleet-node-ts-canary-20260927/state-rehearsal-9dcWvJ/report.json`. All eight actual prepared plists and installer passed syntax validation; actual privileged bootstrap remains unverified.
- Operator completed actual bootstrap at 05:54:07Z. All eight new jobs loaded, daemon listeners only on loopback under UID501; old labels disabled/unloaded. First scheduled five-minute cycle succeeded at05:59:08; independent session/cron/Codex reconciliation is in `bill-collectors-verification.md`. Central post-cutover native logs/metrics/traces and all collector snapshots verified in `bill-central-verification.md`.
- Live ordinary-user maintenance at06:00:07–06:00:46Z passed npm0.1.0→0.1.1-canary-verification→0.1.0 plus explicit restart. Exact executable code unchanged in test-only package; no publication. Each closed gate survived actual KeepAlive retries; all42 protected entries retained bytes/modes/owners through npm and prepare/validate. Collector/exporter recovered with new PIDs asUID501. No sudo or system-plist reinstall used. Evidence on Bill: `/Users/billjohansson/fleet-node-ts-canary-20260927/maintenance-verification-ZsrkRd/report.json`.

## Acceptance

The operator completed Bill cutover at 2026-09-28T05:54:07Z. New services run as UID 501; old jobs remain disabled. Post-rollback recurrence at06:04:07–09Z passed for all five-minute jobs (runs3/exit0/success1), with 1947 imported observations intact and one independently source-confirmed addition. Central checks at06:05:57Z confirmed fresh successful collector signals and no relevant pending/firing monitoring alerts. No privilege policy changes or passwords were collected. Bill is accepted for staged fleet migration, with per-node preflight; no other nodes changed. Bounded ARM64 canary only, not an Intel/reboot/multi-day soak claim. Final receipts, limits and evidence links are in `bill-canary-status.md`.
