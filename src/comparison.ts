import { DatabaseSync } from 'node:sqlite';
import { dirname, join, isAbsolute, basename } from 'node:path';
import { stat, realpath } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import type { RuntimeContext } from './config.js';
import { object, runtimeEnv } from './config.js';
import { isErrno, labels, metric, readRegular, writeAtomic } from './io.js';
import { runCommand } from './process.js';

type Status = 'ok' | 'missing' | 'unreadable' | 'invalid' | 'changed';
type Permission = 'Granted' | 'Denied' | 'No matching record' | 'Unreadable' | 'Other recorded value' | 'Conflicting records' | 'Not observed' | 'Changed during observation';
interface Gateway { pid: number; birth: string; executable: string; script: string | null; packageRoot: string | null; identity: string }
const services = { fda: 'kTCCServiceSystemPolicyAllFiles', accessibility: 'kTCCServiceAccessibility', screen_recording: 'kTCCServiceScreenCapture' } as const;
type Permissions = Record<keyof typeof services, Permission>;
const unknownPermissions = (value: Permission = 'Unreadable'): Permissions => ({ fda: value, accessibility: value, screen_recording: value });
const version = (value: unknown): string => {
  if (typeof value !== 'string' || !/^v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/.test(value) || value.length > 80) throw new Error('Invalid version');
  return value;
};

/** These three macOS services are system TCC records. Never infer a bundle grant,
 * another Node path, or a live capability from a path record. Read-only/WAL-aware. */
export function readPermissions(database: string, executable: string): { permissions: Permissions; status: Status } {
  let db: DatabaseSync;
  try { db = new DatabaseSync(database, { readOnly: true, timeout: 1000 }); }
  catch { return { permissions: unknownPermissions(), status: 'unreadable' }; }
  try {
    const query = db.prepare('SELECT auth_value FROM access WHERE service = ? AND client = ? AND client_type = 1');
    const permissions = unknownPermissions();
    for (const key of Object.keys(services) as (keyof typeof services)[]) {
      const rows = query.all(services[key], executable);
      const values = new Set(rows.map(row => row.auth_value));
      permissions[key] = rows.length === 0 ? 'No matching record' : values.size !== 1 ? 'Conflicting records'
        : values.has(2) ? 'Granted' : values.has(0) ? 'Denied' : 'Other recorded value';
    }
    return { permissions, status: 'ok' };
  } catch { return { permissions: unknownPermissions(), status: 'unreadable' }; }
  finally { db.close(); }
}

/** Export stored values only. Missing keys and explicit null are not equivalent. */
export async function readStoredConfiguration(path: string): Promise<{ tools_profile: string; tools_exec_mode: string; status: Status }> {
  let payload: Record<string, unknown>;
  try { payload = object(JSON.parse(await readRegular(path, 1024 * 1024)) as unknown); }
  catch (error) {
    if (isErrno(error, 'ENOENT')) return { tools_profile: 'unset', tools_exec_mode: 'unset', status: 'missing' };
    return { tools_profile: 'Unreadable', tools_exec_mode: 'Unreadable', status: 'unreadable' };
  }
  try {
    const tools = Object.hasOwn(payload, 'tools') ? object(payload.tools) : {};
    let invalid = false;
    const stored = (p: Record<string, unknown>, key: string): string => {
      if (!Object.hasOwn(p, key)) return 'unset';
      const value = p[key];
      // Bounded low-cardinality configuration tokens, never stringify objects/secrets.
      if (typeof value !== 'string' || !/^[a-z][a-z0-9_-]{0,31}$/.test(value)) { invalid = true; return 'Invalid stored value'; }
      return value;
    };
    const tools_profile = stored(tools, 'profile');
    let tools_exec_mode: string;
    try { tools_exec_mode = stored(Object.hasOwn(tools, 'exec') ? object(tools.exec) : {}, 'mode'); }
    catch { tools_exec_mode = 'Invalid stored value'; invalid = true; }
    return { tools_profile, tools_exec_mode, status: invalid ? 'invalid' : 'ok' };
  } catch { return { tools_profile: 'Invalid stored value', tools_exec_mode: 'Invalid stored value', status: 'invalid' }; }
}

