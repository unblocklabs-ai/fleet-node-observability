import { unlink } from 'node:fs/promises';
import { join } from 'node:path';
import type { RuntimeContext } from './config.js';
import { runtimeEnv } from './config.js';
import { ensurePrivateDir, labels, metric, readRegular, writeAtomic } from './io.js';
import { runCommand } from './process.js';

const queues = {
  logs: 'logs', traces: 'traces', app_metrics: 'openclaw_metrics',
  agent: 'agent_metrics', host: 'host_metrics', heartbeat: 'heartbeat',
} as const;
type Signal = typeof queues[keyof typeof queues];

/** A malformed duplicate invalidates the exporter, not merely that sample. */
export function parseQueues(text: string): Map<Signal, number> {
  const totals = new Map<Signal, number>();
  const invalid = new Set<Signal>();
  for (const line of text.split('\n')) {
    const sample = /^otelcol_exporter_queue_size\{(.*)\}\s+(\S+)(?:\s+\S+)?\s*$/.exec(line);
    if (!sample) continue;
    const exporter = /(?:^|,)\s*exporter="otlp_http\/([^"]+)"(?:,|$)/.exec(sample[1]!);
    const key = exporter?.[1];
    if (!key || !Object.hasOwn(queues, key)) continue;
    const signal = queues[key as keyof typeof queues];
    const valueText = sample[2]!;
    const value = Number(valueText);
    if (!/^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(valueText)
        || !Number.isSafeInteger(value) || value < 0) {
      invalid.add(signal);
      continue;
    }
    const total = (totals.get(signal) ?? 0) + value;
    if (!Number.isSafeInteger(total)) invalid.add(signal);
    totals.set(signal, total);
  }
  for (const signal of invalid) totals.delete(signal);
  return totals;
}

async function storedQueueStart(path: string): Promise<number> {
  try {
    const first = (await readRegular(path, 128)).split('\n')[0] ?? '';
    return /^\d+$/.test(first) && Number.isSafeInteger(Number(first)) ? Number(first) : 0;
  } catch (error) {
    if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return 0;
    throw error;
  }
}

export async function renderHeartbeat(node: string, text: string, stateDir: string, now: number): Promise<string> {
  await ensurePrivateDir(stateDir);
  const observed = parseQueues(text);
  let result = metric('fleet_node_agent_heartbeat_timestamp_seconds',
    'Unix time when the node agent heartbeat was generated.', now, { node });
  result += metric('fleet_node_agent_queue_metrics_available',
    'Whether all six expected local Collector queue exporter samples are present and valid.',
    observed.size === 6 ? 1 : 0, { node });
  result += '# HELP fleet_node_agent_queue_oldest_age_seconds Seconds since a signal queue was first observed non-empty without a subsequently observed valid zero.\n'
    + '# TYPE fleet_node_agent_queue_oldest_age_seconds gauge\n';
  for (const signal of Object.values(queues)) {
    const path = join(stateDir, `queue-oldest-${signal}.timestamp`);
    let started = await storedQueueStart(path);
    const size = observed.get(signal);
    if (size === 0) {
      await unlink(path).catch((error: unknown) => {
        if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
      });
      started = 0;
    } else if (size !== undefined && (started <= 0 || started > now)) {
      started = now;
      await writeAtomic(path, `${started}\n`);
    }
    const age = started > 0 && started <= now ? now - started : 0;
    result += `fleet_node_agent_queue_oldest_age_seconds${labels({ node, signal })} ${age}\n`;
  }
  return result;
}

async function readMetrics(url: string): Promise<string> {
  const response = await fetch(url, { signal: AbortSignal.timeout(2_000), redirect: 'error' });
  if (!response.ok || !response.body) throw new Error('Collector metrics unavailable');
  let text = '';
  let bytes = 0;
  const decoder = new TextDecoder();
  for await (const chunk of response.body) {
    bytes += chunk.byteLength;
    if (bytes > 4 * 1024 * 1024) throw new Error('Collector metrics exceed safety limit');
    text += decoder.decode(chunk, { stream: true });
  }
  return text + decoder.decode();
}

