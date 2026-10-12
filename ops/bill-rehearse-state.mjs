// Read real stopped legacy data; write only private scratch state, never production.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { lstat, mkdir, mkdtemp, readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { transferState } from './import-bill-state.mjs';

process.umask(0o077);
assert.equal(process.getuid(), 501);
const rollout = '/Users/billjohansson/fleet-node-ts-canary-20260927';
const legacy = '/Users/billjohansson/.openclaw/fleet-node-observability';
const production = '/Users/billjohansson/.local/share/fleet-node';
const scratch = await mkdtemp(join(rollout, 'state-rehearsal-'));
const current = join(scratch, 'current');
const restored = join(scratch, 'restored-legacy');
for (const runtime of [current, restored]) {
  for (const dir of ['queue', 'state/sessions', 'services']) await mkdir(join(runtime, dir), {recursive:true,mode:0o700});
}
await mkdir(join(scratch, 'rollback'), {mode:0o700});
await writeFile(join(restored, 'queue/pre-return-sentinel'), 'retain this old state', {mode:0o600});

async function queueFiles(root) {
  const entries = {};
  for (const name of (await readdir(root, {recursive:true})).sort()) {
    const path = join(root, name);
    const st = await lstat(path);
    assert.equal(st.isSymbolicLink(), false);
    entries[name] = st.isDirectory() ? 'directory' : createHash('sha256').update(await readFile(path)).digest('hex');
  }
  return entries;
}

const before = await queueFiles(join(legacy, 'queue'));
const forward = await transferState({legacy,current,rollout:scratch});
assert.deepEqual(await queueFiles(join(current, 'queue')), before);
const reverse = await transferState({legacy:restored,current,rollout:scratch}, true);
assert.deepEqual(await queueFiles(join(restored, 'queue')), before);
assert.deepEqual(await queueFiles(join(legacy, 'queue')), before);
assert.equal(await readFile(join(scratch, 'rollback/legacy-state-before-return/queue/pre-return-sentinel'), 'utf8'), 'retain this old state');
assert.deepEqual(reverse.session, forward.session);
assert.deepEqual(await readdir(join(production, 'queue')), []);
assert.deepEqual(await readdir(join(production, 'state/sessions')), []);
await assert.rejects(lstat(join(production, 'services/enabled')), {code:'ENOENT'});
const report = {at:new Date().toISOString(),scratch,node:process.version,forward,reverse,queueEntries:Object.keys(before).length,queueBytesPreserved:true,productionDestinationStillEmpty:true,newGateClosed:true};
await writeFile(join(scratch, 'report.json'), JSON.stringify(report,null,2)+'\n', {flag:'wx',mode:0o600});
console.log(JSON.stringify(report,null,2));