async function discoverGateway(ctx: RuntimeContext): Promise<Gateway> {
  const command = async (bin: string, args: string[]) => (await runCommand(bin, args, {
    timeoutMs: 5000, maxBytes: 1024 * 1024, env: runtimeEnv(ctx.settings),
  })).stdout;
  // Like the existing readiness contract, the managed Gateway serves loopback :18789.
  const listeners = await command('/usr/sbin/lsof', ['-nP', '-iTCP:18789', '-sTCP:LISTEN', '-Fp']);
  const pids = [...new Set([...listeners.matchAll(/^p(\d+)$/gm)].map(m => Number(m[1])))];
  if (pids.length !== 1 || !pids[0]) throw new Error('No unique Gateway listener');
  const pid = pids[0];
  const birth = (await command('/bin/ps', ['-p', String(pid), '-o', 'lstart='])).trim();
  const comm = (await command('/bin/ps', ['-p', String(pid), '-o', 'comm='])).trim();
  const argv = (await command('/bin/ps', ['-ww', '-p', String(pid), '-o', 'command='])).trim();
  if (!birth) throw new Error('Unreadable Gateway identity');
  // process.title can replace argv. That must not hide a verified Node mapping.
  const script = argv.startsWith(comm + ' ')
    ? /^(\/[^\r\n]+?\.(?:m?js))\s+gateway(?:\s|$)/.exec(argv.slice(comm.length + 1))?.[1] : undefined;
  const mappings = await command('/usr/sbin/lsof', ['-a', '-p', String(pid), '-d', 'txt', '-Fin']);
  // First txt mapping is the process executable, not a PATH lookup or LaunchAgent intent.
  const executable = /^n(.+)$/m.exec(mappings)?.[1];
  if (!executable || !isAbsolute(executable) || basename(executable) !== 'node') throw new Error('Unreadable Node executable mapping');
  const info = await stat(executable);
  const mappedInode = /^i(\d+)$/m.exec(mappings)?.[1];
  if (!mappedInode || Number(mappedInode) !== info.ino) throw new Error('Executable replaced since process launch');
  let servingScript: string | null = null;
  let packageRoot: string | null = null;
  if (script && isAbsolute(script)) {
    try { servingScript = await realpath(script); packageRoot = await servingRoot(servingScript); }
    catch { /* The executable and its permissions remain independently observable. */ }
  } else {
    // A mapped OpenClaw-owned native module ties this root to the actual process,
    // unlike PATH or LaunchAgent intent. Ambiguous roots fail closed.
    const roots = [...new Set([...mappings.matchAll(/^n(\/[^\r\n]+\/node_modules\/openclaw)\/node_modules\/@openclaw\/fs-safe-[^/\r\n]+\/fs-safe-native\.node$/gm)].map(m => m[1]))];
    if (roots.length === 1 && roots[0]) {
      try { packageRoot = await realpath(roots[0]); } catch { /* Package section only. */ }
    }
  }
  return { pid, birth, executable, script: servingScript, packageRoot, identity: JSON.stringify([pid, birth, executable, info.dev, info.ino, info.size, info.mtimeMs, servingScript, packageRoot]) };
}

async function servingRoot(script: string): Promise<string> {
  let root = dirname(script);
  for (let depth = 0; depth < 5; depth++) {
    let payload: Record<string, unknown>;
    try { payload = object(JSON.parse(await readRegular(join(root, 'package.json'), 1024 * 1024)) as unknown); }
    catch (error) { if (!isErrno(error, 'ENOENT')) throw error; root = dirname(root); continue; }
    if (payload.name !== 'openclaw') throw new Error('Serving root is not OpenClaw');
    return root;
  }
  throw new Error('No serving package');
}

