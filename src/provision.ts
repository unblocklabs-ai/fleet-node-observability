import { access, lstat, realpath } from 'node:fs/promises';
import { constants } from 'node:fs';
import { basename, dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { absolutePath, assertNode24, object, parseConfig, parseSettings, runtimeEnv, type LocalSettings, type RuntimeContext } from './config.js';
import { ensurePrivateDir, isErrno, readRegular, writeAtomic, writeExclusive } from './io.js';
import { runCommand } from './process.js';

export const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export async function packageVersion(): Promise<string> {
  const p = object(JSON.parse(await readRegular(join(packageRoot, 'package.json'), 64 * 1024)) as unknown);
  if (typeof p.version !== 'string') throw new Error('Package metadata is invalid');
  return p.version;
}
export async function validateRuntime(settings: LocalSettings): Promise<void> {
  await access(settings.nodePath, constants.X_OK);
  await access(settings.npmCliPath, constants.R_OK);
  const result = await runCommand(settings.nodePath, ['--version'], { timeoutMs: 5000, env: runtimeEnv(settings) });
  assertNode24(result.stdout.trim().replace(/^v/, ''));
  const npm = await runCommand(settings.nodePath, [settings.npmCliPath, '--version'], { timeoutMs: 10000, env: runtimeEnv(settings) });
  if (!/^\d+\.\d+\.\d+/.test(npm.stdout)) throw new Error('Configured npm CLI is invalid');
}

async function canonicalPotentialPath(path: string): Promise<string> {
  try { return await realpath(path); }
  catch (error) {
    if (!isErrno(error, 'ENOENT')) throw error;
    const parent = dirname(path);
    if (parent === path) throw error;
    return join(await canonicalPotentialPath(parent), path.slice(parent.length));
  }
}
function overlaps(a: string, b: string): boolean { return a === b || a.startsWith(b + sep) || b.startsWith(a + sep); }
async function pathLocations(path: string): Promise<string[]> {
  // Keep the symlink's pathname as well as its target: deleting either can break it.
  return [resolve(path), await canonicalPotentialPath(path), join(await canonicalPotentialPath(dirname(path)), basename(path))];
}
async function openClawInstallation(executable: string): Promise<string | null> {
  let binary: string;
  try { binary = await realpath(executable); }
  catch (error) { if (isErrno(error, 'ENOENT')) return null; throw error; }
  for (let directory = dirname(binary); ; directory = dirname(directory)) {
    let content: string | undefined;
    try { content = await readRegular(join(directory, 'package.json'), 1024 * 1024); }
    catch (error) { if (!isErrno(error, 'ENOENT')) throw error; }
    if (content !== undefined) {
      const metadata = object(JSON.parse(content) as unknown);
      // Stop at the nearest package boundary rather than guessing from a bin folder.
      return metadata.name === 'openclaw' ? directory : null;
    }
    if (dirname(directory) === directory) return null;
  }
}
export async function validateLayout(root: string, settings: LocalSettings): Promise<void> {
  const [state, prefix, openclaw, home, pkg] = await Promise.all([root, settings.npmPrefix, settings.openclawRoot, settings.nodeHome, packageRoot].map(canonicalPotentialPath));
  if (!state || !prefix || !openclaw || !home || !pkg || state === '/' || prefix === '/' || state === home || prefix === home || overlaps(state, prefix) || overlaps(state, openclaw) || overlaps(prefix, openclaw) || overlaps(state, pkg)) throw new Error('State, npm prefix, and OpenClaw must be distinct dedicated directories');
  const [nodePaths, npmPaths, prefixPaths, packagePaths, statePaths, dataPaths, installation] = await Promise.all([
    pathLocations(settings.nodePath), pathLocations(settings.npmCliPath), pathLocations(settings.npmPrefix),
    pathLocations(packageRoot), pathLocations(root), pathLocations(settings.openclawRoot), openClawInstallation(settings.openclawBin),
  ]);
  const inside = (path: string, directory: string): boolean => path === directory || path.startsWith(directory + sep);
  if (statePaths.some(path => [...prefixPaths, ...packagePaths].some(directory => overlaps(path, directory)))) {
    throw new Error('State must be outside the fleet npm prefix and package, including symlink paths');
  }
  if ([...nodePaths, ...npmPaths].some(path => [...prefixPaths, ...packagePaths].some(directory => inside(path, directory)))) {
    throw new Error('Node and npm runtime paths must be outside the fleet npm prefix and package');
  }
  const openClawPaths = [...dataPaths, ...(installation ? [installation] : [])];
  if ([...statePaths, ...prefixPaths].some(path => openClawPaths.some(directory => overlaps(path, directory)))
    || [...nodePaths, ...npmPaths].some(path => openClawPaths.some(directory => inside(path, directory)))) {
    throw new Error('State, npm prefix, Node and npm runtime paths must be outside OpenClaw data and installation');
  }
  if (nodePaths.includes(await canonicalPotentialPath(settings.openclawBin))) throw new Error('Node runtime must be independent of OpenClaw');
}

export interface InitOptions {
  root: string;
  configPath: string;
  settings: LocalSettings;
  tokenFile?: string;
  authorizationFile?: string;
}
export async function initialize(options: InitOptions): Promise<RuntimeContext> {
  if (process.getuid?.() === 0) throw new Error('Run initialization as the ordinary node account, not root');
  const root = absolutePath(options.root);
  const settings = parseSettings(options.settings);
  const config = parseConfig(JSON.parse(await readRegular(absolutePath(options.configPath), 64 * 1024)) as unknown);
  await validateLayout(root, settings);
  await validateRuntime(settings);
  for (const name of ['config/node.json', 'config/local.json', 'secrets/authorization-header']) {
    try { await lstat(join(root, name)); }
    catch (e) { if (isErrno(e, 'ENOENT')) continue; throw e; }
    throw new Error('Runtime already initialized; refusing to overwrite state or credentials');
  }
  if (Boolean(options.tokenFile) === Boolean(options.authorizationFile)) throw new Error('Provide exactly one protected token file or authorization file');
  let header: string;
  if (options.tokenFile) {
    const token = (await readRegular(absolutePath(options.tokenFile), 16 * 1024, true)).replace(/\n$/, '');
    if (!token || /[\r\n]/.test(token) || token !== token.trim()) throw new Error('Invalid ingest token file');
    header = `Basic ${Buffer.from(`${config.node_label}:${token}`).toString('base64')}`;
  } else {
    header = (await readRegular(absolutePath(options.authorizationFile!), 24 * 1024, true)).replace(/\n$/, '');
    if (!/^Basic [A-Za-z0-9+/]+=*$/.test(header)) throw new Error('Invalid authorization file');
    const value = Buffer.from(header.slice(6), 'base64').toString('utf8');
    if (!value.startsWith(`${config.node_label}:`) || !value.slice(config.node_label.length + 1) || /[\r\n]/.test(value)) throw new Error('Authorization identity does not match node configuration');
  }
  await ensurePrivateDir(root);
  for (const directory of ['config', 'secrets', 'state', 'state/sessions', 'queue', 'logs', 'textfiles', 'bin', 'services']) await ensurePrivateDir(join(root, directory));
  await writeExclusive(join(root, 'secrets/authorization-header'), header);
  await writeExclusive(join(root, 'config/node.json'), JSON.stringify(config, null, 2) + '\n');
  await writeExclusive(join(root, 'config/local.json'), JSON.stringify(settings, null, 2) + '\n');
  return { root, settings, config };
}

export async function configureOpenClaw(context: RuntimeContext): Promise<string | null> {
  const path = join(context.settings.openclawRoot, 'openclaw.json');
  const before = await readRegular(path, 1024 * 1024);
  let parsed: unknown;
  try { parsed = JSON.parse(before); } catch { throw new Error('OpenClaw configuration is not valid JSON'); }
  const payload = object(parsed);
  const diagnostics = payload.diagnostics === undefined || payload.diagnostics === null ? {} : object(payload.diagnostics);
  const otel = diagnostics.otel === undefined || diagnostics.otel === null ? {} : object(diagnostics.otel);
  for (const key of ['tracesEndpoint', 'metricsEndpoint', 'logsEndpoint']) delete otel[key];
  Object.assign(otel, { enabled: true, endpoint: 'http://127.0.0.1:4318', protocol: 'http/protobuf', serviceName: 'openclaw_gateway', traces: true, metrics: true, logs: true, logsExporter: 'otlp', headers: {} });
  if (otel.captureContent === undefined) otel.captureContent = false;
  diagnostics.enabled = true; diagnostics.otel = otel; payload.diagnostics = diagnostics;
  const after = JSON.stringify(payload, null, 2) + '\n';
  if (JSON.stringify(JSON.parse(before)) === JSON.stringify(payload)) return null;
  if (await readRegular(path, 1024 * 1024) !== before) throw new Error('OpenClaw configuration changed; refusing to overwrite it');
  const backup = `${path}.bak-fleet-node-${Date.now()}-${process.pid}`;
  await writeExclusive(backup, before);
  if (await readRegular(path, 1024 * 1024) !== before) throw new Error('OpenClaw configuration changed; backup retained');
  await writeAtomic(path, after);
  return backup;
}
