// Read-only cutover guard. Invoked as the node account, never as root.
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstatSync, readFileSync, readdirSync, realpathSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';

const stage = dirname(fileURLToPath(import.meta.url));
const p = JSON.parse(readFileSync(join(stage, 'plan.json'), 'utf8'));
assert.equal(stage, p.stage);
assert.equal(process.getuid(), p.uid);
assert.equal(process.platform, 'darwin');
assert.equal(process.env.HOME, p.home);
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');
const regular = path => { const s = lstatSync(path); assert.ok(s.isFile() && !s.isSymbolicLink(), path); return s; };
const present = path => { try { lstatSync(path); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; } };
for (const path of [p.stage, p.root, p.prefix, p.legacy, join(stage,'rollback')]) {
  const s = lstatSync(path);
  assert.ok(s.isDirectory() && !s.isSymbolicLink() && s.uid === p.uid, path);
  assert.equal(realpathSync(path), path);
}
assert.equal(present(join(p.root, 'services/enabled')), false, 'New gate must be closed');
assert.equal(present(join(stage, 'cutover-state-preserved.json')), false, 'Import already completed; inspect before repeating');
assert.equal(present(join(stage, 'cutover-complete')), false);
for (const directory of ['queue','state/sessions']) assert.deepEqual(readdirSync(join(p.root,directory)), []);
for (const entry of readdirSync(join(p.root,'state'))) assert.ok(!entry.startsWith('queue-oldest-'));
const pkg = JSON.parse(readFileSync(join(p.prefix,'lib/node_modules/@unblocklabs/fleet-node/package.json'),'utf8'));
assert.equal(pkg.version,'0.1.0');
assert.equal(pkg.name,'@unblocklabs/fleet-node');
assert.deepEqual(JSON.parse(readFileSync(join(p.root,'config/node.json'),'utf8')),p.config);
assert.deepEqual(JSON.parse(readFileSync(join(p.root,'config/local.json'),'utf8')),p.settings);
for (const [path, digest] of Object.entries(p.guards)) { regular(path); assert.equal(hash(path),digest,'Changed staged/live file: '+path); }
for (const path of [join(p.legacy,'secrets/authorization-header'),join(p.root,'secrets/authorization-header')]) {
  const s=regular(path); assert.equal(s.uid,p.uid); assert.equal(s.mode & 0o077,0);
}
assert.equal(readFileSync(join(p.root,'secrets/authorization-header'),'utf8').trim(),readFileSync(join(p.legacy,'secrets/authorization-header'),'utf8').trim());
const run = (file,args) => execFileSync(file,args,{encoding:'utf8',timeout:20000,stdio:['ignore','pipe','pipe']});
const disabled = run('/bin/launchctl',['print-disabled','system']);
for (const service of p.services) {
  const saved=join(stage,'rollback',service.label+'.plist');
  const path=present(service.path)?service.path:saved;
  assert.equal(present(service.path)&&present(saved),false,'Ambiguous recovery plist');
  regular(path); assert.equal(hash(path),service.sha256,'Legacy service changed: '+service.label);
  if (path===service.path) assert.ok(!disabled.includes('"'+service.label+'" => disabled'),'Legacy service disabled since inventory');
}
for (const job of ['collector','exporter','heartbeat','readiness','thermal','cron','sessions','codex']) {
  assert.equal(present('/Library/LaunchDaemons/com.unblocklabs.fleet-node-ts.'+job+'.plist'),false);
  assert.notEqual(spawnSync('/bin/launchctl',['print','system/com.unblocklabs.fleet-node-ts.'+job]).status,0);
}
assert.ok(run(join(p.legacy,'bin/otelcol-contrib'),['--version']).includes('0.157.0'));
const db=new DatabaseSync(join(p.legacy,'state/sessions/starts.sqlite'),{readOnly:true,timeout:2000});
try {
  assert.equal(db.prepare('PRAGMA integrity_check').get().integrity_check,'ok');
  assert.equal(db.prepare("SELECT value FROM metadata WHERE key='observed_since'").get().value,p.observedSince);
} finally { db.close(); }
const receipt=JSON.parse(readFileSync(join(stage,'preflight-report.json'),'utf8'));
assert.equal(receipt.passed,true);
assert.equal(receipt.node,p.config.node_label);
run(p.node,[p.cli,'validate','--root',p.root]);
console.log('Pre-cutover validation passed; existing services have not been changed.');
