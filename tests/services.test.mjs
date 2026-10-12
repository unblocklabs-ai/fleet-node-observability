import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';
import test from 'node:test';
import { parseLaunchdStatus, prepareServices, startServices, stopServices } from '../dist/services.js';

const execute = promisify(execFile);
const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";

async function fixture(t, script = '') {
  const base = await mkdtemp(join(tmpdir(), 'fleet-services-'));
  const root = join(base, "private space's agent");
  await mkdir(root, { mode: 0o700 });
  t.after(async () => { await rm(base, { recursive: true, force: true }); });
  const runtime = join(root, 'fake-node');
  const marker = join(root, 'entered');
  await writeFile(runtime, `#!/bin/sh\nprintf 'entered\\n' >> ${quote(marker)}\n${script}\n`, { mode: 0o700 });
  const ctx = {
    root,
    config: { config_schema_version: 3, node_label: 'bill', telemetry_endpoint: 'https://unused.invalid', codex_usage_enabled: false },
    settings: { nodePath: runtime, nodeHome: root, openclawRoot: join(root, 'openclaw'), openclawBin: join(root, 'missing-openclaw'), codexBin: join(root, 'missing-codex'), npmCliPath: join(root, 'npm-cli.js'), npmPrefix: join(root, 'npm') },
  };
  const result = await prepareServices(ctx);
  return { ctx, marker, runtime, launcher: result.launcher };
}

function launch(path, job = 'heartbeat') {
  const child = spawn('/bin/sh', [path, job], { stdio: ['ignore', 'pipe', 'pipe'] });
  child.done = once(child, 'exit');
  return child;
}

async function until(check) {
  for (let i = 0; i < 100; i++) { if (await check()) return; await delay(20); }
  assert.fail('condition did not become true');
}

