# OpenClaw OTLP Configuration

OpenClaw sends OTLP/HTTP to the node-local Collector at `http://127.0.0.1:4318`.

The installer updates `~/.openclaw/openclaw.json` for its explicit node account. The writer:

- validates that `diagnostics` and `diagnostics.otel` are objects;
- preserves unrelated configuration;
- enables diagnostics and OTLP logs, metrics, and traces using `http/protobuf`;
- sets `logsExporter` to `otlp` and removes stale signal-specific endpoint overrides;
- sets `serviceName` to `openclaw_gateway`;
- preserves an explicit `captureContent` choice, defaulting to false when absent;
- replaces the complete headers object with `{}`;
- makes a timestamped mode-`0600` backup when a prior file exists; and
- writes mode-`0600` JSON atomically with file and directory fsync.

With `captureContent` disabled, ordinary OpenClaw log bodies arrive as `log`. The Collector
drops only two low-severity structured successes: successful gateway authentication and successful
tool-policy removal. It uses no body-prefix filters. QMD and Codex source noise remains until upstream
provides stable structured discriminators for capture-off telemetry.

## Meaningful log bodies and privacy

The inspected OpenClaw 2026.9.2 producer has no logs-only capture switch.
`diagnostics.otel.captureContent=true` restores message bodies but also captures
model input/output, tool inputs/outputs, and tool definitions. Enable it only with
the operator's approval, using the installed OpenClaw CLI's guarded `config set`:

```sh
openclaw config set diagnostics.otel.captureContent true --strict-json --expect-current-json false
openclaw config validate --json
openclaw gateway restart --preserve-definition --wait 60s
```

Retain a private configuration backup first. Use `--expect-current-absent` instead
when the setting is absent, and skip the write if already true. Verify unrelated
settings are unchanged and actual producer logs arrive centrally. Reconfiguration
with node runtime 0.3.1 or later preserves either explicit choice; older installers
reset it to false. No central credential belongs in OpenClaw configuration.

The central Authorization header exists only in the Collector's protected secret file. A stale
Authorization value in OpenClaw is removed rather than merged.

Configuration validity proves only the local file contract. Runtime validation during the later
deployment must restart OpenClaw, generate each expected signal, and verify actual receipt through
the local Collector and Charizard storage.
