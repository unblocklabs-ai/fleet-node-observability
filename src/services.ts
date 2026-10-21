import { execFile } from 'node:child_process';
import { lstat, open, readFile, rmdir, unlink } from 'node:fs/promises';
import { userInfo } from 'node:os';
import { dirname, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import type { RuntimeContext } from './config.js';
import { ensurePrivateDir, writeAtomic } from './io.js';
import { dependencyPaths } from './dependencies.js';

const execute = promisify(execFile);
const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
export const serviceJobs = ['collector', 'exporter', 'heartbeat', 'readiness', 'thermal', 'cron', 'sessions', 'codex'] as const;
export type ServiceJob = typeof serviceJobs[number];
const intervals: Partial<Record<ServiceJob, number>> = { heartbeat: 30, readiness: 60, thermal: 60, cron: 300, sessions: 300, codex: 300 };
export function serviceLabel(job: ServiceJob): string { return `com.unblocklabs.fleet-node-ts.${job}`; }
const shell = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
const xml = (value: string) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');
const missing = (error: unknown) => error instanceof Error && 'code' in error && error.code === 'ENOENT';
const normalizeBirth = (value: string) => value.trim().replace(/\s+/g, ' ');

function command(ctx: RuntimeContext, job: ServiceJob): string[] {
  const binaries = dependencyPaths(ctx);
  if (job === 'collector') return [binaries.collector, `--config=file:${join(ctx.root, 'config/collector.json')}`];
  if (job === 'exporter') return [binaries.nodeExporter, '--web.listen-address=127.0.0.1:9100', '--no-collector.thermal', `--collector.textfile.directory=${join(ctx.root, 'textfiles')}`];
  return [ctx.settings.nodePath, join(packageRoot, 'dist/cli.js'), 'collect', job, '--root', ctx.root];
}

/** The stable launcher only gates and execs. launchd remains the sole supervisor. */
function launcher(ctx: RuntimeContext, backgroundCodex: boolean): string {
  const cases = serviceJobs.map((job) => `  ${job}) exec ${command(ctx, job).map(shell).join(' ')} ;;`).join('\n');
  return `#!/bin/sh
set -eu
umask 077
gate=${shell(join(ctx.root, 'services/enabled'))}
job="\${1:-}"
case "$job" in collector|exporter|heartbeat|readiness|thermal|cron|sessions|codex) ;; *) exit 64 ;; esac
[ -f "$gate" ] && [ ! -L "$gate" ] || exit 0
if [ "$job" = codex ]; then [ -f ${shell(join(ctx.root, 'services/codex-enabled'))} ] || exit 0; fi
${backgroundCodex ? 'if [ "$job" = codex ]; then [ "${FLEET_NODE_CODEX_BACKGROUND:-}" = 1 ] || exit 0; fi\n' : ''}\
lock=${shell(join(ctx.root, 'state/jobs'))}/"$job"
[ ! -L "$lock" ] || exit 1
if ! /bin/mkdir "$lock" 2>/dev/null; then
  [ -d "$lock" ] && [ ! -L "$lock/owner" ] || exit 1
  old_pid=$(/usr/bin/sed -n '1p' "$lock/owner" 2>/dev/null || true)
  old_birth=$(/usr/bin/sed -n '2p' "$lock/owner" 2>/dev/null || true)
  case "$old_pid" in ''|*[!0-9]*) ;; *)
    current_birth=$(/bin/ps -p "$old_pid" -o lstart= 2>/dev/null || true)
    if [ -n "$old_birth" ] && [ "$old_birth" = "$current_birth" ] && kill -0 "$old_pid" 2>/dev/null; then exit 0; fi ;;
  esac
  /bin/rm -f "$lock/owner"
  /bin/rmdir "$lock" 2>/dev/null || exit 0
  /bin/mkdir "$lock" 2>/dev/null || exit 0
fi
cleanup() {
  if [ "$(/usr/bin/sed -n '1p' "$lock/owner" 2>/dev/null || true)" = "$$" ]; then
    /bin/rm -f "$lock/owner"
    /bin/rmdir "$lock" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM
birth=$(/bin/ps -p "$$" -o lstart=)
printf '%s\\n%s\\n' "$$" "$birth" > "$lock/owner"
# Atomic mkdir followed by this second check closes the update/launch race:
# an entrant either becomes visible to stop, or observes the closed gate.
[ -f "$gate" ] && [ ! -L "$gate" ] || exit 0
case "$job" in
${cases}
esac
`;
}

function plist(ctx: RuntimeContext, job: ServiceJob, backgroundCodex: boolean): string {
  const interval = intervals[job];
  const background = job === 'codex' && backgroundCodex;
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>${serviceLabel(job)}</string>
${background ? '<key>LimitLoadToSessionType</key><string>Background</string>' : `<key>UserName</key><string>${xml(userInfo().username)}</string>`}
<key>ProgramArguments</key><array><string>/bin/sh</string><string>${xml(join(ctx.root, 'services/launch.sh'))}</string><string>${job}</string></array>
<key>EnvironmentVariables</key><dict><key>HOME</key><string>${xml(ctx.settings.nodeHome)}</string><key>PATH</key><string>${xml(dirname(ctx.settings.nodePath))}:/usr/bin:/bin:/usr/sbin:/sbin</string>${background ? '<key>FLEET_NODE_CODEX_BACKGROUND</key><string>1</string>' : ''}</dict>
<key>Umask</key><integer>63</integer>
<key>RunAtLoad</key><true/>
${interval === undefined ? '<key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>' : `<key>StartInterval</key><integer>${interval}</integer>`}
<key>StandardOutPath</key><string>${xml(join(ctx.root, 'logs', job + '.log'))}</string>
<key>StandardErrorPath</key><string>${xml(join(ctx.root, 'logs', job + '.err.log'))}</string>
</dict></plist>
`;
}

async function removeFile(path: string): Promise<void> {
  try { await unlink(path); } catch (error) { if (!missing(error)) throw error; }
}

async function syncDirectory(path: string): Promise<void> {
  const handle = await open(path, 'r');
  try { await handle.sync(); } finally { await handle.close(); }
}

async function closeGate(ctx: RuntimeContext): Promise<void> {
  await ensurePrivateDir(join(ctx.root, 'services'));
  await removeFile(join(ctx.root, 'services/enabled'));
  await syncDirectory(join(ctx.root, 'services'));
}

export async function prepareServices(ctx: RuntimeContext): Promise<{ installScript: string; launcher: string }> {
  for (const path of ['services', 'state/jobs', 'logs']) await ensurePrivateDir(join(ctx.root, path));
  try {
    await lstat(join(ctx.root, 'services/enabled'));
    throw new Error('Stop services before preparing their launch configuration');
  } catch (error) { if (!missing(error)) throw error; }
  // Preserve an already-repaired Background job; ordinary updates must not
  // move it back into the system domain. Other hosts retain their existing layout.
  const backgroundCodex = (await launchdPid(ctx, 'codex')).target.startsWith('user/');
  const launchPath = join(ctx.root, 'services/launch.sh');
  await writeAtomic(launchPath, launcher(ctx, backgroundCodex), 0o700);
  if (ctx.config.codex_usage_enabled) await writeAtomic(join(ctx.root, 'services/codex-enabled'), 'enabled\n');
  else {
    await removeFile(join(ctx.root, 'services/codex-enabled'));
    // Re-scraping an old success must not keep a disabled capability visible.
    await removeFile(join(ctx.root, 'textfiles/codex_usage.prom'));
  }
  const setup: string[] = [
    '#!/bin/sh', 'set -eu',
    '[ "$(/usr/bin/id -u)" -eq 0 ] || { echo "Run this one-time setup script with sudo" >&2; exit 1; }',
    '[ "$(/usr/bin/uname -s)" = Darwin ] || exit 1',
  ];
  for (const job of serviceJobs) {
    const source = join(ctx.root, 'services', serviceLabel(job) + '.plist');
    const background = job === 'codex' && backgroundCodex;
    const domain = background ? `user/${userInfo().uid}` : 'system';
    const target = (background ? '/Library/LaunchAgents/' : '/Library/LaunchDaemons/') + serviceLabel(job) + '.plist';
    await writeAtomic(source, plist(ctx, job, backgroundCodex));
    setup.push(
      `/bin/launchctl bootout ${shell(domain + '/' + serviceLabel(job))} 2>/dev/null || true`,
      `/usr/bin/install -o root -g wheel -m 0644 ${shell(source)} ${shell(target)}`,
      `/bin/launchctl enable ${shell(domain + '/' + serviceLabel(job))}`,
      `/bin/launchctl bootstrap ${shell(domain)} ${shell(target)}`,
    );
  }
  const installScript = join(ctx.root, 'services/install-system.sh');
  await writeAtomic(installScript, setup.join('\n') + '\n', 0o700);
  return { installScript, launcher: launchPath };
}

interface Identity { pid: number; uid: number; birth: string; command: string }
async function identity(pid: number): Promise<Identity | null> {
  let stdout: string;
  try { ({ stdout } = await execute('/bin/ps', ['-p', String(pid), '-o', 'uid=,lstart=,command='], { timeout: 5_000, maxBuffer: 64 * 1024 })); }
  catch (error) {
    if (error instanceof Error && 'code' in error && error.code === 1) return null;
    throw error;
  }
  const fields = stdout.trim().split(/\s+/);
  if (fields.length < 7) return null;
  return { pid, uid: Number(fields[0]), birth: fields.slice(1, 6).join(' '), command: fields.slice(6).join(' ') };
}

export function parseLaunchdStatus(ctx: RuntimeContext, job: ServiceJob, output: string): { loaded: boolean; pid: number | null } {
  const block = /^\s*arguments = \{\n([\s\S]*?)^\s*\}/m.exec(output);
  const args = block?.[1]?.split('\n').map((line) => line.trim()).filter(Boolean);
  if (!args || args.length !== 3 || args[0] !== '/bin/sh' || args[1] !== join(ctx.root, 'services/launch.sh') || args[2] !== job) {
    throw new Error(`Launchd label ${serviceLabel(job)} belongs to another runtime root or launcher`);
  }
  const match = /^\s*pid = (\d+)$/m.exec(output);
  return { loaded: true, pid: match ? Number(match[1]) : null };
}

async function launchdPid(ctx: RuntimeContext, job: ServiceJob): Promise<{ loaded: boolean; pid: number | null; target: string }> {
  const system = 'system/' + serviceLabel(job);
  if (process.platform === 'darwin') {
    const targets = job === 'codex' ? [`user/${userInfo().uid}/${serviceLabel(job)}`, system] : [system];
    for (const target of targets) {
      try {
        const { stdout } = await execute('/bin/launchctl', ['print', target], { timeout: 5_000, maxBuffer: 1024 * 1024 });
        return { ...parseLaunchdStatus(ctx, job, stdout), target };
      } catch (error) {
        if (!(error instanceof Error && 'stderr' in error && /Could not find (?:specified )?service|No such process/i.test(String(error.stderr)))) throw error;
      }
    }
  }
  return { loaded: false, pid: null, target: system };
}

interface LockOwner { pid: number; birth: string }
async function readOwner(path: string): Promise<LockOwner | null> {
  let content: string;
  try {
    const info = await lstat(path);
    if (!info.isFile() || info.isSymbolicLink() || info.size > 1024) throw new Error(`Invalid service lock owner: ${path}`);
    content = await readFile(path, 'utf8');
  } catch (error) { if (missing(error)) return null; throw error; }
  const [pid, birth] = content.split('\n');
  if (!pid || !/^\d+$/.test(pid) || Number(pid) <= 1 || !birth?.trim()) throw new Error(`Invalid service lock owner: ${path}`);
  return { pid: Number(pid), birth: normalizeBirth(birth) };
}

function isManagedCommand(ctx: RuntimeContext, job: ServiceJob, value: string): boolean {
  if (value.includes(join(ctx.root, 'services/launch.sh'))) return true;
  const args = command(ctx, job);
  if (job === 'collector' || job === 'exporter') return value.startsWith(args[0] + ' ');
  return value.includes(join(packageRoot, 'dist/cli.js')) && value.includes('collect ' + job) && value.includes(ctx.root);
}

async function descendants(parents: readonly number[]): Promise<Identity[]> {
  if (!parents.length) return [];
  const { stdout } = await execute('/bin/ps', ['-axo', 'pid=,ppid=,uid='], { timeout: 5_000, maxBuffer: 4 * 1024 * 1024 });
  const children = stdout.trim().split('\n').map((line) => line.trim().split(/\s+/).map(Number));
  const found = new Set(parents);
  let changed = true;
  while (changed) {
    changed = false;
    for (const [pid, parent, uid] of children) {
      if (pid && parent && uid === process.getuid?.() && found.has(parent) && !found.has(pid)) { found.add(pid); changed = true; }
    }
  }
  const result = await Promise.all([...found].filter((pid) => !parents.includes(pid)).map(identity));
  return result.filter((item): item is Identity => item !== null);
}

async function signal(processIdentity: Identity, signalName: NodeJS.Signals): Promise<void> {
  const current = await identity(processIdentity.pid);
  if (!current || current.birth !== processIdentity.birth) return;
  if (current.uid !== process.getuid?.()) throw new Error('Refusing to signal a service owned by another user');
  try { process.kill(current.pid, signalName); }
  catch (error) { if (!(error instanceof Error && 'code' in error && error.code === 'ESRCH')) throw error; }
}

/** Leaves the gate closed on every failure. Never mutate npm after a failed stop. */
export async function stopServices(ctx: RuntimeContext): Promise<void> {
  await closeGate(ctx);
  const tracked = new Map<number, Identity>();
  const terminated = new Set<number>();
  const started = Date.now();
  while (Date.now() - started < 20_000) {
    const live: Identity[] = [];
    let pending = false;
    for (const job of serviceJobs) {
      const lock = join(ctx.root, 'state/jobs', job);
      const status = await launchdPid(ctx, job);
      const owner = await readOwner(join(lock, 'owner'));
      if (status.pid) {
        const current = await identity(status.pid);
        if (current) live.push(current);
      }
      if (owner) {
        const current = await identity(owner.pid);
        if (current && current.birth === owner.birth) {
          if (current.uid !== process.getuid?.() || (status.pid !== current.pid && !isManagedCommand(ctx, job, current.command))) {
            throw new Error(`Refusing to stop unrecognized process in ${job} lock`);
          }
          live.push(current);
        } else {
          await removeFile(join(lock, 'owner'));
        }
      }
      // An empty lock is necessarily before package entry. Atomic rmdir either
      // removes it (its entrant then cannot publish ownership) or fails because
      // ownership was just published and must be observed on the next pass.
      try { await rmdir(lock); }
      catch (error) {
        if (!missing(error)) {
          if (error instanceof Error && 'code' in error && error.code === 'ENOTEMPTY') pending = true;
          else throw error;
        }
      }
    }
    for (const child of await descendants(live.map((item) => item.pid))) tracked.set(child.pid, child);
    for (const item of live) tracked.set(item.pid, item);
    const surviving: Identity[] = [];
    for (const item of tracked.values()) {
      const current = await identity(item.pid);
      if (!current || current.birth !== item.birth) continue;
      surviving.push(current);
      if (!terminated.has(item.pid)) { await signal(item, 'SIGTERM'); terminated.add(item.pid); }
      else if (Date.now() - started > 3_000) await signal(item, 'SIGKILL');
    }
    if (!pending && !surviving.length) return;
    await delay(100);
  }
  throw new Error('Services did not quiesce within 20 seconds; maintenance gate remains closed');
}

export async function startServices(ctx: RuntimeContext): Promise<void> {
  await ensurePrivateDir(join(ctx.root, 'services'));
  await writeAtomic(join(ctx.root, 'services/enabled'), 'enabled\n');
  // KeepAlive daemons restart on their next launchd retry. Scheduled checks run
  // on their existing independent intervals; no privileged kickstart is needed.
}

export async function restartServices(ctx: RuntimeContext): Promise<void> {
  await stopServices(ctx);
  await startServices(ctx);
}

export async function serviceStatus(ctx: RuntimeContext) {
  let enabled = false;
  try { enabled = (await lstat(join(ctx.root, 'services/enabled'))).isFile(); }
  catch (error) { if (!missing(error)) throw error; }
  return { enabled, jobs: await Promise.all(serviceJobs.map(async (job) => ({ job, label: serviceLabel(job), ...await launchdPid(ctx, job) }))) };
}
