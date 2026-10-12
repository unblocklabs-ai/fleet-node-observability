import { createHash } from 'node:crypto';
import { chmodSync, closeSync, lstatSync, mkdirSync, openSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import type { RuntimeContext } from './config.js';
import { runtimeEnv } from './config.js';
import { ensurePrivateDir, labels, metric, writeAtomic } from './io.js';
import { runCommand } from './process.js';

const MAX_AGENTS = 32;
const MAX_ROWS = 200_000;
const ZONE = 'America/New_York';
const dateFormatter = new Intl.DateTimeFormat('en-US', { timeZone: ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
interface Counts { all: number; cron: number }
type DailyCounts = Map<string, Counts>;
type Grain = 'daily' | 'weekly' | 'monthly';
interface Snapshot { counts: DailyCounts; undated: number; since: number }
const finitePositive = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0;

function parts(seconds: number): Record<string, string> {
  return Object.fromEntries(dateFormatter.formatToParts(new Date(seconds * 1000)).map(part => [part.type, part.value]));
}

export function localDay(seconds: number): string {
  const value = parts(seconds);
  return `${value.year}-${value.month}-${value.day}`;
}

function shiftDay(day: string, offset: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}

export function retentionStart(today: string): string {
  return `${Number(today.slice(0, 4)) - 1}${today.slice(4, 7)}-01`;
}

/** Resolve calendar midnight instead of subtracting fixed 24-hour durations across DST. */
export function periodStart(day: string): { label: string; seconds: number } {
  const utc = Date.parse(`${day}T00:00:00Z`) / 1000;
  let probe = utc + 6 * 3600;
  let offset = 0;
  for (let index = 0; index < 3; index++) {
    const value = parts(probe);
    offset = Date.UTC(Number(value.year), Number(value.month) - 1, Number(value.day), Number(value.hour), Number(value.minute), Number(value.second)) / 1000 - probe;
    probe = utc - offset;
  }
  const absolute = Math.abs(offset);
  const zone = `${offset < 0 ? '-' : '+'}${String(Math.floor(absolute / 3600)).padStart(2, '0')}:${String(Math.floor(absolute % 3600 / 60)).padStart(2, '0')}`;
  return { label: `${day}T00:00:00${zone}`, seconds: probe };
}

function bucketFor(day: string, grain: Grain): string {
  if (grain === 'monthly') return `${day.slice(0, 7)}-01`;
  if (grain === 'weekly') return shiftDay(day, -((new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7));
  return day;
}

function sourcePaths(root: string): string[] {
  const paths: string[] = [];
  for (const entry of readdirSync(join(root, 'agents'), { withFileTypes: true })) {
    if (!entry.isDirectory()) {
      if (!entry.isSymbolicLink()) continue;
      try { if (!statSync(join(root, 'agents', entry.name)).isDirectory()) continue; }
      catch (error) { if (error instanceof Error && 'code' in error && error.code === 'ENOENT') continue; throw error; }
    }
    const path = join(root, 'agents', entry.name, 'agent/openclaw-agent.sqlite');
    let exists = false;
    try {
      const source = lstatSync(path);
      if (!source.isFile() && !source.isSymbolicLink()) throw new Error('invalid_source');
      exists = true;
    }
    catch (error) { if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error; }
    if (!exists) continue;
    if (!/^[A-Za-z0-9_-]{1,80}$/.test(entry.name)) throw new Error('invalid_agent');
    paths.push(path);
  }
  if (!paths.length || paths.length > MAX_AGENTS) throw new Error('source_missing_or_unbounded');
  return paths.sort();
}

/** This synchronous work runs only in a separately killable collector subprocess. */
export function collectSessionData(root: string, state: string, now: number): Snapshot {
  const today = localDay(now);
  const records = new Map<string, { day: string; cron: number }>();
  let undated = 0;
  let sourceRows = 0;
  for (const path of sourcePaths(root)) {
    const agent = path.split('/').at(-3);
    if (!agent) throw new Error('invalid_agent');
    const db = new DatabaseSync(realpathSync(path), { readOnly: true, timeout: 2000 });
    try {
      // readOnly honors live WAL. immutable mode and DB-only copies would silently miss it.
      const rows = db.prepare(`SELECT w.session_id, w.session_key, w.started_at,
        CASE WHEN json_extract(n.entry_json, '$.sessionId') = w.session_id
          AND json_type(n.entry_json, '$.sessionStartedAt') IN ('integer', 'real')
          THEN json_extract(n.entry_json, '$.sessionStartedAt') END AS lifecycle_start
        FROM session_windows w LEFT JOIN session_nodes n
          ON n.session_key = w.session_key AND n.current_session_id = w.session_id LIMIT ?`).all(MAX_ROWS + 1);
      sourceRows += rows.length;
      if (sourceRows > MAX_ROWS) throw new Error('source_unbounded');
      for (const row of rows) {
        if (typeof row.session_id !== 'string' || !row.session_id) throw new Error('invalid_identity');
        const started = [row.lifecycle_start, row.started_at].find(finitePositive);
        if (started === undefined) { undated++; continue; }
        if (started > now * 1000 + 30_000) throw new Error('invalid_start_time');
        const day = localDay(started / 1000);
        if (day > today) continue;
        const cron = typeof row.session_key === 'string' && /^agent:[^:]+:cron:[^:]+(?:$|:)/.test(row.session_key);
        const identity = createHash('sha256').update(`${agent}\0${row.session_id}`).digest('hex');
        records.set(identity, { day, cron: Number(cron) });
      }
    } finally { db.close(); }
  }

  mkdirSync(dirname(state), { recursive: true, mode: 0o700 });
  chmodSync(dirname(state), 0o700);
  try { closeSync(openSync(state, 'wx', 0o600)); }
  catch (error) { if (!(error instanceof Error && 'code' in error && error.code === 'EEXIST')) throw error; }
  if (!lstatSync(state).isFile() || lstatSync(state).isSymbolicLink()) throw new Error('invalid_ledger');
  chmodSync(state, 0o600);
  const ledger = new DatabaseSync(state, { timeout: 2000 });
  try {
    ledger.exec('CREATE TABLE IF NOT EXISTS starts (identity TEXT PRIMARY KEY, day TEXT NOT NULL, cron INTEGER NOT NULL);'
      + 'CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value REAL NOT NULL);');
    ledger.exec('BEGIN IMMEDIATE');
    try {
      ledger.prepare("INSERT OR IGNORE INTO metadata VALUES ('observed_since', ?)").run(now);
      const upsert = ledger.prepare('INSERT INTO starts VALUES (?, ?, ?) ON CONFLICT(identity) DO UPDATE SET day=excluded.day, cron=excluded.cron');
      // Correct dates BEFORE pruning: a corrected old observation must leave the window.
      for (const [identity, row] of records) upsert.run(identity, row.day, row.cron);
      ledger.prepare('DELETE FROM starts WHERE day < ?').run(retentionStart(today));
      const size = ledger.prepare('SELECT count(*) AS count FROM starts').get()?.count;
      if (typeof size !== 'number' || size > MAX_ROWS) throw new Error('ledger_unbounded');
      ledger.exec('COMMIT');
    } catch (error) { ledger.exec('ROLLBACK'); throw error; }
    const counts: DailyCounts = new Map();
    for (const row of ledger.prepare('SELECT day, cron, count(*) AS count FROM starts GROUP BY day, cron').all()) {
      if (typeof row.day !== 'string' || typeof row.count !== 'number' || (row.cron !== 0 && row.cron !== 1)) throw new Error('invalid_ledger');
      const count = counts.get(row.day) ?? { all: 0, cron: 0 };
      count.all += row.count;
      count.cron += row.cron ? row.count : 0;
      counts.set(row.day, count);
    }
    const since = ledger.prepare("SELECT value FROM metadata WHERE key='observed_since'").get()?.value;
    if (!finitePositive(since)) throw new Error('invalid_ledger');
    return { counts, undated, since };
  } finally { ledger.close(); }
}

export function renderSessionFailure(node: string, now: number): string {
  const base = { node, node_label: node };
  return metric('openclaw_sessions_collected_at_seconds', 'Latest collection attempt timestamp.', now, base)
    + metric('openclaw_sessions_collector_success', 'Whether all local session metadata sources were read successfully.', 0, base);
}

export function renderSessionSnapshot(node: string, snapshot: Snapshot, now: number): string {
  const base = { node, node_label: node };
  const today = localDay(now);
  const first = retentionStart(today);
  const observedDay = localDay(snapshot.since);
  let output = metric('openclaw_sessions_collected_at_seconds', 'Latest collection attempt timestamp.', now, base)
    + metric('openclaw_sessions_collector_success', 'Whether all local session metadata sources were read successfully.', 1, base)
    + metric('openclaw_sessions_undated', 'Retained session windows excluded because they have no valid start timestamp.', snapshot.undated, base)
    + metric('openclaw_sessions_observed_since_seconds', 'When preservation began; older counts use retained metadata only.', snapshot.since, base)
    + metric('openclaw_sessions_retained_from_seconds', 'Start of retained thirteen-calendar-month window; not proof of complete coverage.', periodStart(first).seconds, base);
  for (const grain of ['daily', 'weekly', 'monthly'] as const) {
    const periods = new Map<string, Counts | undefined>();
    for (let day = first; day <= today; day = shiftDay(day, 1)) {
      const bucket = bucketFor(day, grain);
      if (!periods.has(bucket)) periods.set(bucket, undefined);
      const count = snapshot.counts.get(day) ?? { all: 0, cron: 0 };
      if (day >= observedDay || count.all > 0) {
        const sum = periods.get(bucket) ?? { all: 0, cron: 0 };
        sum.all += count.all;
        sum.cron += count.cron;
        periods.set(bucket, sum);
      }
    }
    const name = `openclaw_sessions_${grain}`;
    output += `# HELP ${name} Unique observed starts per New York calendar period; current and pre-preservation periods are partial.\n# TYPE ${name} gauge\n`;
    for (const [day, count] of periods) {
      for (const kind of ['all', 'cron', 'noncron'] as const) {
        const value = count ? kind === 'noncron' ? count.all - count.cron : count[kind] : 'NaN';
        output += `${name}${labels({ ...base, session_day: periodStart(day).label, session_kind: kind })} ${value}\n`;
      }
    }
  }
  return output;
}

export async function collectSessions(context: RuntimeContext): Promise<number> {
  const now = Date.now() / 1000;
  const state = join(context.root, 'state/sessions/starts.sqlite');
  await ensurePrivateDir(dirname(state));
  let status = 0;
  let output: string;
  try {
    const result = await runCommand(context.settings.nodePath, [fileURLToPath(import.meta.url), '--worker',
      context.config.node_label, context.settings.openclawRoot, state, String(now)], {
      timeoutMs: 30_000, maxBytes: 2 * 1024 * 1024, cwd: context.settings.nodeHome, env: runtimeEnv(context.settings),
    });
    if (result.code !== 0 || !result.stdout.startsWith('# HELP openclaw_sessions_collected_at_seconds ')) throw new Error('session_collection_failed');
    output = result.stdout;
  } catch {
    output = renderSessionFailure(context.config.node_label, now);
    status = 1;
  }
  await writeAtomic(join(context.root, 'textfiles/openclaw_sessions.prom'), output);
  return status;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url) && process.argv[2] === '--worker') {
  process.umask(0o077);
  const [node, root, state, clock] = process.argv.slice(3);
  if (!node || !root || !state || !finitePositive(Number(clock))) process.exitCode = 1;
  else {
    try { process.stdout.write(renderSessionSnapshot(node, collectSessionData(root, state, Number(clock)), Number(clock))); }
    catch { process.exitCode = 1; }
  }
}
