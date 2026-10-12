import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, writeFile, chmod, stat, rm } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { DatabaseSync } from 'node:sqlite';

const repository = dirname(dirname(fileURLToPath(import.meta.url)));
const npmCli = resolve(dirname(process.execPath), '../lib/node_modules/npm/bin/npm-cli.js');

test('packed CLI isolates checks and preserves external state through real npm upgrade and rollback', { timeout: 120_000 }, async t => {
  assert.equal(process.versions.node.split('.')[0], '24', 'Run this integration test with the configured supported Node 24 runtime');
  const temporary = await mkdtemp(join(tmpdir(), 'fleet-packed-test-'));
  const live = new Set();
  const pidsPath = join(temporary, 'openclaw-pids');
  let source;
  let fixtureProcessesMayExist = false;
  t.after(async () => {
    for (const child of live) { try { process.kill(-child.pid, 'SIGKILL'); } catch {} }
    try { if (fixtureProcessesMayExist) {
      for (const pid of JSON.parse(await readFile(pidsPath, 'utf8'))) { try { process.kill(pid, 'SIGKILL'); } catch {} }
    } } catch {}
    source?.close();
    await rm(temporary, { recursive: true, force: true });
  });
  const env = { ...process.env, PATH: `${dirname(process.execPath)}:/usr/bin:/bin:/usr/sbin:/sbin`,
    npm_config_cache: join(temporary, 'npm-cache'), npm_config_offline: 'true', npm_config_update_notifier: 'false',
    npm_config_audit: 'false', npm_config_fund: 'false', NO_UPDATE_NOTIFIER: '1' };
  function command(executable, args, { cwd = temporary, timeout = 40_000 } = {}) {
    return new Promise((resolveResult, reject) => {
      const child = spawn(executable, args, { cwd, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
      live.add(child);
      let stdout = '', stderr = '', size = 0;
      const timer = setTimeout(() => {
        try { process.kill(-child.pid, 'SIGKILL'); } catch {}
        reject(new Error(`Timed out: ${executable} ${args[0] ?? ''}`));
      }, timeout);
      for (const [stream, name] of [[child.stdout, 'stdout'], [child.stderr, 'stderr']]) stream.on('data', chunk => {
        size += chunk.length;
        if (size > 2 * 1024 * 1024) { try { process.kill(-child.pid, 'SIGKILL'); } catch {} reject(new Error('Test subprocess output limit')); }
        if (name === 'stdout') stdout += chunk.toString(); else stderr += chunk.toString();
      });
      child.once('error', error => { clearTimeout(timer); live.delete(child); reject(error); });
      child.once('close', (code, signal) => { clearTimeout(timer); live.delete(child); resolveResult({ code, signal, stdout, stderr }); });
    });
  }
  const successful = async (executable, args, options) => {
    const result = await command(executable, args, options);
    assert.equal(result.code, 0, `${args.join(' ')}\n${result.stderr}`);
    return result.stdout;
  };
  const npm = (args, cwd = temporary) => successful(process.execPath, [npmCli, ...args], { cwd });
  const packed = JSON.parse(await npm(['pack', '--json', '--ignore-scripts', '--pack-destination', temporary], repository));
  const originalArchive = join(temporary, packed[0].filename);
  assert.ok(packed[0].files.some(file => file.path === 'dist/cli.js'));
  assert.ok(packed[0].files.every(file => !file.path.startsWith('src/') && !file.path.startsWith('node_modules/')));
  const prefix = join(temporary, 'npm-installation');
  const install = archive => npm(['install', '--global', '--prefix', prefix, '--ignore-scripts', '--no-audit', '--no-fund', archive]);
  await install(originalArchive);
  const cli = join(prefix, 'bin/fleet-node');
  const runCli = args => command(process.execPath, [cli, ...args]);
  const cliOk = args => successful(process.execPath, [cli, ...args]);
  const repoMetadataBefore = await readFile(join(repository, 'package.json'));
  const version = JSON.parse(repoMetadataBefore).version;
  assert.equal((await cliOk(['--version'])).trim(), version);
  const root = join(temporary, 'external-runtime');
  const home = join(temporary, 'node-home');
  const openclawRoot = join(home, '.openclaw');
  const openclawBin = join(temporary, 'tools/openclaw');
  await mkdir(join(openclawRoot, 'agents/main/agent'), { recursive: true });
  await mkdir(dirname(openclawBin));
  const config = join(temporary, 'node.json');
  const token = join(temporary, 'token');
  await writeFile(config, JSON.stringify({ config_schema_version: 3, node_label: 'bill', telemetry_endpoint: 'https://telemetry.invalid', codex_usage_enabled: true }));
  await writeFile(token, 'PACKED-TEST-ONLY-NOT-A-REAL-CREDENTIAL\n', { mode: 0o600 });
  await cliOk(['init', '--root', root, '--config', config, '--node', process.execPath, '--npm-cli', npmCli,
    '--prefix', prefix, '--home', home, '--openclaw-root', openclawRoot, '--openclaw', openclawBin,
    '--codex', join(temporary, 'tools/missing-codex'), '--token-file', token]);
  assert.equal((await stat(join(root, 'secrets/authorization-header'))).mode & 0o777, 0o600);
  const status = JSON.parse(await cliOk(['status', '--root', root]));
  assert.equal(status.enabled, false);
  // Never operate on an actual loaded launchd service from this disposable test.
  assert.ok(status.jobs.every(job => job.loaded === false), 'Real fleet-node-ts launchd jobs are installed on this machine; do not run disposable stop tests here');

  let missing = await runCli(['collect', 'cron', '--root', root]);
  assert.equal(missing.code, 1);
  assert.match(await readFile(join(root, 'textfiles/openclaw_cron_schedule.prom'), 'utf8'), /error_type="binary_missing"/);
  await cliOk(['collect', 'codex', '--root', root]);
  assert.match(await readFile(join(root, 'textfiles/codex_usage.prom'), 'utf8'), /error_type="app_server_missing"\} 0/);
  await cliOk(['collect', 'readiness', '--root', root]);
  assert.match(await readFile(join(root, 'textfiles/openclaw_gateway_ready.prom'), 'utf8'), /openclaw_gateway_check_timestamp_seconds\{node="bill"\} \d+/);
  await cliOk(['collect', 'thermal', '--root', root]);
  assert.match(await readFile(join(root, 'textfiles/macos_thermal.prom'), 'utf8'), /fleet_macos_thermal_collected_at_seconds/);

  const sourcePath = join(openclawRoot, 'agents/main/agent/openclaw-agent.sqlite');
  source = new DatabaseSync(sourcePath);
  source.exec('PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0; CREATE TABLE session_windows(session_id TEXT PRIMARY KEY,session_key TEXT,started_at INTEGER); CREATE TABLE session_nodes(session_key TEXT PRIMARY KEY,current_session_id TEXT,entry_json TEXT);');
  source.prepare('INSERT INTO session_windows VALUES(?,?,?)').run('test-one', 'agent:main:cron:job:run:test-one', Date.now());
  const sourceBefore = await readFile(sourcePath);
  const walBefore = await readFile(`${sourcePath}-wal`);
  await cliOk(['collect', 'sessions', '--root', root]);
  await cliOk(['collect', 'sessions', '--root', root]);
  assert.deepEqual(await readFile(sourcePath), sourceBefore);
  assert.deepEqual(await readFile(`${sourcePath}-wal`), walBefore);
  assert.match(await readFile(join(root, 'textfiles/openclaw_sessions.prom'), 'utf8'), /openclaw_sessions_collector_success\{[^\n]*\} 1/);
  const ledgerPath = join(root, 'state/sessions/starts.sqlite');
  const ledger = new DatabaseSync(ledgerPath, { readOnly: true });
  try { assert.equal(ledger.prepare('SELECT count(*) AS n FROM starts').get().n, 1); } finally { ledger.close(); }

  await writeFile(openclawBin, `#!${process.execPath}
const fs = require('node:fs');
const child = require('node:child_process').spawn(process.execPath,['-e','process.on("SIGTERM",()=>{});setInterval(()=>{},1000)'],{stdio:'inherit'});
fs.writeFileSync(${JSON.stringify(pidsPath)}, JSON.stringify([process.pid, child.pid]));
process.on('SIGTERM',()=>{});setInterval(()=>{},1000);
`);
  await chmod(openclawBin, 0o755);
  let hungFinished = false;
  const hangStarted = Date.now();
  fixtureProcessesMayExist = true;
  const hangingCron = runCli(['collect', 'cron', '--root', root]).then(result => { hungFinished = true; return result; });
  let pids;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { pids = JSON.parse(await readFile(pidsPath, 'utf8')); break; } catch { await delay(20); }
  }
  assert.equal(pids?.length, 2, 'Hung OpenClaw fixture did not start');
  await cliOk(['collect', 'heartbeat', '--root', root]);
  assert.equal(hungFinished, false, 'Heartbeat must complete while OpenClaw remains hung');
  const heartbeat = await readFile(join(root, 'textfiles/fleet_node_agent_heartbeat.prom'), 'utf8');
  const occurrence = Number(/fleet_node_agent_heartbeat_timestamp_seconds\{node="bill"\} (\d+)/.exec(heartbeat)[1]);
  assert.ok(Math.abs(Date.now() / 1000 - occurrence) < 10);
  const timedOut = await hangingCron;
  assert.equal(timedOut.code, 1);
  assert.ok(Date.now() - hangStarted >= 19_000, 'Exercise the production 20-second deadline, not a test-only shorter timeout');
  assert.ok(Date.now() - hangStarted < 30_000);
  assert.match(await readFile(join(root, 'textfiles/openclaw_cron_schedule.prom'), 'utf8'), /error_type="timeout"/);
  for (let attempt = 0; attempt < 40 && pids.some(pid => { try { process.kill(pid, 0); return true; } catch { return false; } }); attempt++) await delay(25);
  for (const pid of pids) assert.throws(() => process.kill(pid, 0), 'OpenClaw descendants must not survive their check');
  fixtureProcessesMayExist = false;

  const installed = join(prefix, 'lib/node_modules/@unblocklabs/fleet-node');
  // Rendering does not bootstrap services. Exercise the exact packed external launcher,
  // including its stale ownership cleanup and closed gate during npm's non-atomic writes.
  await successful(process.execPath, ['--input-type=module', '-e', `import {loadContext} from ${JSON.stringify(pathToFileURL(join(installed, 'dist/config.js')).href)};import {prepareServices} from ${JSON.stringify(pathToFileURL(join(installed, 'dist/services.js')).href)};await prepareServices(await loadContext(${JSON.stringify(root)}));`]);
  await writeFile(join(root, 'services/enabled'), 'enabled\n', { mode: 0o600 });
  await successful('/bin/sh', [join(root, 'services/launch.sh'), 'heartbeat']);
  await cliOk(['stop', '--root', root]);
  assert.equal(JSON.parse(await cliOk(['status', '--root', root])).enabled, false);
  await writeFile(join(root, 'queue/preserved-test-queue'), 'queued telemetry sentinel\n', { mode: 0o600 });
  await writeFile(join(root, 'logs/preserved-test.log'), 'retained log sentinel\n', { mode: 0o600 });
  const preservedPaths = ['config/node.json', 'config/local.json', 'secrets/authorization-header', 'queue/preserved-test-queue',
    'logs/preserved-test.log', 'state/sessions/starts.sqlite', 'textfiles/fleet_node_agent_heartbeat.prom'];
  const before = new Map(await Promise.all(preservedPaths.map(async path => [path, await readFile(join(root, path))])));
  const extracted = join(temporary, 'upgrade-package');
  await mkdir(extracted);
  await successful('/usr/bin/tar', ['-xzf', originalArchive, '-C', extracted]);
  const copy = join(extracted, 'package');
  const metadata = JSON.parse(await readFile(join(copy, 'package.json'), 'utf8'));
  metadata.version = '0.0.0-packed-upgrade-test';
  await writeFile(join(copy, 'package.json'), JSON.stringify(metadata, null, 2) + '\n');
  const upgraded = JSON.parse(await npm(['pack', '--json', '--ignore-scripts', '--pack-destination', temporary], copy));
  async function changeVersion(archive, expected) {
    let finished = false;
    const changing = install(archive).then(() => null, error => error).finally(() => { finished = true; });
    let launches = 0;
    do {
      await successful('/bin/sh', [join(root, 'services/launch.sh'), 'heartbeat']);
      launches++;
      await delay(20);
    } while (!finished || launches < 5);
    const failure = await changing;
    if (failure) throw failure;
    assert.equal((await cliOk(['--version'])).trim(), expected);
    assert.equal(JSON.parse(await cliOk(['status', '--root', root])).enabled, false);
    for (const [path, content] of before) assert.deepEqual(await readFile(join(root, path)), content, `${path} changed during npm replacement`);
  }
  await changeVersion(join(temporary, upgraded[0].filename), metadata.version);
  await changeVersion(originalArchive, version);
  assert.deepEqual(await readFile(join(repository, 'package.json')), repoMetadataBefore, 'Never mutate the repository package version for artifact testing');
  await cliOk(['collect', 'sessions', '--root', root]);
  const afterRollback = new DatabaseSync(ledgerPath, { readOnly: true });
  try { assert.equal(afterRollback.prepare('SELECT count(*) AS n FROM starts').get().n, 1); } finally { afterRollback.close(); }
});
