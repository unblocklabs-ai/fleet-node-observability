# Fleet Node (TypeScript)

Standalone macOS npm agent, not an OpenClaw plugin. Package version lives only in package.json.
Use strict TypeScript and Node 24 built-ins. Keep state, credentials, pinned binaries and logs outside npm and OpenClaw.
This is the canonical fleet-node-observability repository; the legacy Python implementation is in Git history.
Preserve schema-3, metric/label/freshness and central auth contracts.
Launchd alone supervises and schedules; separate processes isolate checks. Always close external maintenance gate and quiesce old processes before npm changes.
Do not deploy, migrate nodes, change permissions, or modify services without explicit user authorization.
Never print credentials or transcript content. Leave maintenance nodes and unmanaged hosts untouched.
Keep node-specific recovery scripts; dated deployment notes are historical evidence, not current state.
Validate compiled packed artifacts, real subprocess cleanup and preserved SQLite history; do not add implementation-freezing tests.
