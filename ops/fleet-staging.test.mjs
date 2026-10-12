// One-time staged-script acceptance test; requires the inventoried staging artifacts.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, mkdir, readFile, writeFile, rm, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import test from 'node:test';
const execute=promisify(execFile);
const plans=new URL('../artifacts/fleet-staging-20260928/',import.meta.url);
const exists=path=>access(path).then(()=>true,()=>false);

async function fixture(t,host,{precheckFail=false,hung=false}={}) {
  const p=JSON.parse(await readFile(new URL(host+'/plan.json',plans),'utf8'));
  const base=await mkdtemp(join(tmpdir(),'fleet-staged-cutover-'));
  t.after(()=>rm(base,{recursive:true,force:true}));
  const home=join(base,'home'), system=join(base,'LaunchDaemons');
  const stage=p.stage.replace(p.home,home), root=p.root.replace(p.home,home);
  for(const path of [stage+'/rollback',root+'/services',system])await mkdir(path,{recursive:true});
  for(const s of p.services)await writeFile(system+'/'+s.label+'.plist','original '+s.label);
  const imported=join(base,'imported'),started=join(base,'started'),events=join(base,'events');
  await writeFile(root+'/services/install-system.sh',`#!/bin/sh\ntest -f '${imported}'\n`);
  let script=(await readFile(new URL(host+'/cutover.sh',plans),'utf8')).replaceAll(p.home,home).replaceAll('/Library/LaunchDaemons',system);
  const stubs={
    '/usr/bin/id':`[ "$#" = 1 ] && echo 0 || echo ${p.uid}`,
    '/usr/bin/uname':'echo Darwin',
    '/usr/bin/sudo':`case "$*" in *fleet-precheck.mjs*) exit ${precheckFail?1:0};; *import-state.mjs*) test -z "$(find '${system}' -name '*.plist' -print)" || exit 1; touch '${imported}';; *' start '*) test -f '${imported}' && touch '${started}';; esac`,
    '/bin/launchctl':`printf '%s\\n' "$*" >> '${events}'; case "$1" in print) exit 1;; print-disabled) printf '%s\\n' ${p.services.map(s=>`'"${s.label}" => disabled'`).join(' ')};; esac`,
    '/usr/sbin/lsof':`exit ${hung?0:1}`,
    '/usr/bin/pgrep':'exit 1',
    '/usr/sbin/chown':'exit 0',
    '/bin/sleep':'exit 0',
  };
  for(const [path,body] of Object.entries(stubs)) {
    const target=join(base,path.split('/').at(-1));
    await writeFile(target,'#!/bin/sh\n'+body+'\n',{mode:0o700});script=script.replaceAll(path,target);
  }
  const file=join(base,'cutover.sh');await writeFile(file,script);
  return {run:()=>execute('/bin/sh',[file],{timeout:8000}),base,stage,system,imported,started,events,p};
}

test('staged six/seven-job variants park exactly their surveyed jobs before import and activation',async t=>{
  for(const host of ['bridger','fred','mac-thesis-theo']) {
    const f=await fixture(t,host);await f.run();
    for(const s of f.p.services) {
      assert.equal(await exists(f.system+'/'+s.label+'.plist'),false);
      assert.equal(await readFile(f.stage+'/rollback/'+s.label+'.plist','utf8'),'original '+s.label);
    }
    assert.equal(await exists(f.started),true);
    assert.equal(await exists(f.stage+'/cutover-complete'),true);
    assert.equal((await readFile(f.events,'utf8')).includes('node-exporter-proxy'),false);
  }
});

test('failed precheck leaves all existing services untouched',async t=>{
  const f=await fixture(t,'fred',{precheckFail:true});await assert.rejects(f.run());
  assert.equal(await exists(f.events),false);assert.equal(await exists(f.imported),false);
  for(const s of f.p.services)assert.equal(await exists(f.system+'/'+s.label+'.plist'),true);
});

test('hung legacy process prevents importing live queues and retains recovery plists',async t=>{
  const f=await fixture(t,'bridger',{hung:true});await assert.rejects(f.run());
  assert.equal(await exists(f.imported),false);assert.equal(await exists(f.started),false);
  for(const s of f.p.services)assert.equal(await exists(f.stage+'/rollback/'+s.label+'.plist'),true);
});
