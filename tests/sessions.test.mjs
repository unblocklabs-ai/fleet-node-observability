import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';
import { collectSessionData, collectSessions, localDay, periodStart, renderSessionSnapshot, retentionStart } from '../dist/sessions.js';

const now = Date.parse('2026-03-09T16:00:00Z') / 1000;
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'fleet-sessions-test-'));
  const openclaw = join(root, 'openclaw');
  const path = join(openclaw, 'agents/main/agent/openclaw-agent.sqlite');
  mkdirSync(join(openclaw, 'agents/main/agent'), { recursive: true });
  const db = new DatabaseSync(path);
  db.exec('CREATE TABLE session_windows (session_id TEXT PRIMARY KEY, session_key TEXT, started_at INTEGER);'
    + 'CREATE TABLE session_nodes (session_key TEXT PRIMARY KEY, current_session_id TEXT, entry_json TEXT);');
  const state = join(root, 'runtime/state/sessions/starts.sqlite');
  t.after(() => { db.close(); rmSync(root, { recursive: true, force: true }); });
  return { root, openclaw, path, state, db,
    insert: (id, key, started) => db.prepare('INSERT INTO session_windows VALUES (?, ?, ?)').run(id, key, started),
    metadata: (id, key, fields) => db.prepare('INSERT OR REPLACE INTO session_nodes VALUES (?, ?, ?)').run(key, id, JSON.stringify({ sessionId: id, ...fields })),
    context: { root: join(root, 'runtime'), config: { node_label: 'bill', config_schema_version: 3, telemetry_endpoint: 'https://example.test', codex_usage_enabled: false },
      settings: { nodePath: process.execPath, nodeHome: root, openclawRoot: openclaw, openclawBin: '/missing/openclaw', codexBin: '/missing/codex', npmCliPath: '/missing/npm', npmPrefix: join(root, 'npm') } },
  };
}

test('WAL-only metadata is visible without changing source data and repeated polls deduplicate', t => {
  const f = fixture(t);
  f.db.exec('PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;');
  f.insert('one', 'agent:main:cron:job:run:one', now * 1000);
  f.insert('two', 'agent:main:slack:cron:word', now * 1000);
  const sourceBefore = readFileSync(f.path);
  const walBefore = readFileSync(`${f.path}-wal`);
  const first = collectSessionData(f.openclaw, f.state, now);
  assert.deepEqual(first.counts.get('2026-03-09'), { all: 2, cron: 1 });
  assert.deepEqual(collectSessionData(f.openclaw, f.state, now).counts, first.counts);
  assert.deepEqual(readFileSync(f.path), sourceBefore);
  assert.deepEqual(readFileSync(`${f.path}-wal`), walBefore);
  assert.equal(statSync(f.state).mode & 0o777, 0o600);
  assert.equal(statSync(join(f.root, 'runtime/state/sessions')).mode & 0o777, 0o700);
  assert.ok(!readFileSync(f.state).includes(Buffer.from('agent:main')));
  f.db.exec('DELETE FROM session_windows');
  const afterCleanup = collectSessionData(f.openclaw, f.state, now + 10);
  assert.deepEqual(afterCleanup.counts, first.counts);
  assert.equal(afterCleanup.since, now);
});

test('lifecycle priority corrects dates, reset identity cannot redate old windows, and activity dates are ignored', t => {
  const f = fixture(t);
  f.insert('old', 'agent:main:slack:a', now * 1000);
  collectSessionData(f.openclaw, f.state, now);
  f.metadata('old', 'agent:main:slack:a', { sessionStartedAt: Date.parse('2026-03-08T16:00:00Z') });
  f.insert('reset', 'agent:main:slack:b', null);
  f.metadata('replacement', 'agent:main:slack:b', { sessionStartedAt: now * 1000 });
  f.insert('no-date', 'agent:main:slack:c', null);
  f.metadata('no-date', 'agent:main:slack:c', { sessionStartedAt: true, updatedAt: now * 1000, createdAt: now * 1000 });
  f.insert('wrong-json-id', 'agent:main:slack:d', null);
  f.metadata('wrong-json-id', 'agent:main:slack:d', { sessionId: 'different', sessionStartedAt: now * 1000 });
  const result = collectSessionData(f.openclaw, f.state, now);
  assert.deepEqual([...result.counts], [['2026-03-08', { all: 1, cron: 0 }]]);
  assert.equal(result.undated, 3);
  f.metadata('old', 'agent:main:slack:a', { sessionStartedAt: Date.parse('2025-02-28T16:00:00Z') });
  assert.equal(collectSessionData(f.openclaw, f.state, now).counts.size, 0);
});

