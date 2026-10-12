// One-shot Bill canary operation, not installed/published as part of the npm runtime.
import { backup, DatabaseSync } from 'node:sqlite';
import { cp, chmod, lstat, mkdir, readdir, readFile, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import assert from 'node:assert/strict';

// Export the same operation for real filesystem/SQLite rehearsals in private scratch
// directories. The executable entrypoint below remains pinned to Bill's exact layout.
export async function transferState({ legacy, current, rollout }, reverse = false) {
  process.umask(0o077);
  const source = reverse ? current : legacy;
  const destination = reverse ? legacy : current;
  const record = join(rollout, reverse ? 'rollback-state-preserved.json' : 'cutover-state-preserved.json');
  try { await lstat(record); throw new Error('State copy already completed; inspect before repeating'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  for (const path of [source, destination, rollout, join(rollout, 'rollback'), join(source, 'queue'), join(source, 'state'), join(source, 'state/sessions'), join(destination, 'queue'), join(destination, 'state'), join(destination, 'state/sessions')]) {
    const st = await lstat(path);
    assert.ok(st.isDirectory() && !st.isSymbolicLink());
    assert.equal(st.uid, process.getuid());
  }
  try { await lstat(join(current, 'services/enabled')); throw new Error('New services must be stopped'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (!reverse) {
    assert.deepEqual(await readdir(join(destination, 'queue')), [], 'New queue must be empty');
    assert.deepEqual(await readdir(join(destination, 'state/sessions')), [], 'New session state must be empty');
  }
  const timestamps = new Map();
  for (const signal of ['logs', 'traces', 'openclaw_metrics', 'agent_metrics', 'host_metrics', 'heartbeat']) {
    const name = `queue-oldest-${signal}.timestamp`;
    if (!reverse) {
      try { await lstat(join(destination, 'state', name)); throw new Error(`Destination already contains ${name}`); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    let st;
    try { st = await lstat(join(source, 'state', name)); }
    catch (error) { if (error.code === 'ENOENT') continue; throw error; }
    assert.ok(st.isFile() && !st.isSymbolicLink());
    timestamps.set(name, await readFile(join(source, 'state', name)));
  }
  const sourceLedger = join(source, 'state/sessions/starts.sqlite');
  const st = await lstat(sourceLedger);
  assert.ok(st.isFile() && !st.isSymbolicLink());
  const from = new DatabaseSync(sourceLedger, {readOnly:true,timeout:2000});
  try {
    // Validate all source state before moving or copying any destination data.
    const counts = from.prepare('SELECT count(*) AS count,min(day) AS first,max(day) AS last FROM starts').get();
    counts.observed_since = from.prepare("SELECT value FROM metadata WHERE key='observed_since'").get()?.value;
    assert.ok(Number.isFinite(counts.observed_since), 'Missing or invalid observed_since');
    assert.equal(from.prepare('PRAGMA integrity_check').get().integrity_check, 'ok');
    if (reverse) {
      const saved = join(rollout, 'rollback/legacy-state-before-return');
      await mkdir(saved, { mode: 0o700 });
      for (const directory of ['queue', 'state']) {
        await rename(join(destination, directory), join(saved, directory));
        await mkdir(join(destination, directory), {mode: 0o700});
      }
    }
    // The prepared destination already exists. Copy its children exclusively
    // rather than asking fs.cp to recreate that directory (which fails EEXIST).
    for (const entry of await readdir(join(source, 'queue'))) {
      await cp(join(source, 'queue', entry), join(destination, 'queue', entry), {recursive:true,force:false,errorOnExist:true,preserveTimestamps:true});
    }
    for (const [name, value] of timestamps) {
      await writeFile(join(destination, 'state', name), value, {flag:'wx',mode:0o600});
    }
    await mkdir(join(destination, 'state/sessions'), {recursive:true,mode:0o700});
    const target = join(destination, 'state/sessions/starts.sqlite');
    await backup(from, target);
    await chmod(target,0o600);
    const to = new DatabaseSync(target,{readOnly:true});
    try {
      assert.equal(to.prepare('SELECT count(*) AS count FROM starts').get().count,counts.count);
      assert.equal(to.prepare("SELECT value FROM metadata WHERE key='observed_since'").get().value,counts.observed_since);
      assert.equal(to.prepare('PRAGMA integrity_check').get().integrity_check,'ok');
    } finally { to.close(); }
    await writeFile(record,JSON.stringify({at:new Date().toISOString(),source,destination,session:counts},null,2)+'\n',{flag:'wx',mode:0o600});
    return {preserved:true,session:counts};
  } finally { from.close(); }
}

if (import.meta.main) {
  assert.equal(process.getuid(), 501);
  assert.equal(process.env.HOME, '/Users/billjohansson');
  const reverse = process.argv[2] === '--reverse';
  assert.ok(process.argv.length === 2 || (reverse && process.argv.length === 3));
  const result = await transferState({
    legacy: '/Users/billjohansson/.openclaw/fleet-node-observability',
    current: '/Users/billjohansson/.local/share/fleet-node',
    rollout: '/Users/billjohansson/fleet-node-ts-canary-20260927',
  }, reverse);
  console.log(JSON.stringify(result));
}
