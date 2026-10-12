// Run installed collectors against Bill's actual sources without activating telemetry.
import assert from 'node:assert/strict';
import { backup, DatabaseSync } from 'node:sqlite';
import { mkdtemp, mkdir, readFile, copyFile, writeFile, stat, access } from 'node:fs/promises';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';

process.umask(0o077);
assert.equal(process.getuid(), 501);
const rollout = '/Users/billjohansson/fleet-node-ts-canary-20260927';
const installed = '/Users/billjohansson/.local/fleet-node-npm/lib/node_modules/@unblocklabs/fleet-node';
const runtime = '/Users/billjohansson/.local/share/fleet-node';
const legacy = '/Users/billjohansson/.openclaw/fleet-node-observability';
const node = '/opt/homebrew/opt/node@24/bin/node';
const cli = join(installed, 'dist/cli.js');
const execute = promisify(execFile);
const root = await mkdtemp(join(rollout, 'preflight-'));
for (const name of ['config', 'state', 'state/sessions', 'textfiles']) await mkdir(join(root, name), {recursive:true,mode:0o700});
for (const name of ['node.json','local.json']) await copyFile(join(runtime, 'config', name), join(root, 'config', name));
const configPath = '/Users/billjohansson/.openclaw/openclaw.json';
const digest = async path => createHash('sha256').update(await readFile(path)).digest('hex');
const openclawBefore = await digest(configPath);
const credentialBefore = await digest(join(legacy,'secrets/authorization-header'));
try { await access(join(runtime,'services/enabled')); throw new Error('Only run this pre-cutover check with the new gate closed'); }
catch (error) { if (error.code !== 'ENOENT') throw error; }

const ledgerPath = join(root,'state/sessions/starts.sqlite');
const original = new DatabaseSync(join(legacy,'state/sessions/starts.sqlite'),{readOnly:true,timeout:2000});
let before;
try {
  before = original.prepare('SELECT count(*) AS count FROM starts').get();
  before.observed_since = original.prepare("SELECT value FROM metadata WHERE key='observed_since'").get().value;
  await backup(original,ledgerPath);
} finally { original.close(); }
const results = {};
for (const job of ['heartbeat','readiness','thermal','cron','sessions','codex']) {
  const started = Date.now();
  try {
    await execute(node,[cli,'collect',job,'--root',root],{timeout:40000,maxBuffer:65536,env:{HOME:'/Users/billjohansson',PATH:'/opt/homebrew/opt/node@24/bin:/usr/bin:/bin:/usr/sbin:/sbin'}});
    results[job] = {exit:0,elapsedMs:Date.now()-started};
  } catch (error) {
    // Do not echo potentially sensitive subprocess stderr or RPC output.
    results[job] = {exit:typeof error.code === 'number' ? error.code : 'failed',elapsedMs:Date.now()-started};
  }
}
const contracts = {
  heartbeat:['fleet_node_agent_heartbeat.prom','fleet_node_agent_queue_metrics_available'],
  readiness:['openclaw_gateway_ready.prom','openclaw_gateway_ready'],
  thermal:['macos_thermal.prom','fleet_macos_thermal_collector_success'],
  cron:['openclaw_cron_schedule.prom','openclaw_cron_schedule_collector_success'],
  sessions:['openclaw_sessions.prom','openclaw_sessions_collector_success'],
  codex:['codex_usage.prom','codex_collector_success'],
};
for (const [job,[file,metric]] of Object.entries(contracts)) {
  const text = await readFile(join(root,'textfiles',file),'utf8');
  const sample = text.split('\n').find(line=>line.startsWith(metric+'{'));
  const value = sample ? Number(sample.slice(sample.lastIndexOf(' ')+1)) : null;
  results[job].success = value;
  assert.equal(results[job].exit,0,`${job} failed; inspect bounded error categories in scratch textfiles`);
  assert.equal(value,1,`${job} did not report success`);
}
const afterDb = new DatabaseSync(ledgerPath,{readOnly:true});
let after;
try {
  after = afterDb.prepare('SELECT count(*) AS count FROM starts').get();
  after.observed_since = afterDb.prepare("SELECT value FROM metadata WHERE key='observed_since'").get().value;
  assert.equal(after.observed_since,before.observed_since);
  assert.ok(after.count>=before.count);
  assert.equal(afterDb.prepare('PRAGMA integrity_check').get().integrity_check,'ok');
} finally { afterDb.close(); }
assert.equal(await digest(configPath),openclawBefore);
assert.equal(await digest(join(legacy,'secrets/authorization-header')),credentialBefore);
assert.equal((await stat(ledgerPath)).mode&0o777,0o600);
const report={at:new Date().toISOString(),scratch:root,results,history:{before,after},openclawConfigUnchanged:true,legacyCredentialUnchanged:true,newGateClosed:true};
await writeFile(join(root,'report.json'),JSON.stringify(report,null,2)+'\n',{mode:0o600,flag:'wx'});
console.log(JSON.stringify(report,null,2));
