/** Content-free native evidence. Runs only in the separately killable cron child. */
import { createHash } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, open, readdir, realpath } from 'node:fs/promises';
import { join, relative } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { object, type RuntimeContext } from './config.js';
import { ensurePrivateDir, isErrno, metric, readRegular, writeAtomic } from './io.js';
import { packageVersion } from './provision.js';

const CHUNK = 512 * 1024;
const MAX_OUTBOX = 10_000;
const hash = (value: unknown): string => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const token = (value: unknown): string => typeof value === 'string' && /^[A-Za-z][A-Za-z0-9_.:/-]*$/.test(value) && !/sk-|bearer|secret|password|token=/i.test(value) ? value : 'unknown';
const timestamp = (value: unknown): number | undefined => typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : undefined;
const identifier = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
type Outcome = 'pending' | 'success' | 'failure' | 'cancelled' | 'denied' | 'unknown';
type SourceContext = { harness_version: string; instruction_hash: string | null };
const emptyContext = (): SourceContext => ({ harness_version: 'unknown', instruction_hash: null });
const versionToken = (value: unknown): string => typeof value === 'string' && /^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/.test(value) ? value : 'unknown';

async function transportHealth(): Promise<Record<string, number> | null> {
  let response: Response;
  try { response = await fetch('http://127.0.0.1:8888/metrics', { signal: AbortSignal.timeout(2000) }); }
  catch { return null; }
  if (!response.ok) return null;
  let text: string;
  try { text = await response.text(); } catch { return null; }
  const metrics: Record<string, number> = {};
  for (const line of text.split('\n')) {
    const match = /^(otelcol_exporter_queue_size|otelcol_exporter_send_failed_log_records|otelcol_exporter_enqueue_failed_log_records|otelcol_exporter_sent_log_records|otelcol_receiver_refused_log_records)(?:_total)?\{([^}]+)\} ([\d.e+]+)$/.exec(line);
    if (!match?.[1] || !match[2] || !match[3]) continue;
    if (!(match[1].startsWith('otelcol_receiver_') ? match[2].includes('receiver="otlp/openclaw"') : match[2].includes('exporter="otlp_http/logs"'))) continue;
    const value = Number(match[3]);
    if (Number.isFinite(value) && value >= 0) metrics[match[1]] = (metrics[match[1]] ?? 0) + value;
  }
  return Object.keys(metrics).length ? metrics : null;
}

