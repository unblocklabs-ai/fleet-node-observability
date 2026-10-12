import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile, access, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import test from 'node:test';

const execute = promisify(execFile);
const source = await readFile(new URL('../ops/bill-cutover.sh', import.meta.url), 'utf8');
const labels = ['fleet-node-agent', 'node-exporter', 'fleet-node-agent-heartbeat', 'openclaw-gateway-health-textfile', 'macos-thermal-textfile', 'openclaw-cron-schedule-textfile', 'codex-usage-textfile'];
const exists = async path => access(path).then(() => true, () => false);

async function fixture(t, parked = true) {
  const base = await mkdtemp(join(tmpdir(), 'fleet-cutover-'));
  t.after(() => rm(base, { recursive: true, force: true }));
  const home = join(base, 'bill');
  const rollout = join(home, 'fleet-node-ts-canary-20260927');
  const state = join(home, '.local/share/fleet-node');
  const system = join(base, 'LaunchDaemons');
  const gui = join(home, 'Library/LaunchAgents');
  for (const path of [join(rollout, 'rollback'), join(state, 'services'), system, gui]) await mkdir(path, { recursive: true });
  const pairs = labels.map(label => [`${system}/com.unblocklabs.${label}.plist`, `${rollout}/rollback/com.unblocklabs.${label}.plist`]);
  pairs.push([`${gui}/com.unblocklabs.macos-thermal-textfile.plist`, `${rollout}/rollback/gui-macos-thermal-textfile.plist`]);
  for (const [original, saved] of pairs) await writeFile(parked ? saved : original, 'preserved plist');
  await writeFile(join(state, 'services/install-system.sh'), '#!/bin/sh\nexit 0\n');
  const events = join(base, 'events');
  const commands = {
    '/usr/bin/id': '[ "$#" -eq 1 ] && echo 0 || echo 501',
    '/usr/bin/uname': 'echo Darwin',
    '/usr/bin/sudo': `printf 'sudo %s\\n' "$*" >> '${events}'; exit 0`,
    '/bin/launchctl': `printf 'launchctl %s\\n' "$*" >> '${events}'; if [ "$1" = print-disabled ]; then printf '%s\\n' ${labels.map(label => `'"com.unblocklabs.${label}" => disabled'`).join(' ')}; fi; [ "$1" != print ]`,
    '/usr/sbin/lsof': 'exit 1',
    // Simulate the unrelated live process using the real production selector.
    '/usr/bin/pgrep': `printf '%s\\n' '${home}/.openclaw/fleet-node-observability/bin/otelcol-contrib --config=file:${home}/.openclaw/codex-otel-capture/collector.json' | /usr/bin/grep -E -- "$4"`,
    '/usr/sbin/chown': 'exit 0',
  };
  let script = source.replaceAll('/Users/billjohansson', home).replaceAll('/Library/LaunchDaemons', system);
  for (const [command, body] of Object.entries(commands)) {
    const stub = join(base, command.split('/').at(-1));
    await writeFile(stub, `#!/bin/sh\n${body}\n`, { mode: 0o700 });
    script = script.replaceAll(command, stub);
  }
  const path = join(base, 'cutover.sh');
  await writeFile(path, script);
  return { run: () => execute('/bin/sh', [path], { timeout: 5000 }), pairs, rollout, state, events };
}

test('Bill cutover resumes parked plists without touching the separate capture collector', async t => {
  const f = await fixture(t);
  await f.run();
  for (const [original, saved] of f.pairs) {
    assert.equal(await exists(original), false);
    assert.equal(await readFile(saved, 'utf8'), 'preserved plist');
  }
  const events = await readFile(f.events, 'utf8');
  for (const label of labels) assert.ok(events.includes(`launchctl disable system/com.unblocklabs.${label}`));
  assert.ok(events.includes('import-bill-state.mjs'));
  assert.equal(await exists(join(f.rollout, 'cutover-complete')), true);
});

test('first cutover still parks every old plist before state import', async t => {
  const f = await fixture(t, false);
  await f.run();
  for (const [original, saved] of f.pairs) {
    assert.equal(await exists(original), false);
    assert.equal(await readFile(saved, 'utf8'), 'preserved plist');
  }
});

test('ambiguous recovery files or completed import fail before any service mutation', async t => {
  for (const mode of ['both', 'neither', 'symlink', 'already-imported', 'gate-symlink', 'receipt-symlink']) {
    const f = await fixture(t);
    const [original, saved] = f.pairs.at(-1);
    if (mode === 'both') await writeFile(original, 'unexpected original');
    if (mode === 'neither') await rm(saved);
    if (mode === 'symlink') await symlink(join(f.rollout, 'missing'), original);
    if (mode === 'already-imported') await writeFile(join(f.rollout, 'cutover-state-preserved.json'), '{}');
    if (mode === 'gate-symlink') await symlink(join(f.rollout, 'missing'), join(f.state, 'services/enabled'));
    if (mode === 'receipt-symlink') await symlink(join(f.rollout, 'missing'), join(f.rollout, 'cutover-state-preserved.json'));
    await assert.rejects(f.run());
    assert.equal(await exists(f.events), false, mode);
    assert.equal(await exists(join(f.rollout, 'cutover-complete')), false, mode);
  }
});

test('quiescence selector still detects each legacy fleet process', async () => {
  const pattern = source.match(/^old_process_pattern='([^']+)'$/m)[1];
  const root = '/Users/billjohansson/.openclaw/fleet-node-observability';
  const commands = [
    `${root}/bin/otelcol-contrib --config=file:${root}/config/collector.json`,
    `/bin/sh ${root}/bin/fleet-node-agent-heartbeat`,
    `/bin/sh ${root}/bin/openclaw-gateway-health`,
    `/opt/homebrew/bin/python3 ${root}/python/fleet_node_observability/commands/collect_codex_usage.py`,
    '/opt/homebrew/bin/node_exporter --collector.textfile.directory=/opt/homebrew/var/lib/node_exporter/textfile_collector',
    '/usr/bin/python3 /Users/billjohansson/bin/collect-macos-thermal.py',
  ];
  for (const command of commands) {
    await execute('/bin/sh', ['-c', 'printf "%s\\n" "$1" | /usr/bin/grep -E -- "$2"', 'selector', command, pattern]);
  }
});
