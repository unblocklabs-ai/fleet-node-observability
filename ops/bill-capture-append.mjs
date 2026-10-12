// Bill-only correction of the pre-existing file exporter's truncate-on-start default.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, renameSync, lstatSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';
process.umask(0o077);
assert.equal(process.getuid(), 501);
const path = '/Users/billjohansson/.openclaw/codex-otel-capture/collector.json';
const output = '/Users/billjohansson/.openclaw/codex-otel-capture/otel-logs.json';
const binary = '/Users/billjohansson/.local/share/fleet-node/bin/otelcol-contrib';
const plist = '/Users/billjohansson/Library/LaunchAgents/com.billjohansson.openclaw-codex-otel-capture.plist';
const service = 'gui/501/com.billjohansson.openclaw-codex-otel-capture';
const run = (file, args) => execFileSync(file, args, { encoding: 'utf8', timeout: 15000 });
const config = JSON.parse(readFileSync(path, 'utf8'));
assert.equal(config.exporters['file/codex'].path, output);
assert.equal(config.exporters['file/codex'].rotation, undefined);
config.exporters['file/codex'].append = true;
const temporary = path + '.append-' + process.pid;
writeFileSync(temporary, JSON.stringify(config, null, 2) + '\n', {flag: 'wx', mode: 0o600});
run(binary, ['validate', '--config=file:' + temporary]);
const oldPid = Number(run('/bin/launchctl', ['print', service]).match(/\n\s*pid = (\d+)/)?.[1]);
assert.ok(oldPid > 0);
run('/bin/launchctl', ['bootout', service]);
for (let i = 0; i < 40; i++) {
  let alive = true;
  try { process.kill(oldPid, 0); } catch (error) { if (error.code === 'ESRCH') alive = false; else throw error; }
  if (!alive) break;
  assert.ok(i < 39, 'Capture did not stop');
  await delay(250);
}
const before = lstatSync(output);
renameSync(temporary, path);
run('/bin/launchctl', ['bootstrap', 'gui/501', plist]);
for (let i = 0; i < 120; i++) {
  const status = run('/bin/launchctl', ['print', service]);
  const pid = Number(status.match(/\n\s*pid = (\d+)/)?.[1]);
  if (pid && status.includes('state = running')) {
    try {
      assert.ok(run('/usr/sbin/lsof', ['-nP', '-a', '-p', String(pid), '-iTCP:4319', '-sTCP:LISTEN']).includes('127.0.0.1:4319'));
      assert.equal(lstatSync(output).ino, before.ino);
      assert.ok(lstatSync(output).size >= before.size);
      console.log(JSON.stringify({ at: new Date().toISOString(), append: true, pid, outputBytes: lstatSync(output).size }));
      break;
    } catch (error) { if (i === 119) throw error; }
  }
  assert.ok(i < 119, 'Capture did not restart');
  await delay(250);
}
