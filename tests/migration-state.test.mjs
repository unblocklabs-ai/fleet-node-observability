import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, readFile, readdir, lstat, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';
import { transferState } from '../ops/import-bill-state.mjs';

async function files(root) {
  const result = {};
  for (const name of (await readdir(root, { recursive: true })).sort()) {
    const path = join(root, name);
    const st = await lstat(path);
    result[name] = st.isDirectory() ? 'directory' : createHash('sha256').update(await readFile(path)).digest('hex');
  }
  return result;
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'fleet-migration-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const layout = { legacy: join(root, 'legacy'), current: join(root, 'current'), rollout: join(root, 'rollout') };
  for (const runtime of [layout.legacy, layout.current]) {
    for (const dir of ['queue', 'state/sessions', 'services']) await mkdir(join(runtime, dir), { recursive: true, mode: 0o700 });
  }
  await mkdir(join(layout.rollout, 'rollback'), { recursive: true, mode: 0o700 });
  await mkdir(join(layout.legacy, 'queue/compaction'), { mode: 0o700 });
  for (const name of ['exporter_otlp_http_logs_logs', 'compaction/storage']) {
    await writeFile(join(layout.legacy, 'queue', name), Buffer.from([0, 255, 10, 0, 45]), { mode: 0o600 });
  }
  await writeFile(join(layout.legacy, 'state/queue-oldest-logs.timestamp'), '1789873055.218782\n', { mode: 0o600 });
  const db = new DatabaseSync(join(layout.legacy, 'state/sessions/starts.sqlite'));
  db.exec('PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0; CREATE TABLE starts(identity TEXT PRIMARY KEY,day TEXT NOT NULL,cron INTEGER NOT NULL); CREATE TABLE metadata(key TEXT PRIMARY KEY,value REAL NOT NULL);');
  db.prepare('INSERT INTO starts VALUES(?,?,?)').run('hashed-one', '2026-09-27', 1);
  db.prepare('INSERT INTO metadata VALUES(?,?)').run('observed_since', 1789873055.218782);
  t.after(() => { if (db.isOpen) db.close(); });
  return { layout, db };
}

test('real migration preserves existing-empty queue layout, WAL-only ledger, queue bytes and private receipt', async t => {
  const { layout } = await fixture(t);
  const queueBefore = await files(join(layout.legacy, 'queue'));
  const ledgerBefore = await files(join(layout.legacy, 'state/sessions'));
  const result = await transferState(layout);
  assert.equal(result.session.count, 1);
  assert.equal(result.session.observed_since, 1789873055.218782);
  assert.deepEqual(await files(join(layout.current, 'queue')), queueBefore);
  assert.deepEqual(await files(join(layout.legacy, 'queue')), queueBefore);
  const ledgerAfter = await files(join(layout.legacy, 'state/sessions'));
  // SQLite read locks may update the shared-memory index, not database/WAL data.
  assert.equal(ledgerAfter['starts.sqlite'], ledgerBefore['starts.sqlite']);
  assert.equal(ledgerAfter['starts.sqlite-wal'], ledgerBefore['starts.sqlite-wal']);
  assert.equal(await readFile(join(layout.current, 'state/queue-oldest-logs.timestamp'), 'utf8'), '1789873055.218782\n');
  assert.equal((await lstat(join(layout.current, 'state/sessions/starts.sqlite'))).mode & 0o777, 0o600);
  assert.equal((await lstat(join(layout.rollout, 'cutover-state-preserved.json'))).mode & 0o777, 0o600);
  await assert.rejects(transferState(layout), /already completed/);
});

test('real reverse migration preserves current TS observations and retains pre-return legacy state', async t => {
  const { layout, db } = await fixture(t);
  // Production rollback happens only after all writers are stopped.
  db.close();
  await transferState(layout);
  const oldQueue = await files(join(layout.legacy, 'queue'));
  const oldState = await files(join(layout.legacy, 'state'));
  await writeFile(join(layout.current, 'queue/exporter_otlp_http_logs_logs'), 'new queued data', { mode: 0o600 });
  const currentDb = new DatabaseSync(join(layout.current, 'state/sessions/starts.sqlite'));
  currentDb.prepare('INSERT INTO starts VALUES(?,?,?)').run('hashed-two', '2026-09-28', 0);
  currentDb.close();
  const currentQueue = await files(join(layout.current, 'queue'));
  const result = await transferState(layout, true);
  assert.equal(result.session.count, 2);
  assert.equal(result.session.observed_since, 1789873055.218782);
  assert.deepEqual(await files(join(layout.legacy, 'queue')), currentQueue);
  assert.deepEqual(await files(join(layout.current, 'queue')), currentQueue);
  assert.deepEqual(await files(join(layout.rollout, 'rollback/legacy-state-before-return/queue')), oldQueue);
  assert.deepEqual(await files(join(layout.rollout, 'rollback/legacy-state-before-return/state')), oldState);
  await assert.rejects(transferState(layout, true), /already completed/);
});

test('migration refuses dirty destinations or an open gate before copying queues', async t => {
  for (const condition of ['queue', 'history', 'gate', 'queue-symlink', 'queue-age', 'missing-metadata']) {
    const { layout, db } = await fixture(t);
    if (condition === 'queue') await writeFile(join(layout.current, 'queue/existing'), 'untouched');
    if (condition === 'history') await writeFile(join(layout.current, 'state/sessions/existing'), 'untouched');
    if (condition === 'gate') await writeFile(join(layout.current, 'services/enabled'), 'enabled');
    if (condition === 'queue-age') await writeFile(join(layout.current, 'state/queue-oldest-logs.timestamp'), 'untouched');
    if (condition === 'missing-metadata') db.exec('DELETE FROM metadata');
    if (condition === 'queue-symlink') {
      await rm(join(layout.current, 'queue'), { recursive: true });
      await symlink(join(layout.legacy, 'queue'), join(layout.current, 'queue'));
    }
    const before = await files(join(layout.legacy, 'queue'));
    await assert.rejects(transferState(layout));
    assert.deepEqual(await files(join(layout.legacy, 'queue')), before);
    await assert.rejects(lstat(join(layout.rollout, 'cutover-state-preserved.json')), { code: 'ENOENT' });
  }
});
