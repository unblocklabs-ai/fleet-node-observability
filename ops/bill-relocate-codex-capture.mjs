// One-time Bill cleanup prerequisite; preserves the independent capture service.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstatSync, readFileSync, writeFileSync, renameSync, unlinkSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';

process.umask(0o077);
assert.equal(process.getuid(), 501);
assert.equal(process.platform, 'darwin');
const run = (file, args) => execFileSync(file, args, { encoding: 'utf8', timeout: 15000 });
const plist = '/Users/billjohansson/Library/LaunchAgents/com.billjohansson.openclaw-codex-otel-capture.plist';
const service = 'gui/501/com.billjohansson.openclaw-codex-otel-capture';
const oldBinary = '/Users/billjohansson/.openclaw/fleet-node-observability/bin/otelcol-contrib';
const newBinary = '/Users/billjohansson/.local/share/fleet-node/bin/otelcol-contrib';
const config = '/Users/billjohansson/.openclaw/codex-otel-capture/collector.json';
const output = '/Users/billjohansson/.openclaw/codex-otel-capture/otel-logs.json';
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');
const expected = '5caadd3c5f99484b70c2cc204422956942ea8151d6ad1baf5f226b10332b2f19';
for (const binary of [oldBinary, newBinary]) assert.equal(hash(binary), expected);
const st = lstatSync(plist);
assert.ok(st.isFile() && !st.isSymbolicLink() && st.uid === 501);
const original = readFileSync(plist);
// The file exporter truncates by default. Do not restart a capture until its
// existing output is backed up and append mode has been enabled separately.
assert.equal(JSON.parse(readFileSync(config, 'utf8')).exporters['file/codex'].append, true);
const parsed = JSON.parse(run('/usr/bin/plutil', ['-convert', 'json', '-o', '-', plist]));
assert.deepEqual(parsed.ProgramArguments, [oldBinary, '--config=file:' + config]);
assert.equal(parsed.Label, service.split('/').at(-1));
const beforeConfig = hash(config);
const beforeOutput = lstatSync(output);
const oldPid = Number(run('/bin/launchctl', ['print', service]).match(/\n\s*pid = (\d+)/)?.[1]);
assert.ok(oldPid > 0);
const temporary = plist + '.cleanup-' + process.pid;
writeFileSync(temporary, original, { flag: 'wx', mode: st.mode & 0o777 });
const updated = { ...parsed, ProgramArguments: [newBinary, '--config=file:' + config] };
run('/usr/bin/plutil', ['-replace', 'ProgramArguments', '-json', JSON.stringify(updated.ProgramArguments), temporary]);
run('/usr/bin/plutil', ['-lint', temporary]);
assert.deepEqual(JSON.parse(run('/usr/bin/plutil', ['-convert', 'json', '-o', '-', temporary])), updated);
let bootedOut = false;
try {
  run('/bin/launchctl', ['bootout', service]);
  bootedOut = true;
  for (let i = 0; i < 20; i++) {
    let alive = true;
    try { process.kill(oldPid, 0); } catch (error) { if (error.code === 'ESRCH') alive = false; else throw error; }
    if (!alive) break;
    assert.ok(i < 19, 'Capture did not quiesce');
    await delay(250);
  }
  renameSync(temporary, plist);
  run('/bin/launchctl', ['bootstrap', 'gui/501', plist]);
  let newPid;
  for (let i = 0; i < 120; i++) {
    const current = run('/bin/launchctl', ['print', service]);
    newPid = Number(current.match(/\n\s*pid = (\d+)/)?.[1]);
    if (newPid && current.includes('state = running')) {
      const args = run('/bin/ps', ['-p', String(newPid), '-o', 'uid=,command=']);
      assert.ok(args.trim().startsWith('501 '));
      assert.ok(args.includes(newBinary + ' --config=file:' + config));
      try {
        assert.ok(run('/usr/sbin/lsof', ['-nP', '-a', '-p', String(newPid), '-iTCP:4319', '-sTCP:LISTEN']).includes('127.0.0.1:4319'));
        break;
      } catch (error) { if (i === 119) throw error; }
    }
    assert.ok(i < 119, 'Capture did not restart');
    await delay(250);
  }
  assert.equal(hash(config), beforeConfig);
  assert.equal(lstatSync(output).ino, beforeOutput.ino);
  assert.ok(lstatSync(output).size >= beforeOutput.size);
  console.log(JSON.stringify({ at: new Date().toISOString(), oldPid, newPid, binaryHash: expected, configUnchanged: true, outputPreserved: true, outputBytes: lstatSync(output).size }));
} catch (error) {
  if (bootedOut) {
    try { run('/bin/launchctl', ['bootout', service]); } catch {}
    writeFileSync(temporary, original, { mode: st.mode & 0o777 });
    renameSync(temporary, plist);
    run('/bin/launchctl', ['bootstrap', 'gui/501', plist]);
  }
  throw error;
} finally {
  try { unlinkSync(temporary); } catch (error) { if (error.code !== 'ENOENT') throw error; }
}
