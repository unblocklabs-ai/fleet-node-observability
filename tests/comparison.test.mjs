import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdtemp, writeFile, readFile, mkdir, copyFile, chmod, rm, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import test from 'node:test';
import { readPermissions, readStoredConfiguration, collectComparison } from '../dist/comparison.js';

async function temporary(t) {
  const root = await mkdtemp(join(tmpdir(), 'fleet-comparison-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

test('stored configuration distinguishes absent fields, explicit values, null and unreadable input', async t => {
  const root = await temporary(t);
  const path = join(root, 'openclaw.json');
  assert.deepEqual(await readStoredConfiguration(path), { tools_profile: 'unset', tools_exec_mode: 'unset', status: 'missing' });
  for (const [payload, profile, mode, status] of [
    [{}, 'unset', 'unset', 'ok'],
    [{ tools: { profile: 'coding', exec: {} } }, 'coding', 'unset', 'ok'],
    [{ tools: { profile: 'full', exec: { mode: 'full' } } }, 'full', 'full', 'ok'],
    [{ tools: { profile: null } }, 'Invalid stored value', 'unset', 'invalid'],
    [{ tools: { profile: 'full', exec: null } }, 'full', 'Invalid stored value', 'invalid'],
  ]) {
    await writeFile(path, JSON.stringify(payload));
    assert.deepEqual(await readStoredConfiguration(path), { tools_profile: profile, tools_exec_mode: mode, status });
  }
  await writeFile(path, 'broken JSON');
  assert.equal((await readStoredConfiguration(path)).status, 'unreadable');
});

test('real read-only TCC fixture keeps exact paths, no row, denied, granted and unreadable distinct', async t => {
  const root = await temporary(t);
  const path = join(root, 'TCC.db');
  const db = new DatabaseSync(path);
  db.exec('CREATE TABLE access (service TEXT, client TEXT, client_type INTEGER, auth_value INTEGER)');
  const insert = db.prepare('INSERT INTO access VALUES (?, ?, ?, ?)');
  insert.run('kTCCServiceSystemPolicyAllFiles', '/node/old/node', 1, 2);
  insert.run('kTCCServiceSystemPolicyAllFiles', '/node/new/node', 1, 0);
  insert.run('kTCCServiceAccessibility', '/node/new/node', 1, 2);
  insert.run('kTCCServiceScreenCapture', '/node/new/node', 0, 2); // Bundle IDs never match executable records.
  db.close();
  const before = await readFile(path);
  assert.deepEqual(readPermissions(path, '/node/new/node'), {
    status: 'ok', permissions: { fda: 'Denied', accessibility: 'Granted', screen_recording: 'No matching record' },
  });
  assert.equal(readPermissions(path, '/node/old/node').permissions.fda, 'Granted');
  assert.equal(readPermissions(path, '/node/replaced/node').permissions.fda, 'No matching record');
  for (const unreadable of [join(root, 'missing.db'), root]) {
    assert.deepEqual(readPermissions(unreadable, '/node/new/node'), {
      status: 'unreadable', permissions: { fda: 'Unreadable', accessibility: 'Unreadable', screen_recording: 'Unreadable' },
    });
  }
  assert.deepEqual(await readFile(path), before);
  await assert.rejects(readFile(join(root, 'missing.db')), { code: 'ENOENT' });
});

test('real Gateway executable changes reset history and replace the atomic snapshot', { skip: process.platform !== 'darwin' }, async t => {
  const root = await temporary(t);
  const serving = join(root, 'package with spaces');
  await mkdir(serving);
  await writeFile(join(serving, 'package.json'), JSON.stringify({ name: 'openclaw', version: '2026.10.10' }));
  const script = join(serving, 'index.mjs');
  await writeFile(script, "import net from 'node:net'; net.createServer().listen(18789,'127.0.0.1',()=>console.log('ready'));\n");
  const ctx = { root, config: { node_label: 'fixture' }, settings: { nodePath: process.execPath, nodeHome: root, openclawRoot: root, openclawBin: '/usr/bin/false', codexBin: '/usr/bin/false' } };
  await writeFile(join(root, 'openclaw.json'), '{}');
  let gateway;
  const start = async executable => {
    gateway = spawn(executable, [script, 'gateway']);
    const [data] = await once(gateway.stdout, 'data');
    assert.match(String(data), /ready/);
  };
  const stop = async () => { const done = once(gateway, 'exit'); gateway.kill(); await done; };
  t.after(async () => { if (gateway && gateway.exitCode === null && gateway.signalCode === null) await stop(); });
  await start(process.execPath);
  await collectComparison(ctx);
  const first = JSON.parse(await readFile(join(root, 'state/comparison.json'), 'utf8'));
  assert.equal(first.statuses.gateway, 'ok');
  await writeFile(join(root, 'state/comparison.json'), JSON.stringify({ ...first, lastSuccess: 1000 }));
  await stop();
  const alternate = join(root, 'node');
  await copyFile(process.execPath, alternate); await chmod(alternate, 0o700);
  await start(alternate);
  await collectComparison(ctx);
  const second = JSON.parse(await readFile(join(root, 'state/comparison.json'), 'utf8'));
  assert.equal(second.statuses.gateway, 'ok');
  assert.equal(second.executable, await realpath(alternate));
  assert.notEqual(second.identity, first.identity);
  assert.notEqual(second.lastSuccess, 1000);
  const output = await readFile(join(root, 'textfiles/node_comparison.prom'), 'utf8');
  assert.match(output, /openclaw_version="2026.10.10"/);
  assert.match(output, /tools_profile="unset"/);
  assert.doesNotMatch(output, /node\/old|package with spaces|pid=/);
  await stop();
  await writeFile(script, "import net from 'node:net'; process.title='openclaw-gateway'; net.createServer().listen(18789,'127.0.0.1',()=>console.log('ready'));\n");
  await start(alternate);
  await collectComparison(ctx);
  const titled = JSON.parse(await readFile(join(root, 'state/comparison.json'), 'utf8'));
  assert.equal(titled.statuses.gateway, 'ok');
  assert.equal(titled.executable, await realpath(alternate));
  assert.equal(titled.statuses.package, 'unreadable'); // No mapped OpenClaw module in this fixture.
  assert.match(await readFile(join(root, 'textfiles/node_comparison.prom'), 'utf8'), /gateway_node_version="v24\./);
});