async function exists(path) {
  try { await stat(path); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}

test('real external launcher survives unavailable package/runtime during maintenance, then uses the new version', async t => {
  const { ctx, marker, runtime, launcher } = await fixture(t);
  await writeFile(join(ctx.root, 'history'), 'durable-history');
  // Model launchd KeepAlive retries during a partially removed npm installation.
  await rm(runtime);
  for (let i = 0; i < 4; i++) assert.equal((await launch(launcher).done)[0], 0);
  assert.equal(await exists(marker), false);
  await writeFile(runtime, `#!/bin/sh\nprintf 'version-two\\n' >> ${quote(marker)}\n`, { mode: 0o700 });
  await startServices(ctx);
  assert.equal((await launch(launcher).done)[0], 0);
  assert.equal(await readFile(marker, 'utf8'), 'version-two\n');
  await stopServices(ctx);
  assert.equal(await readFile(join(ctx.root, 'history'), 'utf8'), 'durable-history');
  assert.equal(await exists(join(ctx.root, 'services/enabled')), false);
});

test('per-job lock prevents overlap; stop quiesces a hung check and its child before npm mutation', async t => {
  const { ctx, marker, runtime, launcher } = await fixture(t);
  const childFile = join(ctx.root, 'child.pid');
  await writeFile(runtime, `#!/bin/sh\nprintf 'entered\\n' >> ${quote(marker)}\n/bin/sleep 60 &\nprintf '%s' "$!" > ${quote(childFile)}\nwait\n`, { mode: 0o700 });
  await startServices(ctx);
  const first = launch(launcher);
  t.after(() => { if (first.exitCode === null) first.kill('SIGKILL'); });
  await until(() => exists(childFile));
  const childPid = Number(await readFile(childFile, 'utf8'));
  const duplicate = launch(launcher);
  assert.equal((await duplicate.done)[0], 0);
  assert.equal(await readFile(marker, 'utf8'), 'entered\n');
  await stopServices(ctx);
  await first.done;
  assert.throws(() => process.kill(childPid, 0), { code: 'ESRCH' });
  assert.equal(await exists(join(ctx.root, 'state/jobs/heartbeat')), false);
  assert.equal((await launch(launcher).done)[0], 0);
  assert.equal(await readFile(marker, 'utf8'), 'entered\n');
});

test('stale owner PID reuse is not mistaken for an active service', async t => {
  const { ctx } = await fixture(t);
  const unrelated = spawn('/bin/sleep', ['60']);
  const done = once(unrelated, 'exit');
  t.after(async () => { unrelated.kill(); await done; });
  const lock = join(ctx.root, 'state/jobs/heartbeat');
  await mkdir(lock);
  await writeFile(join(lock, 'owner'), `${unrelated.pid}\nThu Jan 1 00:00:00 1970\n`);
  await stopServices(ctx);
  assert.equal(process.kill(unrelated.pid, 0), true);
  assert.equal(await exists(lock), false);
});

test('a live unrelated process in a lock fails closed instead of being killed', async t => {
  const { ctx } = await fixture(t);
  const unrelated = spawn('/bin/sleep', ['60']);
  const done = once(unrelated, 'exit');
  t.after(async () => { unrelated.kill(); await done; });
  const { stdout } = await execute('/bin/ps', ['-p', String(unrelated.pid), '-o', 'lstart=']);
  const lock = join(ctx.root, 'state/jobs/heartbeat');
  await mkdir(lock);
  await writeFile(join(lock, 'owner'), `${unrelated.pid}\n${stdout.trim()}\n`);
  await startServices(ctx);
  await assert.rejects(stopServices(ctx), /unrecognized process/);
  assert.equal(await exists(join(ctx.root, 'services/enabled')), false);
  assert.equal(process.kill(unrelated.pid, 0), true);
});

test('disabled Codex never enters package code and running launch configuration cannot be overwritten', async t => {
  const { ctx, marker, launcher } = await fixture(t);
  await mkdir(join(ctx.root, 'textfiles'));
  await writeFile(join(ctx.root, 'textfiles/codex_usage.prom'), 'previous-codex-success');
  await prepareServices(ctx);
  assert.equal(await exists(join(ctx.root, 'textfiles/codex_usage.prom')), false);
  await startServices(ctx);
  assert.equal((await launch(launcher, 'codex').done)[0], 0);
  assert.equal(await exists(marker), false);
  await assert.rejects(prepareServices(ctx), /Stop services/);
  const gate = await stat(join(ctx.root, 'services/enabled'));
  assert.equal(gate.mode & 0o777, 0o600);
  await stopServices(ctx);
});

test('gate closure racing actual launches leaves no late package execution', async t => {
  const { ctx, marker, launcher } = await fixture(t);
  await startServices(ctx);
  const attempts = Array.from({ length: 12 }, () => launch(launcher));
  await stopServices(ctx);
  await Promise.all(attempts.map(child => child.done));
  const before = await exists(marker) ? await readFile(marker, 'utf8') : '';
  for (let i = 0; i < 4; i++) await launch(launcher).done;
  await delay(50);
  assert.equal(await exists(marker) ? await readFile(marker, 'utf8') : '', before);
});

test('a disposable root cannot adopt the fixed-label production PID', () => {
  const ctx = { root: '/private/disposable' };
  const output = path => `system/com.unblocklabs.fleet-node-ts.heartbeat = {\n\targuments = {\n\t\t/bin/sh\n\t\t${path}\n\t\theartbeat\n\t}\n\tpid = 9876\n}`;
  assert.throws(() => parseLaunchdStatus(ctx, 'heartbeat', output('/Users/bill/.local/share/fleet-node/services/launch.sh')), /another runtime root/);
  assert.throws(() => parseLaunchdStatus(ctx, 'heartbeat', output('/private/disposable/services/launch.sh-other')), /another runtime root/);
  assert.deepEqual(parseLaunchdStatus(ctx, 'heartbeat', output('/private/disposable/services/launch.sh')), { loaded: true, pid: 9876 });
});