test('same session ID in different agent namespaces is distinct', t => {
  const f = fixture(t);
  f.insert('same', 'agent:main:slack:a', now * 1000);
  const otherPath = join(f.openclaw, 'agents/other/agent');
  mkdirSync(otherPath, { recursive: true });
  const other = new DatabaseSync(join(otherPath, 'openclaw-agent.sqlite'));
  try {
    other.exec('CREATE TABLE session_windows (session_id TEXT, session_key TEXT, started_at INTEGER); CREATE TABLE session_nodes (session_key TEXT, current_session_id TEXT, entry_json TEXT);');
    other.prepare('INSERT INTO session_windows VALUES (?, ?, ?)').run('same', 'agent:other:cron:job', now * 1000);
  } finally { other.close(); }
  assert.deepEqual(collectSessionData(f.openclaw, f.state, now).counts.get('2026-03-09'), { all: 2, cron: 1 });
});

test('symlinked agent namespaces are collected rather than silently omitted', t => {
  const f = fixture(t);
  f.insert('main', 'agent:main:slack:a', now * 1000);
  const linked = join(f.root, 'external-agent');
  mkdirSync(join(linked, 'agent'), { recursive: true });
  const other = new DatabaseSync(join(linked, 'agent/openclaw-agent.sqlite'));
  try {
    other.exec('CREATE TABLE session_windows (session_id TEXT, session_key TEXT, started_at INTEGER); CREATE TABLE session_nodes (session_key TEXT, current_session_id TEXT, entry_json TEXT);');
    other.prepare('INSERT INTO session_windows VALUES (?, ?, ?)').run('linked', 'agent:linked:cron:job', now * 1000);
  } finally { other.close(); }
  symlinkSync(linked, join(f.openclaw, 'agents/linked'));
  const snapshot = collectSessionData(f.openclaw, f.state, now);
  assert.deepEqual(snapshot.counts.get('2026-03-09'), { all: 2, cron: 1 });
  assert.equal(snapshot.undated, 0);
});

test('New York midnight handles spring and fall DST transitions, and real calendar retention', () => {
  assert.equal(periodStart('2026-03-08').label, '2026-03-08T00:00:00-05:00');
  assert.equal(periodStart('2026-03-09').label, '2026-03-09T00:00:00-04:00');
  assert.equal(periodStart('2026-03-09').seconds - periodStart('2026-03-08').seconds, 23 * 3600);
  assert.equal(periodStart('2026-11-01').label, '2026-11-01T00:00:00-04:00');
  assert.equal(periodStart('2026-11-02').label, '2026-11-02T00:00:00-05:00');
  assert.equal(periodStart('2026-11-02').seconds - periodStart('2026-11-01').seconds, 25 * 3600);
  assert.equal(localDay(Date.parse('2026-03-08T04:59:00Z') / 1000), '2026-03-07');
  assert.equal(retentionStart('2025-02-28'), '2024-02-01');
  assert.equal(retentionStart('2024-02-29'), '2023-02-01');
});

