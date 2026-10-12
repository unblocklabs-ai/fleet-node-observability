// Read-only live acceptance snapshot, executed over SSH stdin as the node account.
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, lstatSync, readFileSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

const stage=join(homedir(),'.local/share/fleet-node-migration-20260928');
const p=JSON.parse(readFileSync(join(stage,'plan.json'),'utf8'));
assert.equal(process.getuid(),p.uid);
const run=(file,args)=>execFileSync(file,args,{encoding:'utf8',timeout:20000,maxBuffer:1024*1024});
const status=JSON.parse(run(p.node,[p.cli,'status','--root',p.root]));
const cutover=lstatSync(join(stage,'cutover-complete')).mtimeMs/1000;
const imported=JSON.parse(readFileSync(join(stage,'cutover-state-preserved.json'),'utf8'));
const disabled=run('/bin/launchctl',['print-disabled','system']);
const legacy=p.services.map(s=>({label:s.label,unloaded:spawnSync('/bin/launchctl',['print','system/'+s.label]).status!==0,disabled:disabled.includes('"'+s.label+'" => disabled'),parked:existsSync(join(stage,'rollback',s.label+'.plist'))}));
for(const job of status.jobs){
  const output=run('/bin/launchctl',['print','system/'+job.label]);
  job.runs=Number(output.match(/\n\s*runs = (\d+)/)?.[1]);
  job.lastExit=output.match(/\n\s*last exit code = (\S+)/)?.[1]??null;
  if(job.pid)job.uid=Number(run('/bin/ps',['-p',String(job.pid),'-o','uid=']).trim());
}
const metrics=[];
for(const file of readdirSync(join(p.root,'textfiles')).filter(f=>f.endsWith('.prom'))){
  for(const line of readFileSync(join(p.root,'textfiles',file),'utf8').split('\n')){
    if(!line||line.startsWith('#'))continue;
    const name=line.split(/[ {]/)[0];
    if(/heartbeat_timestamp_seconds$|queue_metrics_available$|collector_success$|collected_at_seconds$|check_timestamp_seconds$|gateway_ready$|observed_since_seconds$/.test(name)){
      metrics.push({name,value:Number(line.trim().split(/\s+/).at(-1)),errorType:line.match(/error_type="([a-z_]+)"/)?.[1]});
    }
  }
}
const before=new DatabaseSync(join(p.legacy,'state/sessions/starts.sqlite'),{readOnly:true,timeout:2000});
const after=new DatabaseSync(join(p.root,'state/sessions/starts.sqlite'),{readOnly:true,timeout:2000});
let history;
try{
  const oldRows=before.prepare('SELECT identity,day,cron FROM starts').all();
  const currentRows=after.prepare('SELECT identity,day,cron FROM starts').all();
  const current=new Map(currentRows.map(r=>[r.identity,r]));
  history={imported:imported.session.count,oldCount:oldRows.length,newCount:currentRows.length,
    missing:oldRows.filter(r=>!current.has(r.identity)).length,
    changed:oldRows.filter(r=>current.has(r.identity)&&(current.get(r.identity).day!==r.day||current.get(r.identity).cron!==r.cron)).length,
    observedSince:after.prepare("SELECT value FROM metadata WHERE key='observed_since'").get().value,
    expectedSince:p.observedSince,integrity:after.prepare('PRAGMA integrity_check').get().integrity_check};
}finally{before.close();after.close();}
const scrape=await fetch('http://127.0.0.1:8888/metrics',{signal:AbortSignal.timeout(5000)}).then(r=>r.text());
const collector=[];
for(const line of scrape.split('\n')){
 const match=/^(otelcol_exporter_(?:queue_size|(?:sent_metric_points|sent_log_records|sent_spans|send_failed_metric_points|send_failed_log_records|send_failed_spans)(?:_total)?))\{.*exporter="([^"]+)".*\}\s+(\S+)/.exec(line);
 if(match)collector.push({metric:match[1],exporter:match[2],value:Number(match[3])});
}
const openclaw=join(p.home,'.openclaw/openclaw.json');
const protectedFilesUnchanged=[openclaw,join(p.legacy,'config/collector.json')].every(path=>createHash('sha256').update(readFileSync(path)).digest('hex')===p.guards[path]);
const health=await fetch('http://127.0.0.1:13133/',{signal:AbortSignal.timeout(5000)}).then(r=>r.ok,()=>false);
const listeners=run('/usr/sbin/lsof',['-nP','-iTCP:9100','-iTCP:4318','-iTCP:8888','-iTCP:13133','-sTCP:LISTEN']);
const validation=run(p.node,[p.cli,'validate','--root',p.root]).trim();
console.log(JSON.stringify({at:Date.now()/1000,node:p.config.node_label,codexEnabled:p.config.codex_usage_enabled,cutover,status,legacy,history,metrics,collector,protectedFilesUnchanged,health,listeners,validation}));