export async function collectComparison(ctx: RuntimeContext): Promise<number> {
  let gateway: Gateway | undefined;
  let gatewayStatus: Status = 'missing';
  let packageStatus: Status = 'missing';
  let permissionsStatus: Status = 'missing';
  let openclawVersion = 'Not observed';
  let nodeVersion = 'Not observed';
  let permissions = unknownPermissions('Not observed');
  const configuration = await readStoredConfiguration(join(ctx.settings.openclawRoot, 'openclaw.json'));
  try { gateway = await discoverGateway(ctx); gatewayStatus = 'ok'; }
  catch { /* No cached executable or permissions when the Gateway disappears. */ }
  if (gateway) {
    try {
      const result = await runCommand(gateway.executable, ['--version'], { timeoutMs: 5000, maxBytes: 1024, env: runtimeEnv(ctx.settings) });
      nodeVersion = version(result.stdout.trim());
    } catch { gatewayStatus = 'unreadable'; }
    try {
      if (!gateway.packageRoot) throw new Error('Serving root not observable');
      const payload = object(JSON.parse(await readRegular(join(gateway.packageRoot, 'package.json'), 1024 * 1024)) as unknown);
      if (payload.name !== 'openclaw') throw new Error('Serving root is not OpenClaw');
      openclawVersion = version(payload.version); packageStatus = 'ok';
    }
    catch { packageStatus = 'unreadable'; }
    // A killable child bounds SQLite work; no Python runtime or external sqlite CLI.
    try {
      const result = await runCommand(ctx.settings.nodePath, [fileURLToPath(import.meta.url), '--tcc', '/Library/Application Support/com.apple.TCC/TCC.db', gateway.executable], { timeoutMs: 5000, maxBytes: 4096, env: runtimeEnv(ctx.settings) });
      const parsed = object(JSON.parse(result.stdout) as unknown);
      if (parsed.status !== 'ok') throw new Error('Unreadable TCC');
      const p = object(parsed.permissions);
      const allowed = new Set<Permission>(['Granted', 'Denied', 'No matching record', 'Unreadable', 'Other recorded value', 'Conflicting records']);
      for (const key of Object.keys(services) as (keyof typeof services)[]) {
        const value = p[key];
        if (typeof value !== 'string' || !allowed.has(value as Permission)) throw new Error('Invalid TCC result');
        permissions[key] = value as Permission;
      }
      permissionsStatus = 'ok';
    } catch { permissions = unknownPermissions(); permissionsStatus = 'unreadable'; }
    let current: Gateway | undefined;
    try { current = await discoverGateway(ctx); } catch { /* changed or stopped */ }
    if (current?.identity !== gateway.identity) {
      gatewayStatus = packageStatus = permissionsStatus = 'changed';
      openclawVersion = nodeVersion = 'Changed during observation';
      permissions = unknownPermissions('Changed during observation');
    }
  }
  const now = Math.floor(Date.now() / 1000);
  const statuses = { gateway: gatewayStatus, package: packageStatus, configuration: configuration.status, permissions: permissionsStatus };
  const success = gatewayStatus === 'ok' && packageStatus === 'ok' && permissionsStatus === 'ok'
    && (configuration.status === 'ok' || configuration.status === 'missing');
  let lastSuccess = 0;
  const statePath = join(ctx.root, 'state/comparison.json');
  try {
    const previous = object(JSON.parse(await readRegular(statePath, 8192)) as unknown);
    if (gatewayStatus !== 'changed' && gateway?.identity === previous.identity && typeof previous.lastSuccess === 'number' && Number.isSafeInteger(previous.lastSuccess) && previous.lastSuccess <= now && previous.lastSuccess >= 0) lastSuccess = previous.lastSuccess;
  } catch { /* First run or invalid old state is not a successful observation. */ }
  if (success) lastSuccess = now;
  // Private audit identity; executable paths, PIDs and timestamps are not metric labels.
  await writeAtomic(statePath, JSON.stringify({ identity: gateway?.identity ?? null, executable: gateway?.executable ?? null, script: gateway?.script ?? null, packageRoot: gateway?.packageRoot ?? null, lastSuccess, observedAt: now, statuses }) + '\n');
  const node = ctx.config.node_label;
  const snapshot = { node, openclaw_version: openclawVersion, gateway_node_version: nodeVersion, tools_profile: configuration.tools_profile, tools_exec_mode: configuration.tools_exec_mode, ...permissions,
    gateway_status: gatewayStatus, package_status: packageStatus, configuration_status: configuration.status, permissions_status: permissionsStatus };
  let content = metric('fleet_node_comparison_snapshot_timestamp_seconds', 'Completed read-only comparison snapshot time; labels are recorded evidence, not live capability tests.', now, snapshot)
    + metric('fleet_node_comparison_last_success_timestamp_seconds', 'Last complete successful observation for this Gateway identity; zero means none.', lastSuccess, { node });
  content += '# HELP fleet_node_comparison_section_status Latest bounded per-section collection status.\n# TYPE fleet_node_comparison_section_status gauge\n';
  for (const [section, status] of Object.entries(statuses)) content += `fleet_node_comparison_section_status${labels({ node, section, status })} 1\n`;
  await writeAtomic(join(ctx.root, 'textfiles/node_comparison.prom'), content, 0o644);
  return 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url) && process.argv[2] === '--tcc') {
  const database = process.argv[3]; const executable = process.argv[4];
  if (!database || !executable) process.exitCode = 64;
  else console.log(JSON.stringify(readPermissions(database, executable)));
}
