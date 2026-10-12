import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, chmod, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { setTimeout as delay } from 'node:timers/promises';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { fetchCodexUsage, collectCodex, parseCodexUsage, renderCodex } from '../dist/codex.js';

function context(root, codexBin = join(root, 'codex')) {
  return { root, config: { config_schema_version: 3, node_label: 'bill', telemetry_endpoint: 'https://unused.invalid', codex_usage_enabled: true },
    settings: { nodePath: process.execPath, nodeHome: root, openclawRoot: join(root, 'openclaw'), openclawBin: join(root, 'openclaw-bin'), codexBin, npmCliPath: join(root, 'npm'), npmPrefix: join(root, 'prefix') } };
}

async function fixture(t, mode = 'success') {
  const root = await mkdtemp(join(tmpdir(), 'fleet-codex-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const codex = join(root, 'codex');
  await writeFile(codex, `#!${process.execPath}
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const mode = ${JSON.stringify(mode)};
const root = ${JSON.stringify(root)};
if (JSON.stringify(process.argv.slice(2)) !== JSON.stringify(['-s','read-only','-a','never','app-server'])) process.exit(13);
const descendant = spawn(process.execPath, ['-e', 'process.on("SIGTERM",()=>{});setInterval(()=>{},1000)'], { stdio: ['ignore', 1, 2] });
fs.writeFileSync(root + '/pids', JSON.stringify([process.pid, descendant.pid]));
if (mode === 'hang') { process.on('SIGTERM',()=>{}); setInterval(()=>{},1000); }
else if (mode === 'flood') { process.stdout.write('x'.repeat(2 * 1024 * 1024)); setInterval(()=>{},1000); }
else {
 let initialized = false;
 require('node:readline').createInterface({input:process.stdin}).on('line', line => {
  const request = JSON.parse(line);
  fs.appendFileSync(root + '/requests', line + '\\n');
  if(request.method === 'initialized') { initialized=true; return; }
  if(request.method !== 'initialize' && !initialized) process.exit(14);
  if(request.method === 'account/read' && request.params.refreshToken !== false) process.exit(15);
  const result = request.method === 'initialize' ? {serverInfo:{name:'fixture'}}
   : request.method === 'account/read' ? {account:{email:'Bill@Example.COM',planType:'pro'}}
   : {rateLimits:{primary:{usedPercent:99}},rateLimitsByLimitId:{codex:{primary:{usedPercent:10,windowDurationMins:10080,resetsAt:1770000000},secondary:null,credits:{hasCredits:false,unlimited:true,balance:0}}}};
  if (mode === 'error' && request.method === 'account/rateLimits/read') console.log(JSON.stringify({id:request.id,error:{message:'SECRET_MARKER_MUST_NOT_ESCAPE'}}));
  else console.log(JSON.stringify({id:request.id,result}));
 });
}
`);
  await chmod(codex, 0o755);
  return context(root);
}

async function assertProcessesGone(root) {
  const pids = JSON.parse(await readFile(join(root, 'pids'), 'utf8'));
  for (let attempt = 0; attempt < 30; attempt++) {
    if (pids.every(pid => { try { process.kill(pid, 0); return false; } catch { return true; } })) return;
    await delay(25);
  }
  assert.fail(`Subprocesses survived cleanup: ${pids.join(',')}`);
}

test('real app-server protocol prefers codex bucket, omits missing windows, and cleans descendants on success', async t => {
  const ctx = await fixture(t);
  const snapshot = await fetchCodexUsage(ctx, 2000);
  assert.equal(snapshot.accountDomain, 'example.com');
  assert.equal(snapshot.planType, 'pro');
  assert.equal(snapshot.primary.used_percent, 10);
  assert.equal(snapshot.primary.window_minutes, 10080);
  assert.equal(snapshot.secondary, null);
  assert.deepEqual(snapshot.credits, { has_credits: 0, unlimited: 1, balance: 0 });
  const requests = (await readFile(join(ctx.root, 'requests'), 'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(requests.map(r => r.method), ['initialize', 'initialized', 'account/read', 'account/rateLimits/read']);
  assert.equal('id' in requests[1], false);
  assert.deepEqual(requests[2].params, { refreshToken: false });
  const metrics = renderCodex('bill', 1000, snapshot);
  assert.match(metrics, /codex_usage_primary_window_minutes.* 10080/);
  assert.doesNotMatch(metrics, /codex_usage_secondary|account_email|Bill@|snapshot_age_seconds/);
  assert.match(metrics, /account_domain="example.com"/);
  await assertProcessesGone(ctx.root);
});

test('timeout and parent termination remove stdout-holding descendants', async t => {
  const ctx = await fixture(t, 'hang');
  const start = Date.now();
  await assert.rejects(fetchCodexUsage(ctx, 1000), error => error.errorType === 'app_server_timeout');
  assert.ok(Date.now() - start < 3000);
  await assertProcessesGone(ctx.root);
  const wrapper = join(ctx.root, 'wrapper.mjs');
  await writeFile(wrapper, `import {fetchCodexUsage} from ${JSON.stringify(new URL('../dist/codex.js', import.meta.url).href)};await fetchCodexUsage(${JSON.stringify(ctx)}, 30000);`);
  await rm(join(ctx.root, 'pids'));
  const processUnderTest = spawn(process.execPath, [wrapper], { stdio: 'ignore' });
  t.after(() => { processUnderTest.kill('SIGKILL'); });
  const exited = once(processUnderTest, 'exit');
  for (let attempt = 0; attempt < 100; attempt++) {
    try { await readFile(join(ctx.root, 'pids')); break; } catch { await delay(20); }
  }
  processUnderTest.kill('SIGTERM');
  const [, signal] = await exited;
  assert.equal(signal, 'SIGTERM');
  await assertProcessesGone(ctx.root);
});

test('missing and erroring Codex publish bounded failures without private server messages', async t => {
  const ctx = await fixture(t, 'error');
  await collectCodex(ctx);
  let metrics = await readFile(join(ctx.root, 'textfiles/codex_usage.prom'), 'utf8');
  assert.match(metrics, /error_type="app_server_capability_unavailable"\} 0/);
  assert.doesNotMatch(metrics, /SECRET_MARKER|Bill@|primary_used/);
  ctx.settings.codexBin = join(ctx.root, 'absent');
  await collectCodex(ctx);
  metrics = await readFile(join(ctx.root, 'textfiles/codex_usage.prom'), 'utf8');
  assert.match(metrics, /error_type="app_server_missing"\} 0/);
  assert.match(metrics, /codex_usage_collected_at_seconds/);
  await assertProcessesGone(ctx.root);
});

test('unavailable or non-finite usage is not fabricated as zero', () => {
  assert.throws(() => parseCodexUsage({ rateLimits: { primary: { usedPercent: 'NaN' }, secondary: null } }, {}), error => error.errorType === 'app_server_no_usage');
  const creditsOnly = parseCodexUsage({ rateLimits: { credits: { balance: 0 } } }, {});
  assert.equal(creditsOnly.primary, null);
  assert.match(renderCodex('bill', 1000, creditsOnly), /codex_credits_balance.* 0/);
  assert.doesNotMatch(renderCodex('bill', 1000, creditsOnly), /codex_usage_primary/);
});

test('oversized app-server output is bounded and its processes are cleaned', async t => {
  const ctx = await fixture(t, 'flood');
  await assert.rejects(fetchCodexUsage(ctx, 3000), error => error.errorType === 'app_server_invalid_response');
  await assertProcessesGone(ctx.root);
});
