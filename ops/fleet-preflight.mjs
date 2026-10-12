// Exercise the installed registry CLI against actual sources, writing only scratch output.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { backup, DatabaseSync } from 'node:sqlite';
import { copyFile, lstat, mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

process.umask(0o077);
const stage=dirname(fileURLToPath(import.meta.url));
const p=JSON.parse(await readFile(join(stage,'plan.json'),'utf8'));
assert.equal(process.getuid(),p.uid);
const run=promisify(execFile);
const hash=async path=>createHash('sha256').update(await readFile(path)).digest('hex');
const protectedFiles=[join(p.home,'.openclaw/openclaw.json'),join(p.legacy,'secrets/authorization-header'),join(p.legacy,'config/collector.json')];
const before=await Promise.all(protectedFiles.map(hash));
const scratch=await mkdtemp(join(stage,'preflight-'));
for (const path of ['config','state/sessions','textfiles']) await mkdir(join(scratch,path),{recursive:true,mode:0o700});
for (const name of ['node.json','local.json']) await copyFile(join(p.root,'config',name),join(scratch,'config',name));
const original=new DatabaseSync(join(p.legacy,'state/sessions/starts.sqlite'),{readOnly:true,timeout:2000});
let count;
try { count=original.prepare('SELECT count(*) AS count FROM starts').get().count; await backup(original,join(scratch,'state/sessions/starts.sqlite')); }
finally {original.close();}
const contracts={heartbeat:['fleet_node_agent_heartbeat.prom','fleet_node_agent_queue_metrics_available'],readiness:['openclaw_gateway_ready.prom','openclaw_gateway_ready'],thermal:['macos_thermal.prom','fleet_macos_thermal_collector_success'],cron:['openclaw_cron_schedule.prom','openclaw_cron_schedule_collector_success'],sessions:['openclaw_sessions.prom','openclaw_sessions_collector_success'],codex:['codex_usage.prom','codex_collector_success']};
if(!p.config.codex_usage_enabled)delete contracts.codex;
const results={};
for(const [job,[file,metric]] of Object.entries(contracts)) {
  const start=Date.now();let code=0;
  try {await run(p.node,[p.cli,'collect',job,'--root',scratch],{timeout:45000,maxBuffer:65536,env:{...process.env,PATH:[dirname(p.node),dirname(p.settings.openclawBin),dirname(p.settings.codexBin),'/usr/bin','/bin','/usr/sbin','/sbin'].join(':')}});}
  catch(error){code=typeof error.code==='number'?error.code:-1;}
  let value=null;
  try { const text=await readFile(join(scratch,'textfiles',file),'utf8');const sample=text.split('\n').find(line=>line.startsWith(metric+'{'));value=sample?Number(sample.trim().split(' ').at(-1)):null; } catch {}
  results[job]={exit:code,success:value,elapsedMs:Date.now()-start};
}
assert.deepEqual(await Promise.all(protectedFiles.map(hash)),before,'Protected live configuration changed');
const db=new DatabaseSync(join(scratch,'state/sessions/starts.sqlite'),{readOnly:true});
let history;
try {
  history={before:count,after:db.prepare('SELECT count(*) AS count FROM starts').get().count,observedSince:db.prepare("SELECT value FROM metadata WHERE key='observed_since'").get().value,integrity:db.prepare('PRAGMA integrity_check').get().integrity_check};
} finally {db.close();}
assert.equal(history.observedSince,p.observedSince);
assert.equal(history.integrity,'ok');
assert.equal((await lstat(join(scratch,'state/sessions/starts.sqlite'))).mode & 0o777,0o600);
const report={at:new Date().toISOString(),node:p.config.node_label,scratch,results,history,protectedConfigurationUnchanged:true,passed:Object.values(results).every(r=>r.exit===0&&r.success===1)};
await writeFile(join(stage,'preflight-report.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx',mode:0o600});
console.log(JSON.stringify(report));
assert.equal(report.passed,true,'Real-source preflight failed; do not cut over');
