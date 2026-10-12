import { join } from 'node:path';
import type { RuntimeContext } from './config.js';
import { runtimeEnv } from './config.js';
import { labels, metric, writeAtomic } from './io.js';
import { ProcessError, runCommand } from './process.js';

type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue | undefined =>
  typeof value === 'object' && value !== null && !Array.isArray(value) ? value as ObjectValue : undefined;
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const bounded = (value: unknown, length: number, fallback: string): string =>
  String(value || fallback).replace(/\s+/gu, ' ').trim().slice(0, length) || fallback;
const kinds = new Set(['at', 'cron', 'every', 'on-exit', 'stream']);
const statuses = new Set(['cancelled', 'error', 'failed', 'never', 'ok', 'running', 'skipped', 'success', 'timeout']);

class CronError extends Error {
  constructor(readonly kind: string) { super(kind); }
}

/** OpenClaw sometimes prints diagnostics before and after its JSON object. */
export function parseJobs(output: string): ObjectValue[] {
  for (const match of output.matchAll(/^[ \t]*(?=\{)/gm)) {
    const start = match.index + match[0].length;
    let depth = 0;
    let quoted = false;
    let escaped = false;
    for (let index = start; index < output.length; index++) {
      const char = output[index];
      if (quoted) {
        if (escaped) escaped = false;
        else if (char === '\\') escaped = true;
        else if (char === '"') quoted = false;
      } else if (char === '"') quoted = true;
      else if (char === '{') depth++;
      else if (char === '}' && --depth === 0) {
        let payload: unknown;
        try { payload = JSON.parse(output.slice(start, index + 1)); }
        catch { break; }
        const jobs = object(payload)?.jobs;
        if (!Array.isArray(jobs) || jobs.some(job => !object(job))) throw new CronError('invalid_payload');
        return jobs as ObjectValue[];
      }
    }
  }
  throw new CronError('invalid_json');
}

function scheduleIdentity(value: unknown): [string, string, string] {
  const schedule = object(value);
  if (!schedule) return ['unknown', 'unknown', ''];
  const rawKind = bounded(schedule.kind, 24, 'unknown').toLowerCase();
  const kind = kinds.has(rawKind) ? rawKind : 'unknown';
  const timezone = bounded(schedule.tz, 80, 'local');
  let description = kind;
  if (kind === 'cron') description = bounded(schedule.expr, 160, 'unknown');
  else if (kind === 'at') description = bounded(schedule.at, 160, 'unknown');
  else if (kind === 'every') description = finite(schedule.everyMs) ? `every:${schedule.everyMs}ms` : 'every:unknown';
  return [kind, description, timezone];
}

export function renderCronFailure(node: string, kind: string, now = Date.now() / 1000): string {
  const base = { node, node_label: node };
  return metric('openclaw_cron_schedule_collected_at_seconds', 'Latest local cron collection attempt timestamp.', Math.floor(now), base)
    + metric('openclaw_cron_schedule_collector_success', 'Whether the latest local cron collection succeeded.', 0, base)
    + metric('openclaw_cron_schedule_collection_error', 'Latest bounded collection failure category.', 1, { ...base, error_type: kind });
}

export function renderCron(node: string, jobs: readonly ObjectValue[], now = Date.now() / 1000): string {
  const base = { node, node_label: node };
  let output = metric('openclaw_cron_schedule_collected_at_seconds', 'Latest local cron collection attempt timestamp.', Math.floor(now), base)
    + metric('openclaw_cron_schedule_collector_success', 'Whether the latest local cron collection succeeded.', 1, base);
  const sample = (name: string, value: number | string, identity: Record<string, string>): void => {
    output += `${name}${labels(identity)} ${value}\n`;
  };
  const declare = (name: string, help: string): void => { output += `# HELP ${name} ${help}\n# TYPE ${name} gauge\n`; };
  const enabled = (job: ObjectValue): boolean => job.enabled === undefined || Boolean(job.enabled);
  declare('openclaw_cron_schedule_jobs_total', 'OpenClaw cron jobs by enabled state.');
  const enabledCount = jobs.filter(enabled).length;
  sample('openclaw_cron_schedule_jobs_total', enabledCount, { ...base, cron_enabled: 'true' });
  sample('openclaw_cron_schedule_jobs_total', jobs.length - enabledCount, { ...base, cron_enabled: 'false' });
  const groups = new Map<string, { schedule: [string, string, string]; count: number }>();
  for (const job of jobs.filter(enabled)) {
    const schedule = scheduleIdentity(job.schedule);
    const key = JSON.stringify(schedule);
    const group = groups.get(key);
    if (group) group.count++;
    else groups.set(key, { schedule, count: 1 });
  }
  declare('openclaw_cron_schedule_group_jobs', 'Enabled OpenClaw cron jobs sharing one local schedule.');
  for (const [, { schedule: [kind, description, timezone], count }] of [...groups].sort(([a], [b]) => a.localeCompare(b))) {
    sample('openclaw_cron_schedule_group_jobs', count, { ...base, cron_schedule_kind: kind, cron_schedule: description, cron_timezone: timezone });
  }
  const declarations: Record<string, string> = {
    openclaw_cron_job_info: 'Bounded identity and latest state of a cron job.',
    openclaw_cron_job_next_run_timestamp_seconds: 'Timestamp of the next scheduled run.',
    openclaw_cron_job_last_run_timestamp_seconds: 'Timestamp of the latest attempted run; absent before first run.',
    openclaw_cron_job_running_since_timestamp_seconds: 'Timestamp when the active run started; absent when not running.',
    openclaw_cron_job_last_duration_seconds: 'Duration of the latest run.',
    openclaw_cron_job_consecutive_errors: 'Current consecutive execution error count.',
  };
  for (const [name, help] of Object.entries(declarations)) declare(name, help);
  for (const job of [...jobs].sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')) || String(a.id || '').localeCompare(String(b.id || '')))) {
    const state = object(job.state) ?? {};
    const [kind, schedule, timezone] = scheduleIdentity(job.schedule);
    const rawStatus = bounded(state.lastRunStatus || state.lastStatus, 24, 'never').toLowerCase();
    const identity = { ...base, cron_job_id: bounded(job.id, 80, 'unknown'), cron_job_name: bounded(job.name, 120, 'unnamed'),
      cron_schedule_kind: kind, cron_schedule: schedule, cron_timezone: timezone, cron_enabled: String(enabled(job)),
      cron_status: statuses.has(rawStatus) ? rawStatus : 'other', cron_running: String(Boolean(state.runningAtMs)) };
    sample('openclaw_cron_job_info', 1, identity);
    for (const [key, name] of [
      ['lastRunAtMs', 'openclaw_cron_job_last_run_timestamp_seconds'],
      ['runningAtMs', 'openclaw_cron_job_running_since_timestamp_seconds'],
      ['nextRunAtMs', 'openclaw_cron_job_next_run_timestamp_seconds'],
    ] as const) {
      const value = state[key];
      if (finite(value) && value > 0) sample(name, (value / 1000).toFixed(3), identity);
    }
    if (finite(state.lastDurationMs) && state.lastDurationMs >= 0) sample('openclaw_cron_job_last_duration_seconds', state.lastDurationMs / 1000, identity);
    sample('openclaw_cron_job_consecutive_errors', finite(state.consecutiveErrors) && state.consecutiveErrors >= 0 ? state.consecutiveErrors : 0, identity);
  }
  return output;
}

export async function collectCron(context: RuntimeContext): Promise<number> {
  let output: string;
  let status = 0;
  try {
    const result = await runCommand(context.settings.openclawBin, ['cron', 'list', '--all', '--json'], {
      timeoutMs: 20_000, maxBytes: 4 * 1024 * 1024, cwd: context.settings.nodeHome, env: runtimeEnv(context.settings),
    });
    if (result.code !== 0) throw new CronError('command_failed');
    output = renderCron(context.config.node_label, parseJobs(result.stdout));
  } catch (error) {
    const kind = error instanceof CronError ? error.kind : error instanceof ProcessError
      ? error.kind === 'missing' ? 'binary_missing' : error.kind === 'timeout' ? 'timeout' : 'command_failed'
      : 'command_failed';
    output = renderCronFailure(context.config.node_label, kind);
    status = 1;
  }
  await writeAtomic(join(context.root, 'textfiles/openclaw_cron_schedule.prom'), output);
  return status;
}