// A closed vocabulary, not a redaction regex over arbitrary private prose.
function diagnostics(texts: string[], inspected: boolean) {
  const codes = ['ENOENT', 'EACCES', 'EPERM', 'ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'ENOTFOUND', 'EAI_AGAIN', 'ENOSPC', 'EMFILE', 'EPIPE'] as const;
  const types = ['TypeError', 'ValueError', 'SyntaxError', 'ReferenceError', 'AssertionError', 'PermissionError', 'FileNotFoundError', 'TimeoutError', 'HTTPError', 'ConnectionError', 'JSONDecodeError', 'ValidationError'] as const;
  const markers = [
    ['rate_limit', /^(?:error:\s*)?(?:rate limit(?:ed| exceeded)|too many requests)\b/im],
    ['quota_exceeded', /^(?:error:\s*)?(?:quota exceeded|insufficient quota)\b/im],
    ['expired_cell', /^(?:error:\s*)?(?:unknown|expired|invalid) (?:execution |exec )?cell(?: id)?\b/im],
    ['invalid_arguments', /^(?:error:\s*)?(?:invalid (?:arguments?|parameters?)|validation (?:error|failed))\b/im],
    ['tls_failure', /^(?:error:\s*)?(?:certificate verify failed|SSL certificate problem)\b/im],
    ['command_not_found', /^(?:zsh|bash|sh):[^\n]*(?:command not found|not found:)/m],
    ['cli_usage_error', /^error: (?:unrecognized arguments|unknown option|unexpected argument|the following arguments are required)\b/im],
    ['patch_context_mismatch', /^(?:Invalid Context|Failed to find expected lines|Invalid patch|Failed to read file to update)\b/m],
    ['git_revision_missing', /^fatal: (?:not a git repository|couldn't find remote ref|ambiguous argument|bad revision|pathspec)\b/m],
    ['tool_connection_missing', /^(?:Error: )?(?:Tool (?:is )?not connected|No active connection|Tool (?:is )?not attached)\b/im],
  ] as const;
  const structured: Record<string, unknown>[] = [];
  for (const text of texts) {
    let value: unknown;
    try { value = JSON.parse(text) as unknown; } catch { continue; }
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
    const record = object(value);
    const error = record.error;
    structured.push(error && typeof error === 'object' && !Array.isArray(error) ? object(error) : record);
  }
  return { version: 'safe-diagnostics-2', output_present: texts.some(text => text.length > 0),
    inspection: !inspected ? 'not_inspected' : texts.some(text => text.length > 0) ? 'inspected' : 'empty',
    truncated: false,
    codes: codes.filter(code => structured.some(value => value.code === code) || texts.some(text => new RegExp(`^(?:Error: |error: |[a-z]+ )?${code}\\b`, 'm').test(text))),
    error_types: types.filter(type => structured.some(value => value.name === type || value.type === type) || texts.some(text => new RegExp(`^(?:[a-zA-Z_][a-zA-Z0-9_]*\\.)*${type}:`, 'm').test(text))),
    markers: markers.filter(([, pattern]) => texts.some(text => pattern.test(text))).map(([name]) => name) };
}

function packetFromLine(line: string, agent: string, version: string, now: number, context: SourceContext): Record<string, unknown> | null {
  const event = object(JSON.parse(line) as unknown);
  if (!event.payload || typeof event.payload !== 'object' || Array.isArray(event.payload)) return null;
  const payload = object(event.payload);
  if (event.type === 'session_meta') {
    context.harness_version = versionToken(payload.cli_version);
    const base = payload.base_instructions;
    context.instruction_hash = base && typeof base === 'object' && 'text' in base && typeof base.text === 'string' ? hash(base.text) : null;
    return null;
  }
  if (payload.type !== 'item_started' && payload.type !== 'item_completed') return null;
  const item = object(payload.item);
  if (item.type !== 'DynamicToolCall' && item.type !== 'CommandExecution') return null;
  const phase = payload.type === 'item_started' ? 'started' : 'completed';
  const started = timestamp(payload.started_at_ms);
  const completed = timestamp(payload.completed_at_ms);
  const wrapperTime = typeof event.timestamp === 'string' ? Date.parse(event.timestamp) : undefined;
  const occurred = wrapperTime && Number.isSafeInteger(wrapperTime) ? wrapperTime : phase === 'completed' ? completed : started;
  if (!occurred || occurred > now + 30_000) throw new Error('unsupported_timestamp');
  const attempt = identifier(payload.thread_id) && identifier(payload.turn_id) && identifier(item.id)
    ? hash([agent, payload.thread_id, payload.turn_id, item.id]) : null;
  const id = attempt ? hash([attempt, phase, occurred]) : hash([agent, line]);
  let outcome: Outcome = 'unknown';
  const exit = typeof item.exit_code === 'number' && Number.isInteger(item.exit_code) && item.exit_code >= -255 && item.exit_code <= 65535 ? item.exit_code : null;
  if (phase === 'started') outcome = 'pending';
  else if (item.status === 'cancelled' || item.status === 'canceled' || item.status === 'interrupted') outcome = 'cancelled';
  else if (item.status === 'denied' || item.status === 'declined') outcome = 'denied';
  else if (item.success === false || item.status === 'failed' || (exit !== null && exit !== 0)) outcome = 'failure';
  else if (item.success === true || (item.type === 'CommandExecution' && exit === 0)) outcome = 'success';
  // Inspect complete output text in memory, and only for terminal failures.
  // Parameters, command text, prompts, and arbitrary excerpts never leave the host.
  const texts: string[] = [];
  if (outcome === 'failure' || outcome === 'unknown') {
    for (const key of ['stderr', 'aggregated_output']) if (typeof item[key] === 'string') texts.push(item[key]);
    if (Array.isArray(item.content_items)) for (const entry of item.content_items) {
      if (entry && typeof entry === 'object' && 'text' in entry && typeof entry.text === 'string') texts.push(entry.text);
    }
  }
  const signals: string[] = [];
  const patterns = [
    ['missing_handler', /^(?:error:\s*)?(?:OpenClaw did not register a handler for this app-server tool call\.|tool handler not found|no (?:registered )?handler (?:found )?for (?:tool|dynamic tool))/i],
    ['timed_out', /^(?:error:\s*)?(?:execution|request|connection|command|tool) timed out\b/i],
    ['permission_denied', /^(?:error:\s*)?permission denied\b/i],
    ['not_found', /^(?:error:\s*)?(?:command not found|no such file or directory)\b/i],
    ['connection_refused', /^(?:error:\s*)?(?:connect ECONNREFUSED|connection refused)\b/i],
  ] as const;
  for (const [signal, pattern] of patterns) if (texts.some(text => pattern.test(text.trim()))) signals.push(signal);
  if (signals.includes('missing_handler')) outcome = 'failure';
  let http: number | null = null;
  for (const text of texts) {
    const match = /^(?:HTTP\/\d(?:\.\d)?\s+|HTTP (?:error|status)[: ]+|(?:[A-Za-z_][A-Za-z0-9_]*\.)*HTTPError:\s*)([45]\d\d)\b/im.exec(text.trim());
    if (match?.[1]) http = Number(match[1]);
  }
  let executable = 'unknown';
  // Native Codex records argv, usually ["/bin/zsh", "-lc", command]. Only
  // unwrap that exact shell shape; never join argv into an invented shell line.
  const argv = Array.isArray(item.command) && item.command.every(value => typeof value === 'string') ? item.command : null;
  const command = typeof item.command === 'string' ? item.command
    : argv?.length === 3 && /^(?:\/bin\/)?(?:zsh|bash|sh)$/.test(argv[0] ?? '') && ['-c', '-lc'].includes(argv[1] ?? '') ? argv[2] : undefined;
  if (item.type === 'CommandExecution' && typeof command === 'string') {
    const match = /^(rg|grep|find|curl|python(?:3)?|node|git)(?:\s|$)/.exec(command.trim());
    // Shell composition makes an exit code ambiguous; do not call it "no match".
    executable = match?.[1] && !/[|;&\n`$<>]/.test(command) ? match[1].replace('python3', 'python') : 'other';
  }
  const duration = phase === 'completed' && started && completed && completed >= started && completed - started <= 90 * 86400_000 ? completed - started : null;
  return { schema: 4, kind: 'observation', id, source: 'codex_native', occurred_ms: occurred, observed_ms: now,
    attempt_id: attempt, population: item.type === 'DynamicToolCall' ? 'dynamic_tool' : 'command_execution', phase,
    tool: item.type === 'CommandExecution' ? 'command' : token(item.tool), namespace: token(item.namespace), outcome,
    duration_ms: duration, timing_basis: duration === null ? 'unavailable' : 'native_item_interval', signals,
    exit_code: exit, http_status: http, executable, agent: hash(agent), node_agent_version: version,
    diagnostics: diagnostics(texts, outcome === 'failure' || outcome === 'unknown'), harness: 'codex', ...context };
}

async function scan(context: RuntimeContext, db: DatabaseSync, now: number, version: string): Promise<void> {
  const count = (sql: string): number => Number(db.prepare(sql).get()?.n ?? 0);
  const set = (key: string, value: number): void => { db.prepare('INSERT OR REPLACE INTO counters VALUES (?,?)').run(key, value); };
  const get = (key: string): number => Number(db.prepare('SELECT value FROM counters WHERE key=?').get(key)?.value ?? 0);
  const increment = (key: string): void => set(key, get(key) + 1);
  const newSweep = !count('SELECT count(*) n FROM directories') && !count('SELECT count(*) n FROM files WHERE checked < (SELECT value FROM counters WHERE key=\'scan_started_ms\')');
  {
    const agents = await readdir(join(context.settings.openclawRoot, 'agents'), { withFileTypes: true });
    if (agents.length > 32) throw new Error('evidence_agent_limit');
    for (const entry of agents) {
      if (!entry.isDirectory() && !entry.isSymbolicLink()) continue;
      const source = join(context.settings.openclawRoot, 'agents', entry.name, 'agent/codex-home/sessions');
      try {
        const root = await realpath(source);
        if (!(await lstat(root)).isDirectory()) continue;
        if (newSweep) db.prepare('INSERT OR IGNORE INTO directories VALUES (?,?,?)').run(root, root, entry.name);
        // Discover live date directories even while the historical sweep lags.
        for (const age of [0, 86400_000]) {
          const day = new Date(now - age).toISOString().slice(0, 10).replaceAll('-', '/');
          const active = join(root, day);
          try { if ((await lstat(active)).isDirectory()) db.prepare('INSERT OR IGNORE INTO directories VALUES (?,?,?)').run(active, root, entry.name); }
          catch (error) { if (!isErrno(error, 'ENOENT')) throw error; }
        }
      } catch (error) { if (!isErrno(error, 'ENOENT')) throw error; }
    }
    if (newSweep && !count('SELECT count(*) n FROM directories')) throw new Error('evidence_source_missing');
    if (newSweep) set('scan_started_ms', now);
  }
  for (let index = 0; index < 50; index++) {
    const row = db.prepare('SELECT * FROM directories ORDER BY path DESC LIMIT 1').get();
    if (!row) break;
    const path = String(row.path), root = String(row.root), agent = String(row.agent);
    try {
      const entries = await readdir(path, { withFileTypes: true });
      if (entries.length > 20_000) throw new Error('evidence_directory_limit');
      for (const entry of entries) {
        const child = join(path, entry.name);
        if (entry.isDirectory() && relative(root, child).split('/').length <= 8) db.prepare('INSERT OR IGNORE INTO directories VALUES (?,?,?)').run(child, root, agent);
        else if (entry.isFile() && entry.name.endsWith('.jsonl')) {
          const day = relative(root, child).slice(0, 10);
          db.prepare('INSERT INTO files(path,agent,day) VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET day=excluded.day')
            .run(child, agent, /^\d{4}\/\d{2}\/\d{2}$/.test(day) ? day : '');
        }
      }
    } catch (error) { if (!isErrno(error, 'ENOENT')) throw error; increment('rewrites'); }
    db.prepare('DELETE FROM directories WHERE path=?').run(path);
    if (count('SELECT count(*) n FROM files') > 50_000 || count('SELECT count(*) n FROM directories') > 10_000) throw new Error('evidence_inventory_limit');
  }
  const started = get('scan_started_ms');
  const recent = new Date(now - 2 * 86400_000).toISOString().slice(0, 10).replaceAll('-', '/');
  const hot = db.prepare('SELECT * FROM files WHERE day>=? ORDER BY visited,path LIMIT 32').all(recent);
  const cold = db.prepare('SELECT * FROM files WHERE checked < ? ORDER BY visited,path LIMIT 64').all(started);
  const selected = [...new Map([...hot, ...cold].map(row => [row.path, row])).values()].slice(0, 64);
  for (const row of selected) {
    if (count('SELECT count(*) n FROM outbox') >= MAX_OUTBOX) break;
    const path = String(row.path);
    let handle;
    try { handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK); }
    catch (error) {
      if (!isErrno(error, 'ENOENT')) throw error;
      increment('rewrites'); db.prepare('DELETE FROM files WHERE path=?').run(path); continue;
    }
    try {
      const stat = await handle.stat();
      if (!stat.isFile() || stat.uid !== process.getuid?.()) throw new Error('evidence_source_not_owned_regular_file');
      let offset = Number(row.offset);
      let sourceContext: SourceContext = { harness_version: String(row.harness_version), instruction_hash: row.instruction_hash === null ? null : String(row.instruction_hash) };
      const anchor = Buffer.alloc(Math.min(offset, 256));
      await handle.read(anchor, 0, anchor.length, offset - anchor.length);
      if (String(stat.dev) + ':' + String(stat.ino) !== row.inode || stat.size < offset || (row.anchor && hash(anchor.toString('base64')) !== row.anchor)) {
        if (row.inode) increment('rewrites');
        offset = 0; sourceContext = emptyContext();
      }
      const initialOffset = offset;
      let position = offset;
      let fragments: Buffer[] = [];
      let fragmentBytes = 0;
      // CHUNK schedules work and sizes I/O; it is never a record-size limit.
      // Complete a spanning record before yielding. Only LF frames JSONL:
      // readline also splits valid JSON strings at U+2028/U+2029 in Node 24.
      reading: while (position < stat.size) {
        const buffer = Buffer.alloc(Math.min(CHUNK, stat.size - position));
        const { bytesRead } = await handle.read(buffer, 0, buffer.length, position);
        if (!bytesRead) break;
        let consumed = 0;
        for (let index = 0; index < bytesRead; index++) {
          if (buffer[index] !== 10) continue;
          const part = buffer.subarray(consumed, index);
          const record = fragments.length ? Buffer.concat([...fragments, part], fragmentBytes + part.length) : part;
          let packet: Record<string, unknown> | null = null;
          try {
            packet = packetFromLine(new TextDecoder('utf-8', { fatal: true }).decode(record), String(row.agent), version, now, sourceContext);
          } catch { increment('parse_errors'); }
          // Storage failures must roll back the offset, not masquerade as bad JSON.
          if (packet) {
              set('source_latest_ms', Math.max(get('source_latest_ms'), Number(packet.occurred_ms)));
              if (Number(packet.occurred_ms) >= now - 90 * 86400_000) {
                const { observed_ms: _observed, ...facts } = packet;
                const key = hash(facts);
                if (!db.prepare('SELECT 1 FROM sent WHERE key=? AND transport_version=2').get(key)) db.prepare('INSERT OR IGNORE INTO outbox VALUES (?,?,?)').run(key, JSON.stringify(packet), now);
              }
          }
          fragments = []; fragmentBytes = 0;
          consumed = index + 1;
          offset = position + consumed;
          if (offset - initialOffset >= CHUNK || count('SELECT count(*) n FROM outbox') >= MAX_OUTBOX) break reading;
        }
        if (consumed < bytesRead) {
          const part = buffer.subarray(consumed, bytesRead);
          fragments.push(part); fragmentBytes += part.length;
        }
        position += bytesRead;
      }
      // An unfinished record stays at its start, regardless of its size. Raw
      // fragments remain in memory only and are reread after the producer appends.
      const tail = Buffer.alloc(Math.min(offset, 256));
      await handle.read(tail, 0, tail.length, offset - tail.length);
      const after = await handle.stat();
      if (after.size < stat.size || after.ino !== stat.ino || after.size === stat.size && after.mtimeMs !== stat.mtimeMs) throw new Error('evidence_source_rewritten_during_read');
      db.prepare('UPDATE files SET offset=?,inode=?,anchor=?,dropping=0,checked=?,visited=?,harness_version=?,instruction_hash=? WHERE path=?')
        .run(offset, String(stat.dev) + ':' + String(stat.ino), hash(tail.toString('base64')), Math.max(position, offset) >= stat.size ? now : 0, now, sourceContext.harness_version, sourceContext.instruction_hash, path);
    } finally { await handle.close(); }
  }
  const pending = count(`SELECT count(*) n FROM files WHERE checked < ${started}`) + count('SELECT count(*) n FROM directories');
  if (!pending) set('last_full_scan_ms', now);
  const coverage = { schema: 4, kind: 'coverage', id: hash(['coverage', now]), source: 'codex_native', occurred_ms: now, observed_ms: now,
    last_full_scan_ms: get('last_full_scan_ms'), scan_started_ms: started, source_latest_ms: get('source_latest_ms'),
    parse_errors: get('parse_errors'), unsupported: get('unsupported'), rewrites: get('rewrites'), pending_files: pending,
    outbox_records: count('SELECT count(*) n FROM outbox'), scan_complete: pending === 0, transport: await transportHealth() };
  // Keep the newest coverage packet during outages, without crowding out facts.
  db.prepare('INSERT OR REPLACE INTO outbox VALUES (?,?,?)').run('coverage', JSON.stringify(coverage), now);
}

async function deliver(db: DatabaseSync): Promise<void> {
  for (let batch = 0; batch < 8; batch++) {
    const rows = db.prepare('SELECT * FROM outbox ORDER BY created,key LIMIT 128').all();
    if (!rows.length) return;
    const observed = String(BigInt(Date.now()) * 1_000_000n);
    const response = await fetch('http://127.0.0.1:4318/v1/logs', { method: 'POST', signal: AbortSignal.timeout(4000), headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resourceLogs: [{ resource: { attributes: [{ key: 'service.name', value: { stringValue: 'fleet-node-evidence' } }] }, scopeLogs: [{ scope: { name: 'fleet.evidence', version: '1' }, logRecords: rows.map(row => ({
        // Loki deduplicates timestamp + body, not structured metadata. The
        // content-free fact key distinguishes records sharing a batch time.
        timeUnixNano: observed, observedTimeUnixNano: observed, severityNumber: 9, body: { stringValue: `fleet tool evidence v1 ${String(row.key)}` },
        attributes: [{ key: 'fleet.evidence', value: { stringValue: String(row.packet) } }],
      })) }] }] }) });
    if (!response.ok) throw new Error('evidence_export_rejected');
    const body = await response.text();
    const acknowledgement = body ? object(JSON.parse(body) as unknown) : {};
    if (acknowledgement.partialSuccess !== undefined) {
      const partial = object(acknowledgement.partialSuccess);
      if (Number(partial.rejectedLogRecords ?? 0) !== 0) throw new Error('evidence_export_partial_rejection');
    }
    // Acceptance by the loopback Collector, NOT a central end-to-end receipt.
    db.exec('BEGIN IMMEDIATE');
    try {
      for (const row of rows) {
        if (row.key !== 'coverage') db.prepare('INSERT INTO sent(key,accepted_ms,transport_version) VALUES (?,?,2) ON CONFLICT(key) DO UPDATE SET accepted_ms=excluded.accepted_ms,transport_version=2').run(String(row.key), Date.now());
        db.prepare('DELETE FROM outbox WHERE key=?').run(String(row.key));
      }
      db.exec('COMMIT');
    } catch (error) { db.exec('ROLLBACK'); throw error; }
  }
}

export async function collectEvidence(context: RuntimeContext): Promise<number> {
  try {
    if ((await readRegular(join(context.root, 'config/evidence.enabled'), 16, true)).trim() !== 'v1') throw new Error('invalid_evidence_opt_in');
  } catch (error) { if (isErrno(error, 'ENOENT')) return 0; throw error; }
  const directory = join(context.root, 'state/evidence');
  await ensurePrivateDir(directory);
  const path = join(directory, 'evidence.sqlite');
  try {
    const st = await lstat(path);
    if (!st.isFile() || st.isSymbolicLink() || st.uid !== process.getuid?.() || (st.mode & 0o077)) throw new Error('evidence_database_not_private');
  } catch (error) { if (!isErrno(error, 'ENOENT')) throw error; }
  const db = new DatabaseSync(path, { timeout: 500 });
  const lock = new DatabaseSync(join(directory, 'worker-lock.sqlite'), { timeout: 100 });
  let success = false;
  try {
    // A SQLite writer lock releases automatically on crash/kill. No stale PID lock.
    lock.exec('BEGIN EXCLUSIVE');
    const schema = Number(db.prepare('PRAGMA user_version').get()?.user_version);
    if (![0, 1, 2, 3, 4, 5].includes(schema)) throw new Error('unsupported_evidence_database');
    db.exec(`PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
      CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,agent TEXT,offset INTEGER DEFAULT 0,inode TEXT DEFAULT '',anchor TEXT DEFAULT '',dropping INTEGER DEFAULT 0,checked INTEGER DEFAULT 0);
      CREATE TABLE IF NOT EXISTS directories(path TEXT PRIMARY KEY,root TEXT,agent TEXT);
      CREATE TABLE IF NOT EXISTS counters(key TEXT PRIMARY KEY,value INTEGER);
      CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY,packet TEXT,created INTEGER);
      CREATE TABLE IF NOT EXISTS sent(key TEXT PRIMARY KEY,accepted_ms INTEGER);`);
    if (schema < 2) db.exec(`BEGIN IMMEDIATE;
      ALTER TABLE files ADD COLUMN day TEXT NOT NULL DEFAULT '';
      ALTER TABLE files ADD COLUMN visited INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE files ADD COLUMN harness_version TEXT NOT NULL DEFAULT 'unknown';
      ALTER TABLE files ADD COLUMN instruction_hash TEXT;
      UPDATE files SET offset=0,inode='',anchor='',dropping=0,checked=0;
      PRAGMA user_version=2; COMMIT;`);
    if (schema < 4) db.exec(`BEGIN IMMEDIATE;
      UPDATE files SET offset=0,inode='',anchor='',dropping=0,checked=0,harness_version='unknown',instruction_hash=NULL;
      PRAGMA user_version=4; COMMIT;`);
    if (schema < 5) db.exec(`BEGIN IMMEDIATE;
      ALTER TABLE sent ADD COLUMN transport_version INTEGER NOT NULL DEFAULT 1;
      UPDATE files SET offset=0,inode='',anchor='',dropping=0,checked=0,harness_version='unknown',instruction_hash=NULL;
      PRAGMA user_version=5; COMMIT;`);
    const version = await packageVersion();
    db.exec('BEGIN IMMEDIATE');
    try { await scan(context, db, Date.now(), version); db.exec('COMMIT'); }
    catch (error) { db.exec('ROLLBACK'); throw error; }
    await deliver(db);
    db.prepare('DELETE FROM sent WHERE accepted_ms < ?').run(Date.now() - 90 * 86400_000);
    success = true;
    return 0;
  } catch { return 1; }
  finally {
    db.close(); lock.close();
    await writeAtomic(join(context.root, 'textfiles/evidence.prom'),
      metric('fleet_node_evidence_collection_success', 'Last opt-in evidence collection/export succeeded (not a central receipt)', Number(success), {})
      + metric('fleet_node_evidence_collection_timestamp_seconds', 'Last opt-in evidence collection attempt', Date.now() / 1000, {}), 0o644);
  }
}
