import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

export type FailureKind = 'missing' | 'timeout' | 'failed' | 'output_limit';
export class ProcessError extends Error {
  constructor(public readonly kind: FailureKind) { super(`Subprocess ${kind}`); this.name = 'ProcessError'; }
}
export interface CommandOptions { timeoutMs: number; cwd?: string; env?: NodeJS.ProcessEnv; maxBytes?: number; allowNonzero?: boolean }

function signalGroup(pid: number | undefined, signal: NodeJS.Signals): void {
  if (pid === undefined) return;
  try { process.kill(-pid, signal); } catch (e) { if (!(e instanceof Error && 'code' in e && e.code === 'ESRCH')) throw e; }
}

export async function withCommand<T>(command: string, args: readonly string[], options: CommandOptions, body: (child: ChildProcessWithoutNullStreams) => Promise<T>): Promise<T> {
  const child = spawn(command, [...args], { cwd: options.cwd, env: options.env, stdio: ['pipe', 'pipe', 'pipe'], detached: true });
  let fail: (error: Error) => void = () => {};
  const failure = new Promise<never>((_, reject) => { fail = reject; });
  let bytes = 0;
  const count = (chunk: Buffer): void => {
    bytes += chunk.length;
    if (bytes > (options.maxBytes ?? 4 * 1024 * 1024)) fail(new ProcessError('output_limit'));
  };
  child.stdout.on('data', count); child.stderr.on('data', count);
  child.stdin.on('error', () => fail(new ProcessError('failed')));
  child.on('error', error => fail(new ProcessError('code' in error && error.code === 'ENOENT' ? 'missing' : 'failed')));
  const timer = setTimeout(() => fail(new ProcessError('timeout')), options.timeoutMs);
  let interrupted: NodeJS.Signals | undefined;
  const onTerm = (): void => { interrupted ??= 'SIGTERM'; fail(new ProcessError('failed')); };
  const onInt = (): void => { interrupted ??= 'SIGINT'; fail(new ProcessError('failed')); };
  // Consume repeated stop signals until the entire process group has been killed.
  // A once-listener would restore default termination during the cleanup grace.
  process.on('SIGTERM', onTerm); process.on('SIGINT', onInt);
  try { return await Promise.race([body(child), failure]); }
  finally {
    clearTimeout(timer);
    // Kill the process group even when its immediate child exited; descendants may hold pipes.
    signalGroup(child.pid, 'SIGTERM');
    child.stdin.destroy();
    await delay(150);
    signalGroup(child.pid, 'SIGKILL');
    child.stdout.destroy(); child.stderr.destroy();
    process.removeListener('SIGTERM', onTerm); process.removeListener('SIGINT', onInt);
    if (interrupted) process.kill(process.pid, interrupted);
  }
}

export async function runCommand(command: string, args: readonly string[], options: CommandOptions): Promise<{ stdout: string; stderr: string; code: number }> {
  return withCommand(command, args, options, child => new Promise((resolve, reject) => {
    const stdout: Buffer[] = []; const stderr: Buffer[] = [];
    child.stdout.on('data', (chunk: Buffer) => stdout.push(chunk));
    child.stderr.on('data', (chunk: Buffer) => stderr.push(chunk));
    child.stdin.end();
    child.once('close', code => {
      if (code === null || (code !== 0 && !options.allowNonzero)) reject(new ProcessError('failed'));
      else resolve({ stdout: Buffer.concat(stdout).toString('utf8'), stderr: Buffer.concat(stderr).toString('utf8'), code });
    });
  }));
}
