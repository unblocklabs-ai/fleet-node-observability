# Native tool evidence (opt-in)

`fleet-node collect evidence --root /absolute/runtime` reads managed native Codex
tool lifecycle events and exports schema-5 facts and complete redacted output through the existing
loopback Collector. It does nothing unless private `config/evidence.enabled`
contains `v1`. The normal cron collector runs it as an independent 45-second child.
No new launchd service or schema-3 field is introduced.

Only native DynamicToolCall and CommandExecution start/completion observations
are supported. Messages, prompts and tool arguments are not collected. Completed
items retain all present `stdout`, `stderr`, `aggregated_output`, `output`, `error`,
`result` and `content_items` fields in `context.text`, without excerpts or truncation.
Credential-valued keys and recognizable secret strings are redacted before export.
Redaction is best effort, not proof that arbitrary output contains no sensitive data.
Complete output is also inspected for fixed anchored diagnostic hints.
Diagnostics use a closed vocabulary of OS codes, exception types and markers,
plus output-present/truncation flags and `inspection=inspected|empty|not_inspected`;
successful output is inspected too: successful wrappers can contain actual errors.
Reviewed shell-command, CLI usage, patch-context, Git-revision and missing-tool-connection
markers are hints, not limits on what Jev can understand from the original prose.
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

There are no agent/file/directory inventory or nesting-depth cutoffs. Collection
is scheduled (50 discovery directories and 64 file visits per run, with backpressure
at 10,000 outbox facts). These defer work without discarding it. I/O uses 512-KiB chunks but completes
spanning records without truncating or dropping them. Up to 32 slots prioritize recent days;
remaining slots rotate historical files by last visit. Today/yesterday discovery
continues during catch-up, preventing old inventories from hiding new files.
Partial records of any size wait at their starting offset. JSONL is framed only
by LF bytes, not Unicode separators inside JSON strings. Coverage reports scan
freshness, incomplete work, parsing/unsupported records and rewrites. No readable
source is failure, not a healthy idle observation.
Local schema-1/2/3/4/5/6 checkpoint databases migrate to version 7 without deleting queued
evidence/history; offsets replay to recover formerly skipped records and complete
diagnostics and original output. The central worker accepts all five evidence schemas, preferring the
new normalization for the same source observation. This evidence version is distinct
from the unchanged four-field node configuration schema. Other harnesses and compressed archives remain unsupported,
not silently treated as covered.

The log body contains the full packet, with `fleet.evidence=body-v2` as a small
metadata marker. This avoids Loki's structured-metadata limit and preserves distinct
bodies at equal timestamps. Old envelopes remain readable. The node redacts before
JSON serialization; central unstructured regexes must not rewrite escaped JSON.

OTLP partial rejection keeps the whole batch for retry. Loopback acceptance is
not a central receipt: Collector queues have finite memory/disk capacity. Log export
now sends whole individual records (not byte-split batches that drop large items),
and transient delivery retries no longer expire after 24 hours. HTTP transport
accepts 64 MiB (Jev's 32-MiB body plus JSON-envelope escaping); larger rejected
exports stay in the source/outbox, never truncated.
If a multi-record envelope receives HTTP 413, delivery retries smaller groups of
whole records. A receiver-sized record must not get stuck behind an oversized batch.
There is no asynchronous log batch processor before the persistent queue: a full
queue must reject the caller before acknowledgement. Version 7 replays earlier
acknowledgements once (transport version 3) to recover possible pre-queue loss.
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