test('calendar gauges aggregate Monday weeks and months, preserve unknown NaN gaps, and prune on month boundary', t => {
  const f = fixture(t);
  for (const [id, day, kind] of [['first', '2025-03-01', 'slack'], ['expired', '2025-02-28', 'slack'], ['feb', '2026-02-28', 'cron'], ['sun', '2026-03-01', 'slack'], ['mon', '2026-03-02', 'slack']]) {
    f.insert(id, `agent:main:${kind}:${id}`, Date.parse(`${day}T16:00:00Z`));
  }
  const snapshot = collectSessionData(f.openclaw, f.state, now);
  assert.ok(!snapshot.counts.has('2025-02-28'));
  const output = renderSessionSnapshot('bill', snapshot, now);
  assert.match(output, /openclaw_sessions_weekly\{[^\n]*session_day="2026-02-23T00:00:00-05:00",session_kind="all"\} 2\n/);
  assert.match(output, /openclaw_sessions_monthly\{[^\n]*session_day="2026-03-01T00:00:00-05:00",session_kind="noncron"\} 2\n/);
  assert.match(output, /openclaw_sessions_daily\{[^\n]*session_day="2026-03-07T00:00:00-05:00",session_kind="all"\} NaN\n/);
  assert.match(output, /openclaw_sessions_daily\{[^\n]*session_day="2026-03-09T00:00:00-04:00",session_kind="all"\} 0\n/);
  f.db.exec('DELETE FROM session_windows');
  assert.equal(collectSessionData(f.openclaw, f.state, Date.parse('2026-03-31T16:00:00Z') / 1000).counts.get('2025-03-01').all, 1);
  assert.ok(!collectSessionData(f.openclaw, f.state, Date.parse('2026-04-01T16:00:00Z') / 1000).counts.has('2025-03-01'));
});

test('existing Python ledger identity and observed_since survive source cleanup and new observations', t => {
  const f = fixture(t);
  mkdirSync(join(f.root, 'runtime/state/sessions'), { recursive: true, mode: 0o700 });
  const ledger = new DatabaseSync(f.state);
  try {
    ledger.exec('CREATE TABLE starts (identity TEXT PRIMARY KEY, day TEXT NOT NULL, cron INTEGER NOT NULL); CREATE TABLE metadata (key TEXT PRIMARY KEY, value REAL NOT NULL);');
    ledger.prepare('INSERT INTO starts VALUES (?, ?, ?)').run(createHash('sha256').update('main\0vanished').digest('hex'), '2026-03-07', 1);
    ledger.prepare('INSERT INTO metadata VALUES (?, ?)').run('observed_since', now - 86400);
  } finally { ledger.close(); }
  f.insert('new', 'agent:main:slack:new', now * 1000);
  const snapshot = collectSessionData(f.openclaw, f.state, now);
  assert.equal(snapshot.since, now - 86400);
  assert.deepEqual(snapshot.counts.get('2026-03-07'), { all: 1, cron: 1 });
  assert.deepEqual(snapshot.counts.get('2026-03-09'), { all: 1, cron: 0 });
});

test('real subprocess collection publishes success, then failures replace counts and preserve ledger', async t => {
  const f = fixture(t);
  f.insert('valid', 'agent:main:cron:job', Date.now());
  assert.equal(await collectSessions(f.context), 0);
  const output = join(f.context.root, 'textfiles/openclaw_sessions.prom');
  assert.match(readFileSync(output, 'utf8'), /openclaw_sessions_collector_success\{[^\n]*\} 1/);
  const before = readFileSync(f.state);
  f.insert('future', 'agent:main:cron:future', Date.now() + 3600_000);
  assert.equal(await collectSessions(f.context), 1);
  const failure = readFileSync(output, 'utf8');
  assert.match(failure, /openclaw_sessions_collector_success\{[^\n]*\} 0/);
  assert.ok(!failure.includes('openclaw_sessions_daily'));
  assert.deepEqual(readFileSync(f.state), before);
  const missing = { ...f.context, settings: { ...f.context.settings, openclawRoot: join(f.root, 'missing') } };
  assert.equal(await collectSessions(missing), 1);
  assert.deepEqual(readFileSync(f.state), before);
});

test('locked SQLite source fails within its busy bound without blocking parent event loop', async t => {
  const f = fixture(t);
  f.db.exec('BEGIN EXCLUSIVE');
  let ticked = false;
  const timer = setTimeout(() => { ticked = true; }, 30);
  const start = performance.now();
  try { assert.equal(await collectSessions(f.context), 1); }
  finally { clearTimeout(timer); f.db.exec('ROLLBACK'); }
  assert.ok(ticked);
  assert.ok(performance.now() - start < 6000);
  assert.match(readFileSync(join(f.context.root, 'textfiles/openclaw_sessions.prom'), 'utf8'), /collector_success\{[^\n]*\} 0/);
});
