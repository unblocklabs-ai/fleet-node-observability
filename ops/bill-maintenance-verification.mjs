// One-time canary verification, not a package updater or installed runtime service.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstat, mkdtemp, readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';

process.umask(0o077);
assert.equal(process.getuid(), 501);
const root = '/Users/billjohansson/.local/share/fleet-node';
const prefix = '/Users/billjohansson/.local/fleet-node-npm';
const rollout = '/Users/billjohansson/fleet-node-ts-canary-20260927';
const node = '/opt/homebrew/opt/node@24/bin/node';
const npm = '/opt/homebrew/opt/node@24/lib/node_modules/npm/bin/npm-cli.js';
const cli = join(prefix, 'lib/node_modules/@unblocklabs/fleet-node/dist/cli.js');
const execute = promisify(execFile);
const env = {...process.env, PATH:'/opt/homebrew/opt/node@24/bin:/usr/bin:/bin:/usr/sbin:/sbin', npm_config_update_notifier:'false'};
const run = async args => (await execute(node, args, {env,timeout:45000,maxBuffer:1024*1024})).stdout;
const command = (name, ...args) => run([cli,name,...args,'--root',root]);
const status = async () => JSON.parse(await command('status'));
const reportDirectory = await mkdtemp(join(rollout, 'maintenance-verification-'));
const events = [];
function record(event, data = {}) {
  const value = {at:new Date().toISOString(),event,...data};
  events.push(value);
  console.log(JSON.stringify(value));
}
const digest = async path => createHash('sha256').update(await readFile(path)).digest('hex');
const artifacts = [
  {version:'0.1.1-canary-verification',file:'unblocklabs-fleet-node-0.1.1-canary-verification.tgz',hash:'455a74a4b9ca2ff7bb8c09c58e111063a68873dee18086357a6972dbdc642f47'},
  {version:'0.1.0',file:'unblocklabs-fleet-node-0.1.0.tgz',hash:'be9e44d4fe76036f3d135d133f0debfbc0c8200dc545f50df94b63c9829a4d51'},
];
for (const artifact of artifacts) assert.equal(await digest(join(rollout, artifact.file)), artifact.hash);
assert.equal((await status()).version, '0.1.0');

async function snapshot() {
  const result = {};
  for (const dir of ['config','secrets','queue','state','textfiles','logs']) {
    for (const name of (await readdir(join(root,dir),{recursive:true})).sort()) {
      const relative = join(dir,name);
      if (relative === 'state/jobs' || relative.startsWith('state/jobs/')) continue;
      const path = join(root,relative);
      const st = await lstat(path);
      assert.equal(st.isSymbolicLink(),false);
      assert.equal(st.uid,501);
      result[relative] = {mode:st.mode & 0o777,digest:st.isDirectory() ? null : await digest(path)};
    }
  }
  return result;
}

async function assertNoListeners() {
  const result = await execute('/usr/sbin/lsof',['-nP','-iTCP:9100','-iTCP:4318','-iTCP:8888','-iTCP:13133','-sTCP:LISTEN'],{timeout:5000}).then(() => 0, error => error.code);
  assert.equal(result,1,'Managed listeners must remain stopped while maintenance gate is closed');
  assert.equal((await status()).enabled,false);
}

async function healthy() {
  for (let attempt=0; attempt<60; attempt++) {
    const ok = await fetch('http://127.0.0.1:13133/',{signal:AbortSignal.timeout(1000)}).then(r=>r.ok,()=>false);
    const exported = await fetch('http://127.0.0.1:9100/metrics',{signal:AbortSignal.timeout(1000)}).then(async r=>{await r.arrayBuffer();return r.ok;},()=>false);
    if (ok && exported) {
      const current = await status();
      assert.equal(current.enabled,true);
      assert.ok(current.jobs.every(job=>job.loaded));
      // Scheduled one-shots may finish between status and ps; only daemons must persist.
      for (const job of current.jobs.filter(job=>['collector','exporter'].includes(job.job))) {
        assert.ok(job.pid);
        const {stdout} = await execute('/bin/ps',['-p',String(job.pid),'-o','uid='],{timeout:5000});
        assert.equal(Number(stdout.trim()),501);
      }
      return current;
    }
    await delay(500);
  }
  throw new Error('Managed listeners did not recover after opening gate');
}

const openclawConfig = '/Users/billjohansson/.openclaw/openclaw.json';
const openclawBefore = await digest(openclawConfig);
try {
for (const artifact of artifacts) {
  await command('stop');
  await assertNoListeners();
  const before = await snapshot();
  // Let KeepAlive retry during the closed gate, rather than assuming a kill is enough.
  await delay(12000);
  await assertNoListeners();
  assert.deepEqual(await snapshot(),before);
  record('gate-survived-keepalive',{targetVersion:artifact.version,protectedEntries:Object.keys(before).length});
  await run([npm,'install','--global','--prefix',prefix,'--ignore-scripts','--offline','--no-audit','--no-fund',join(rollout,artifact.file)]);
  assert.equal((await run([cli,'--version'])).trim(),artifact.version);
  await assertNoListeners();
  assert.deepEqual(await snapshot(),before);
  await command('prepare');
  await command('validate');
  assert.deepEqual(await snapshot(),before);
  record('npm-version-change-preserved-state',{version:artifact.version});
  await command('start');
  const started = await healthy();
  record('launchd-recovered',{version:artifact.version,pids:started.jobs.filter(job=>job.pid).map(job=>({job:job.job,pid:job.pid}))});
}
const beforeRestart = await healthy();
await command('restart');
const afterRestart = await healthy();
for (const job of ['collector','exporter']) {
  assert.notEqual(afterRestart.jobs.find(j=>j.job===job).pid,beforeRestart.jobs.find(j=>j.job===job).pid);
}
assert.equal(await digest(openclawConfig),openclawBefore);
record('ordinary-user-restart-passed',{finalVersion:afterRestart.version,openclawConfigUnchanged:true});
} catch (error) {
  record('verification-failed',{errorType:error.name});
  // Never reopen a gate after an unverified npm replacement. Retain evidence for
  // explicit recovery with the original artifact by the coordinating operator.
  throw error;
} finally {
  const final = await status().catch(()=>null);
  await writeFile(join(reportDirectory,'report.json'),JSON.stringify({uid:process.getuid(),node:process.version,events,final},null,2)+'\n',{flag:'wx',mode:0o600});
  console.log(JSON.stringify({report:join(reportDirectory,'report.json')}));
}