export async function collectHeartbeat(context: RuntimeContext): Promise<number> {
  const now = Math.floor(Date.now() / 1000);
  const text = await readMetrics('http://127.0.0.1:8888/metrics').catch(() => '');
  const content = await renderHeartbeat(context.config.node_label, text, join(context.root, 'state'), now);
  await writeAtomic(join(context.root, 'textfiles', 'fleet_node_agent_heartbeat.prom'), content, 0o644);
  return 0;
}

export async function probeReadiness(url: string, timeoutMs = 5_000): Promise<boolean> {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), redirect: 'manual' });
    let bytes = 0;
    if (response.body) {
      for await (const chunk of response.body) {
        bytes += chunk.byteLength;
        if (bytes > 64 * 1024) return false;
      }
    }
    // curl --fail regards redirects as successful; it does not follow them by default.
    return response.status >= 200 && response.status < 400;
  } catch { return false; }
}

export async function collectReadiness(context: RuntimeContext): Promise<number> {
  const url = 'http://127.0.0.1:18789/readyz';
  const ready = await probeReadiness(url);
  const node = context.config.node_label;
  const content = metric('openclaw_gateway_ready', 'Whether the local OpenClaw gateway readiness endpoint is healthy.',
    ready ? 1 : 0, { node, gateway_ready_url: url })
    + metric('openclaw_gateway_check_timestamp_seconds', 'Unix timestamp of the completed local readiness probe.',
      Math.floor(Date.now() / 1000), { node });
  await writeAtomic(join(context.root, 'textfiles', 'openclaw_gateway_ready.prom'), content, 0o644);
  return 0;
}

const thermalLevels: Readonly<Record<string, number>> = {
  nominal: 0, normal: 0, fair: 1, moderate: 1, serious: 2, heavy: 2, critical: 3, trapping: 3,
};

export function parseThermal(text: string): { level: number | null; pressure: string } {
  if (text.toLowerCase().includes('no thermal warning level has been recorded')) return { level: 0, pressure: 'nominal' };
  for (const [pressure, level] of Object.entries(thermalLevels)) {
    if (new RegExp(`\\b${pressure}\\b`, 'i').test(text)) return { level, pressure };
  }
  const pressure = /thermal\s+pressure\s*:\s*([a-z]+)/i.exec(text)?.[1]?.toLowerCase() ?? 'unknown';
  return { level: null, pressure };
}

export function renderThermal(node: string, now: number, level: number | null, pressure: string, error?: string): string {
  const common = { node, node_label: node, source: 'pmset' };
  let result = metric('fleet_macos_thermal_pressure_available', 'Whether macOS thermal pressure was collected from this node.', level === null ? 0 : 1, common)
    + metric('fleet_macos_thermal_pressure_level', 'macOS thermal pressure level: 0 nominal, 1 moderate, 2 serious, 3 critical.', level ?? 0, { ...common, pressure })
    + metric('fleet_macos_thermal_collector_success', 'Whether the latest macOS thermal collection succeeded.', error ? 0 : 1, common)
    + metric('fleet_macos_thermal_collected_at_seconds', 'Unix timestamp of latest macOS thermal collection.', now, common);
  if (error) result += metric('fleet_macos_thermal_collection_error_info', 'Latest macOS thermal collection error.', 1, { ...common, error: error.slice(0, 160) });
  return result;
}

export async function collectThermal(context: RuntimeContext): Promise<number> {
  const now = Math.floor(Date.now() / 1000);
  let content: string;
  try {
    const result = await runCommand('/usr/bin/pmset', ['-g', 'therm'], {
      timeoutMs: 10_000, cwd: context.settings.nodeHome, env: runtimeEnv(context.settings), maxBytes: 64 * 1024, allowNonzero: true,
    });
    const { level, pressure } = parseThermal(result.stdout);
    const error = result.code !== 0 ? ((result.stderr || result.stdout).trim() || `pmset exited ${result.code}`)
      : level === null ? 'pmset output did not include a recognized thermal pressure level' : undefined;
    content = renderThermal(context.config.node_label, now, result.code === 0 ? level : null, result.code === 0 ? pressure : 'unknown', error);
  } catch (error) {
    content = renderThermal(context.config.node_label, now, null, 'unknown', error instanceof Error ? error.message : 'pmset failed');
  }
  await writeAtomic(join(context.root, 'textfiles', 'macos_thermal.prom'), content, 0o644);
  return 0;
}
