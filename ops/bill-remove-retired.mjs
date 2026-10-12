// Exact, reviewed Bill-only allowlist. Recovery archive is on Bek's Mac, not Bill.
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, lstatSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';

process.umask(0o077);
assert.equal(process.getuid(), 501);
assert.equal(process.platform, 'darwin');
assert.ok(process.argv.length === 2 || process.argv[2] === '--delete');
const root = '/Users/billjohansson/.local/share/fleet-node';
const manifest = readFileSync(root + '/bill-cleanup-targets.json');
assert.equal(createHash('sha256').update(manifest).digest('hex'), '2fd47895ad89d41b2b8358c9ffe85b807298be01644d1d2753d813b103fe4489');
const targets = JSON.parse(manifest);
const all = [...targets.userDirectories, ...targets.userFiles, ...targets.privilegedDirectories];
const run = (file, args) => execFileSync(file, args, { encoding: 'utf8', timeout: 15000, maxBuffer: 16 * 1024 * 1024 });
const validateTarget = (path, directory, uid) => {
  const stat = lstatSync(path);
  assert.equal(stat.isSymbolicLink(), false, path);
  assert.equal(stat.isDirectory(), directory, path);
  assert.equal(stat.uid, uid, path);
  assert.equal(realpathSync(path), path, path);
};
for (const path of targets.userDirectories) validateTarget(path, true, 501);
for (const path of targets.userFiles) validateTarget(path, false, 501);
for (const path of targets.privilegedDirectories) validateTarget(path, true, 0);
const oldLabels = ['fleet-node-agent', 'node-exporter', 'fleet-node-agent-heartbeat', 'openclaw-gateway-health-textfile', 'macos-thermal-textfile', 'openclaw-cron-schedule-textfile', 'codex-usage-textfile'];
const disabled = run('/bin/launchctl', ['print-disabled', 'system']);
for (const suffix of oldLabels) {
  const label = 'com.unblocklabs.' + suffix;
  assert.notEqual(spawnSync('/bin/launchctl', ['print', 'system/' + label]).status, 0, label);
  assert.ok(disabled.includes('"' + label + '" => disabled'), label);
  assert.equal(existsSync('/Library/LaunchDaemons/' + label + '.plist'), false);
}
assert.notEqual(spawnSync('/bin/launchctl', ['print', 'gui/501/com.unblocklabs.macos-thermal-textfile']).status, 0);
assert.ok(run('/bin/launchctl', ['print-disabled', 'gui/501']).includes('"com.unblocklabs.macos-thermal-textfile" => disabled'));
const processes = run('/bin/ps', ['-axo', 'command=']);
for (const path of all) assert.equal(processes.includes(path), false, 'Active process references ' + path);
for (const directory of ['/Library/LaunchDaemons', '/Library/LaunchAgents', '/Users/billjohansson/Library/LaunchAgents']) {
  for (const name of readdirSync(directory).filter(name => name.endsWith('.plist'))) {
    const content = JSON.stringify(JSON.parse(run('/usr/bin/plutil', ['-convert', 'json', '-o', '-', directory + '/' + name])));
    for (const path of all) assert.equal(content.includes(path), false, 'Active plist references ' + path);
  }
}
const cli = '/Users/billjohansson/.local/fleet-node-npm/lib/node_modules/@unblocklabs/fleet-node/dist/cli.js';
const status = JSON.parse(run(process.execPath, [cli, 'status', '--root', root]));
assert.equal(status.version, '0.1.0');
assert.equal(status.enabled, true);
assert.ok(status.jobs.every(job => job.loaded));
const capture = JSON.parse(readFileSync('/Users/billjohansson/.openclaw/codex-otel-capture/collector.json', 'utf8'));
assert.equal(capture.exporters['file/codex'].append, true);
assert.ok(run('/bin/launchctl', ['print', 'gui/501/com.billjohansson.openclaw-codex-otel-capture']).includes('program = ' + root + '/bin/otelcol-contrib'));

// Remove local-file aliases and only the unpublished test-version cache blob.
// The official registry entry shares the 0.1.0 content and must remain usable.
const cacache = createRequire('/opt/homebrew/opt/node@24/lib/node_modules/npm/bin/npm-cli.js')('cacache');
const cache = '/Users/billjohansson/.npm/_cacache';
const entries = await cacache.ls(cache);
const official = 'sha512-khYoSArLGVFwPHdB2kzyI8rIB60xt/esMV1KZu/3MgyiD/M1RmFzdtLSAVVu11vJJ0O6A5xWzTLK50N6WFFcdw==';
const canary = 'sha512-kqgjFJpcPxDMVpItgYHa4MOJk+TP7sAoO7hrpJF/fnOVNq830BR3xHEWjSP5J4en91CtwSjJkPmDzEjUPGxR5g==';
const cacheKeys = [];
for (const location of ['../../../fleet-node-ts-canary-20260927/', '/Users/billjohansson/fleet-node-ts-canary-20260927/']) {
  for (const version of ['0.1.0', '0.1.1-canary-verification']) {
    const key = 'pacote:tarball:file:' + location + 'unblocklabs-fleet-node-' + version + '.tgz';
    assert.equal(entries[key]?.integrity, version === '0.1.0' ? official : canary);
    cacheKeys.push(key);
  }
}
assert.equal(createHash('sha256').update((await cacache.get.byDigest(cache, canary))).digest('hex'), '455a74a4b9ca2ff7bb8c09c58e111063a68873dee18086357a6972dbdc642f47');
assert.ok(Object.values(entries).filter(entry => entry.integrity === canary).every(entry => cacheKeys.includes(entry.key)));
const report = { at: new Date().toISOString(), archive: '/Users/bek/fleet-node-bill-recovery-20260928.zPBa3G/retired-fleet-files.tar.gz', archiveSha256: 'f22ab8b0d966e685f9f3bbe5d3eb95e8cf9d6e1702d6c347ed5764d01a82e256', verifiedUserTargets: 17, removed: [], removedCacheKeys: [], remainingPrivilegedTargets: targets.privilegedDirectories };
console.log(JSON.stringify({ ...report, dryRun: process.argv[2] !== '--delete' }));
if (process.argv[2] === '--delete') {
  const receipt = root + '/state/retired-cleanup-20260928.json';
  assert.equal(existsSync(receipt), false, 'Cleanup already ran; inspect its receipt');
  const save = () => writeFileSync(receipt, JSON.stringify(report, null, 2) + '\n', { mode: 0o600 });
  save();
  for (const key of cacheKeys) { await cacache.rm.entry(cache, key); report.removedCacheKeys.push(key); save(); }
  await cacache.rm.content(cache, canary);
  assert.ok(await cacache.get.hasContent(cache, official));
  for (const path of [...targets.userDirectories, ...targets.userFiles]) {
    validateTarget(path, targets.userDirectories.includes(path), 501);
    rmSync(path, { recursive: targets.userDirectories.includes(path), force: false });
    assert.equal(existsSync(path), false);
    report.removed.push(path);
    save();
  }
  console.log(JSON.stringify(report));
}
