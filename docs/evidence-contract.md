# Native tool evidence (opt-in)

`fleet-node collect evidence --root /absolute/runtime` reads managed native Codex
tool lifecycle events and exports content-free schema-4 evidence facts through the existing
loopback Collector. It does nothing unless private `config/evidence.enabled`
contains `v1`. The normal cron collector runs it as an independent 45-second child.
No new launchd service or schema-3 field is introduced.

Only native DynamicToolCall and CommandExecution start/completion observations
are supported. Messages, prompts, arguments and output are never exported or
persisted in derived state. Complete output is inspected in memory for fixed
anchored diagnostics, including the exact OpenClaw missing-handler receipt.
Diagnostics use a closed vocabulary of OS codes, exception types and markers,
plus output-present/truncation flags and `inspection=inspected|empty|not_inspected`;
successful output is deliberately not inspected. No arbitrary excerpt is redacted and sent.
Reviewed shell-command, CLI usage, patch-context, Git-revision and missing-tool-connection
markers extend the vocabulary without copying private arguments or error prose.
Native shell argv (`/bin/zsh -lc`, bash/sh equivalents) is unwrapped only for the
exact three-element shape; composed commands remain ambiguous. Empty output is
reported as absent. These boundaries were checked against the Bill/Fred Farrynheight/
Pearl read-only evaluation and the packed CLI; no agent/service rollout is implied.
Historical Codex version and a base-instruction fingerprint come only from native
session metadata. They are not proof of all runtime configuration or prompt adoption.
Timing is a native item interval or unavailable, not a
process/runtime estimate. Missing correlation is unmatched, not an attempt.

`state/evidence/` contains private SQLite file checkpoints, sanitized outbox and
accepted hashes. Offsets and facts commit together; interrupted delivery replays.
The central ledger owns attempt deduplication and late completion. Rotation and
checkpoint-boundary rewrites trigger replay. Arbitrary historical in-place edits
before the byte anchor are outside the append-only source contract.

Collection is scheduled (32 agents, 50,000 files, 50 discovery directories and 64
file visits per run, 10,000 outbox facts). I/O uses 512-KiB chunks but completes
spanning records without truncating or dropping them. Up to 32 slots prioritize recent days;
remaining slots rotate historical files by last visit. Today/yesterday discovery
continues during catch-up, preventing old inventories from hiding new files.
Partial records of any size wait at their starting offset. JSONL is framed only
by LF bytes, not Unicode separators inside JSON strings. Coverage reports scan
freshness, incomplete work, parsing/unsupported records and rewrites. No readable
source is failure, not a healthy idle observation.
Local schema-1/2/3 checkpoint databases migrate to schema 4 without deleting queued
evidence/history; offsets replay to recover formerly skipped records and complete
diagnostics. The central worker accepts all four evidence schemas, preferring the
new normalization for the same source observation. This evidence version is distinct
from the unchanged four-field node configuration schema. Other harnesses and compressed archives remain unsupported,
not silently treated as covered.

OTLP partial rejection keeps the whole batch for retry. Loopback acceptance is
not a central receipt: existing Collector queues have finite retry/size limits.
Check central coverage and Collector drops. Evidence collection success and
attempt timestamp gauges use the existing textfile path. The evidence marker can
be removed to disable future runs without deleting checkpoints/history.

Schema-4 coverage includes actual loopback Collector log queue/failure/refusal counters.
Unavailable self-metrics stay null; absent optional counters are not invented zeros.
The outbox count is sampled before delivery, so new pending work is not itself proof
of loss. Central comparisons require historical delivery health and withhold
improvement claims on missing history, changed error counters or end-window backlog.

The central `docs/error-intelligence.md` guide in the sister `fleet-observability`
repository documents strict fields, accounting, historical admission, 90-day
compact retention, safe Grafana projection and inference budgets.

Do not enable on real nodes without canary/rollout approval. Preserve the normal
maintenance gate, independent collector schedules and maintenance holds.
