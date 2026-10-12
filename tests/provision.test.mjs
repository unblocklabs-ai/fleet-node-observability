import assert from 'node:assert/strict';
import { copyFile, mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { packageRoot, validateLayout, validateRuntime } from '../dist/provision.js';

async function fixture(t) {
  const base = await mkdtemp(join(tmpdir(), 'fleet-provision-test-'));
  t.after(() => rm(base, { recursive: true, force: true }));
  const settings = { nodePath: process.execPath, nodeHome: base, openclawRoot: join(base, 'openclaw-data'),
    openclawBin: join(base, 'tools/missing-openclaw'), codexBin: join(base, 'tools/missing-codex'),
    npmCliPath: resolve(dirname(process.execPath), '../lib/node_modules/npm/bin/npm-cli.js'), npmPrefix: join(base, 'fleet-npm') };
  return { base, settings, root: join(base, 'fleet-state') };
}

test('independent explicit runtime and missing OpenClaw remain supported', async t => {
  const f = await fixture(t);
  await validateLayout(f.root, f.settings);
  await validateRuntime(f.settings);
});

test('runtime links located inside the npm prefix are rejected even when targets are outside it', async t => {
  const f = await fixture(t);
  const managed = join(f.settings.npmPrefix, 'lib/node_modules/@unblocklabs/fleet-node/tools');
  await mkdir(managed, { recursive: true });
  for (const [key, source] of [['nodePath', f.settings.nodePath], ['npmCliPath', f.settings.npmCliPath]]) {
    const link = join(managed, key);
    await symlink(source, link);
    const settings = { ...f.settings, [key]: link };
    // These executables genuinely work before npm replaces their containing package.
    await validateRuntime(settings);
    await assert.rejects(validateLayout(f.root, settings), /outside the fleet npm prefix/);
  }
});

test('external runtime symlink targeting npm contents and aliases to package parents are rejected', async t => {
  const f = await fixture(t);
  const managed = join(f.settings.npmPrefix, 'lib/node_modules/@unblocklabs/fleet-node/tools');
  await mkdir(managed, { recursive: true });
  const owned = join(managed, 'npm-cli.js');
  await copyFile(f.settings.npmCliPath, owned);
  const outside = join(f.base, 'outside-npm-link');
  await symlink(owned, outside);
  await assert.rejects(validateLayout(f.root, { ...f.settings, npmCliPath: outside }), /outside the fleet npm prefix/);
  await symlink(f.settings.nodePath, join(managed, 'node'));
  const directoryAlias = join(f.base, 'alias-to-managed-tools');
  await symlink(managed, directoryAlias);
  await assert.rejects(validateLayout(f.root, { ...f.settings, nodePath: join(directoryAlias, 'node') }), /outside the fleet npm prefix/);
  await assert.rejects(validateLayout(f.root, { ...f.settings, nodePath: join(packageRoot, 'future-runtime/node') }), /outside the fleet npm prefix/);
  const externalStateParent = join(f.base, 'external-state-parent');
  await mkdir(externalStateParent);
  const stateAlias = join(managed, 'state-alias');
  await symlink(externalStateParent, stateAlias);
  await assert.rejects(validateLayout(join(stateAlias, 'state'), f.settings), /State must be outside the fleet npm prefix/);
});

test('real OpenClaw npm package boundary excludes state, prefix, and runtime but not shared external tools', async t => {
  const f = await fixture(t);
  const installation = join(f.base, 'openclaw-npm/lib/node_modules/openclaw');
  await mkdir(join(installation, 'dist'), { recursive: true });
  // Bill's real OpenClaw exports map makes package.json larger than 128 KiB.
  await writeFile(join(installation, 'package.json'), JSON.stringify({ name: 'openclaw', version: 'fixture', exports: { './large-fixture': 'x'.repeat(140_000) } }));
  await writeFile(join(installation, 'dist/openclaw.mjs'), '// inert CLI fixture');
  await mkdir(join(f.base, 'tools'));
  const executable = join(f.base, 'tools/openclaw');
  await symlink(join(installation, 'dist/openclaw.mjs'), executable);
  const settings = { ...f.settings, openclawBin: executable };
  await validateLayout(f.root, settings);
  await assert.rejects(validateLayout(join(installation, 'fleet-state'), settings), /outside OpenClaw data and installation/);
  await assert.rejects(validateLayout(f.root, { ...settings, npmPrefix: join(installation, 'fleet-npm') }), /outside OpenClaw data and installation/);
  for (const [key, target] of [['nodePath', f.settings.nodePath], ['npmCliPath', f.settings.npmCliPath]]) {
    const link = join(installation, key);
    await symlink(target, link);
    await assert.rejects(validateLayout(f.root, { ...settings, [key]: link }), /outside OpenClaw data and installation/);
  }
  const installedAlias = join(f.base, 'openclaw-install-alias');
  await symlink(installation, installedAlias);
  await assert.rejects(validateLayout(join(installedAlias, 'state'), settings), /outside OpenClaw data and installation/);
});

test('OpenClaw data directory cannot own the npm CLI or a lexical runtime symlink', async t => {
  const f = await fixture(t);
  await mkdir(f.settings.openclawRoot);
  const nodeLink = join(f.settings.openclawRoot, 'node');
  await symlink(f.settings.nodePath, nodeLink);
  await assert.rejects(validateLayout(f.root, { ...f.settings, nodePath: nodeLink }), /outside OpenClaw data and installation/);
  await assert.rejects(validateLayout(f.root, { ...f.settings, npmCliPath: join(f.settings.openclawRoot, 'npm-cli.js') }), /outside OpenClaw data and installation/);
});
