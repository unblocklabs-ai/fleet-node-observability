import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, stat, symlink, access, readdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawn } from 'node:child_process';
import { writeAtomic, readRegular } from '../dist/io.js';
import { parseConfig } from '../dist/config.js';
import { runCommand } from '../dist/process.js';

test('private atomic publication never follows a symlink and leaves complete old/new content', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'fleet-node-private-'));
  const path = join(dir, 'secret');
  await writeAtomic(path, 'before');
  const writes = Array.from({length: 12}, (_, i) => writeAtomic(path, String(i).repeat(10000)));
  await Promise.all(writes);
  const value = await readFile(path, 'utf8');
  assert.ok(Array.from({length: 12}, (_, i) => String(i).repeat(10000)).includes(value));
  assert.equal((await stat(path)).mode & 0o777, 0o600);
  assert.equal((await stat(dir)).mode & 0o777, 0o700);
  assert.deepEqual(await readdir(dir), ['secret']);
  const link = join(dir, 'link'); await symlink(path, link);
  await assert.rejects(writeAtomic(link, 'oops'));
  assert.equal(await readFile(path, 'utf8'), value);
  await assert.rejects(readRegular(link, 1024, true));
  await writeFile(join(dir, 'public-token'), 'token', {mode: 0o644});
  await assert.rejects(readRegular(join(dir, 'public-token'), 1024, true));
});

test('schema3 rejects endpoint credential tricks and missing/extra fields', () => {
  const base = {config_schema_version: 3, node_label: ' BILL ', telemetry_endpoint: 'https://telemetry.example.test', codex_usage_enabled: false};
  assert.equal(parseConfig(base).node_label, 'bill');
  for (const endpoint of ['http://host','https://u:pass@host','https://host/v1/logs','https://host?x','https://host#x','https://host:0','https://host:',' https://host','https://host\\evil']) {
    assert.throws(() => parseConfig({...base,telemetry_endpoint:endpoint}), endpoint);
  }
  assert.throws(() => parseConfig({...base,path:'/secret'}));
  assert.throws(() => parseConfig({...base,codex_usage_enabled:1}));
});

test('bounded subprocess kills a hung process and its TERM-ignoring child', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'fleet-node-process-'));
  const pidPath = join(dir, 'pid');
  const script = `const {spawn}=require('node:child_process');const fs=require('node:fs');const p=spawn(process.execPath,['-e','process.on("SIGTERM",()=>{});setInterval(()=>{},1000)'],{stdio:'inherit'});fs.writeFileSync(${JSON.stringify(pidPath)},String(p.pid));process.on('SIGTERM',()=>{});setInterval(()=>{},1000);`;
  await assert.rejects(runCommand(process.execPath,['-e',script],{timeoutMs:500}), e => e.kind === 'timeout');
  const pid = Number(await readFile(pidPath, 'utf8'));
  await new Promise(r=>setTimeout(r,100));
  assert.throws(()=>process.kill(pid,0));
});

test('subprocess deadline still applies if parent exits but a descendant holds stdout', async () => {
  const start = Date.now();
  const script = `require('node:child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'inherit'});process.exit(0)`;
  await assert.rejects(runCommand(process.execPath,['-e',script],{timeoutMs:300}), e => e.kind === 'timeout');
  assert.ok(Date.now()-start<2000);
});

test('missing subprocess and excessive output fail boundedly', async () => {
  await assert.rejects(runCommand('/nonexistent/fleet-node-test',[],{timeoutMs:300}), e=>e.kind==='missing');
  await assert.rejects(runCommand(process.execPath,['-e','setInterval(()=>process.stdout.write("x".repeat(10000)),1)'],{timeoutMs:2000,maxBytes:1000}), e=>e.kind==='output_limit');
});

test('terminating collector parent cleans up its subprocess group', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'fleet-node-terminate-'));
  const pidPath = join(dir, 'child-pid');
  const moduleUrl = new URL('../dist/process.js', import.meta.url).href;
  const code = `import {runCommand} from ${JSON.stringify(moduleUrl)}; await runCommand(process.execPath,['-e',${JSON.stringify(`require('node:fs').writeFileSync(${JSON.stringify(pidPath)},String(process.pid));process.on('SIGTERM',()=>{});setInterval(()=>{},1000);`)}],{timeoutMs:10000});`;
  const parent = spawn(process.execPath,['--input-type=module','-e',code],{stdio:'ignore'});
  for(let i=0;i<50;i++){try{await access(pidPath);break;}catch{await new Promise(r=>setTimeout(r,20));}}
  const pid=Number(await readFile(pidPath,'utf8'));
  const closed=new Promise(r=>parent.once('close',r)); parent.kill('SIGTERM'); await closed;
  await new Promise(r=>setTimeout(r,100));
  assert.throws(()=>process.kill(pid,0));
});

test('repeated stop signals cannot interrupt child cleanup and the first signal owns final exit', async t => {
  for (const [first, second] of [['SIGTERM', 'SIGTERM'], ['SIGINT', 'SIGINT'], ['SIGTERM', 'SIGINT']]) {
    const dir = await mkdtemp(join(tmpdir(), 'fleet-node-repeat-stop-'));
    const pidPath = join(dir, 'pid');
    const noticed = join(dir, 'group-term-received');
    const childCode = `const fs=require('node:fs');process.on('SIGTERM',()=>fs.writeFileSync(${JSON.stringify(noticed)},'received'));fs.writeFileSync(${JSON.stringify(pidPath)},String(process.pid));setInterval(()=>{},1000);`;
    const code = `import {runCommand} from ${JSON.stringify(new URL('../dist/process.js', import.meta.url).href)};await runCommand(process.execPath,['-e',${JSON.stringify(childCode)}],{timeoutMs:10000});`;
    const parent = spawn(process.execPath, ['--input-type=module', '-e', code], { stdio: 'ignore' });
    const closed = new Promise(resolve => parent.once('close', (exitCode, signal) => resolve({ exitCode, signal })));
    let pid;
    t.after(async () => {
      if (parent.exitCode === null && parent.signalCode === null) parent.kill('SIGKILL');
      if (pid) { try { process.kill(pid, 'SIGKILL'); } catch {} }
      await rm(dir, { recursive: true, force: true });
    });
    const waitForFile = async path => {
      for (let attempt = 0; attempt < 200; attempt++) {
        try { await access(path); return; } catch { await new Promise(resolve => setTimeout(resolve, 5)); }
      }
      assert.fail('Subprocess fixture did not reach its expected signal state');
    };
    await waitForFile(pidPath);
    pid = Number(await readFile(pidPath, 'utf8'));
    parent.kill(first);
    // Send the repeat while the child is inside the termination grace, not by
    // guessing process startup timing or shortening the production grace.
    await waitForFile(noticed);
    parent.kill(second);
    assert.deepEqual(await closed, { exitCode: null, signal: first });
    for (let attempt = 0; attempt < 40; attempt++) {
      try { process.kill(pid, 0); } catch { break; }
      await new Promise(resolve => setTimeout(resolve, 5));
    }
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
    pid = undefined;
  }
});
