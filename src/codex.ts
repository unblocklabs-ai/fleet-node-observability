import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import type { RuntimeContext } from './config.js';
import { runtimeEnv } from './config.js';
import { labels, metric, writeAtomic } from './io.js';
import { ProcessError, withCommand } from './process.js';

type ObjectValue = Record<string, unknown>;
type Window = Partial<Record<'used_percent' | 'remaining_percent' | 'window_minutes' | 'resets_at_seconds', number>>;
interface Snapshot {
  accountDomain: string;
  planType: string;
  primary: Window | null;
  secondary: Window | null;
  credits: Partial<Record<'has_credits' | 'unlimited' | 'balance', number>>;
}

export class CodexError extends Error {
  constructor(readonly errorType: string, message: string) { super(message); }
}

function object(value: unknown): ObjectValue | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as ObjectValue : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function number(value: unknown): number | undefined {
  if (typeof value !== 'number' && (typeof value !== 'string' || !value.trim())) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function bool(value: unknown): number | undefined {
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'number' && Number.isFinite(value)) return value ? 1 : 0;
  if (typeof value === 'string') {
    if (['1', 'true', 'yes'].includes(value.trim().toLowerCase())) return 1;
    if (['0', 'false', 'no'].includes(value.trim().toLowerCase())) return 0;
  }
  return undefined;
}

function parseWindow(value: unknown): Window | null {
  const input = object(value);
  if (!input) return null;
  const result: Window = {};
  const used = number(input.usedPercent ?? input.used_percent);
  const reset = number(input.resetsAt ?? input.resets_at);
  const minutes = number(input.windowDurationMins ?? input.window_minutes);
  if (used !== undefined) { result.used_percent = used; result.remaining_percent = Math.max(0, 100 - used); }
  if (reset !== undefined) result.resets_at_seconds = reset;
  if (minutes !== undefined) result.window_minutes = minutes;
  return Object.keys(result).length ? result : null;
}

export function parseCodexUsage(limitsPayload: ObjectValue, accountPayload: ObjectValue): Snapshot {
  const rateLimits = object(object(limitsPayload.rateLimitsByLimitId)?.codex) ?? object(limitsPayload.rateLimits) ?? limitsPayload;
  const account = object(accountPayload.account) ?? {};
  const creditsPayload = object(rateLimits.credits) ?? {};
  const credits: Snapshot['credits'] = {};
  const hasCredits = bool(creditsPayload.hasCredits ?? creditsPayload.has_credits);
  const unlimited = bool(creditsPayload.unlimited);
  const balance = number(creditsPayload.balance);
  if (hasCredits !== undefined) credits.has_credits = hasCredits;
  if (unlimited !== undefined) credits.unlimited = unlimited;
  if (balance !== undefined) credits.balance = balance;
  const email = text(account.email);
  const domain = email?.includes('@') ? email.slice(email.lastIndexOf('@') + 1).trim().toLowerCase() : '';
  const result: Snapshot = {
    accountDomain: domain || 'unknown', planType: text(account.planType) ?? text(rateLimits.planType) ?? 'unknown',
    primary: parseWindow(rateLimits.primary), secondary: parseWindow(rateLimits.secondary), credits,
  };
  if (!result.primary && !result.secondary && !Object.keys(credits).length) {
    throw new CodexError('app_server_no_usage', 'Codex app-server did not return usage windows or credits');
  }
  return result;
}

export async function fetchCodexUsage(context: RuntimeContext, timeoutMs = 10_000): Promise<Snapshot> {
  const packageJson: unknown = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
  const version = text(object(packageJson)?.version);
  if (!version) throw new Error('Package version is missing');
  try {
    return await withCommand(context.settings.codexBin, ['-s', 'read-only', '-a', 'never', 'app-server'], {
      timeoutMs, maxBytes: 1024 * 1024, cwd: context.settings.nodeHome, env: runtimeEnv(context.settings),
    }, async (child) => {
      const reader = createInterface({ input: child.stdout, crlfDelay: Infinity });
      const iterator = reader[Symbol.asyncIterator]();
      const stdinError = (): void => {}; // write callback reports EPIPE without an unhandled stream error.
      child.stdin.on('error', stdinError);
      async function send(payload: ObjectValue): Promise<void> {
        await new Promise<void>((resolve, reject) => {
          child.stdin.write(`${JSON.stringify(payload)}\n`, (error) => error ? reject(new CodexError('app_server_connection_failed', 'Codex app-server connection closed unexpectedly')) : resolve());
        });
      }
      async function rpc(id: number, method: string, params: ObjectValue = {}): Promise<ObjectValue> {
        await send({ id, method, params });
        for (;;) {
          const next = await iterator.next();
          if (next.done) throw new CodexError('app_server_connection_failed', 'Codex app-server closed its output');
          let parsed: unknown;
          try { parsed = JSON.parse(next.value); } catch { continue; }
          const message = object(parsed);
          if (!message || message.id !== id) continue;
          if (object(message.error)) throw new CodexError('app_server_capability_unavailable', `Codex app-server method ${method} failed`);
          const result = object(message.result);
          if (result) return result;
          throw new CodexError('app_server_invalid_response', `Codex app-server method ${method} returned no object result`);
        }
      }
      try {
        await rpc(1, 'initialize', { clientInfo: { name: 'fleet-node-observability', version } });
        await send({ method: 'initialized', params: {} });
        const account = await rpc(2, 'account/read', { refreshToken: false });
        const limits = await rpc(3, 'account/rateLimits/read');
        return parseCodexUsage(limits, account);
      } finally {
        reader.close();
        child.stdin.off('error', stdinError);
      }
    });
  } catch (error) {
    if (error instanceof CodexError) throw error;
    if (error instanceof ProcessError) {
      const types = { missing: 'app_server_missing', timeout: 'app_server_timeout', failed: 'app_server_start_failed', output_limit: 'app_server_invalid_response' };
      throw new CodexError(types[error.kind], 'Codex app-server collection failed');
    }
    throw new CodexError('app_server_connection_failed', 'Codex app-server connection closed unexpectedly');
  }
}

export function renderCodex(node: string, now: number, snapshot: Snapshot | null, errorType = 'none'): string {
  const identity = { node, profile: 'default', account_domain: snapshot?.accountDomain ?? 'unknown', plan_type: snapshot?.planType ?? 'unknown', source: 'app_server' };
  let result = metric('codex_collector_success', 'Whether the latest Codex usage collection attempt succeeded.', snapshot ? 1 : 0, { ...identity, error_type: errorType })
    + metric('codex_usage_collected_at_seconds', 'Unix timestamp when Codex usage was collected.', now, identity);
  if (snapshot) {
    for (const name of ['primary', 'secondary'] as const) {
      for (const [key, value] of Object.entries(snapshot[name] ?? {})) result += `codex_usage_${name}_${key}${labels(identity)} ${value}\n`;
    }
    for (const [key, value] of Object.entries(snapshot.credits)) result += `codex_credits_${key}${labels(identity)} ${value}\n`;
  }
  return result;
}

export async function collectCodex(context: RuntimeContext): Promise<number> {
  if (!context.config.codex_usage_enabled) return 0;
  let snapshot: Snapshot | null = null;
  let errorType = 'none';
  try { snapshot = await fetchCodexUsage(context); }
  catch (error) { errorType = error instanceof CodexError ? error.errorType : 'app_server_connection_failed'; }
  await writeAtomic(join(context.root, 'textfiles', 'codex_usage.prom'),
    renderCodex(context.config.node_label, Math.floor(Date.now() / 1000), snapshot, errorType), 0o644);
  return 0;
}
