import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { promisify } from 'node:util';
import type { RuntimeContext } from './config.js';
import { ensurePrivateDir, writeAtomic } from './io.js';
import { dependencyPaths } from './dependencies.js';

const execute = promisify(execFile);
export const HEARTBEAT_METRIC = 'fleet_node_agent_heartbeat_timestamp_seconds';
const routineLogDrops = [
  'severity_number < SEVERITY_NUMBER_WARN and body == "openclaw.security.event" and attributes["openclaw.security.action"] == "gateway.auth.succeeded" and attributes["openclaw.security.outcome"] == "success" and attributes["openclaw.security.policy.decision"] == "allow"',
  'severity_number < SEVERITY_NUMBER_WARN and body == "log" and attributes["openclaw.logger"] == "agents/tool-policy" and attributes["openclaw.removedToolCount"] > 0 and (attributes["openclaw.ruleKind"] == "allow" or attributes["openclaw.ruleKind"] == "deny")',
];

function source(value: string) {
  return { attributes: [{ key: 'fleet.signal.source', value, action: 'upsert' }] };
}

function exporter(ctx: RuntimeContext, queueMiB: number, requestKiB: number, maxElapsed: string) {
  const requestBytes = requestKiB * 1024;
  return {
    endpoint: ctx.config.telemetry_endpoint,
    headers: { Authorization: '${file:' + join(ctx.root, 'secrets/authorization-header') + '}' },
    compression: 'gzip', timeout: '10s',
    retry_on_failure: { enabled: true, initial_interval: '1s', max_interval: '30s', max_elapsed_time: maxElapsed },
    sending_queue: {
      enabled: true, sizer: 'bytes', queue_size: queueMiB * 1024 * 1024,
      num_consumers: 1, block_on_overflow: false, storage: 'file_storage/fleet',
      batch: { flush_timeout: '1s', min_size: Math.min(requestBytes / 4, 256 * 1024), max_size: requestBytes, sizer: 'bytes' },
    },
  };
}

function scrape(job: string, endpoint: string) {
  return { config: {
    global: { scrape_interval: '30s', scrape_timeout: '5s' },
    scrape_configs: [{ job_name: job, static_configs: [{ targets: [endpoint] }] }],
  } };
}

/** Names, queue limits and source attributes are the deployed central contract. */
export function renderCollectorConfig(ctx: RuntimeContext) {
  return {
    extensions: {
      'file_storage/fleet': {
        directory: join(ctx.root, 'queue'), create_directory: true, fsync: true,
        compaction: { directory: join(ctx.root, 'queue/compaction'), on_start: true },
      },
      health_check: { endpoint: '127.0.0.1:13133' },
    },
    receivers: {
      // Jev accepts 32 MiB JSON; the OTLP string envelope can double escaping.
      'otlp/openclaw': { protocols: { http: { endpoint: '127.0.0.1:4318', max_request_body_size: 64 * 1024 * 1024 } } },
      'prometheus/agent': scrape('fleet_node_agent', '127.0.0.1:8888'),
      'prometheus/node_exporter': scrape('node_exporter_fleet', '127.0.0.1:9100'),
    },
    processors: {
      memory_limiter: { check_interval: '1s', limit_mib: 96, spike_limit_mib: 24 },
      'resource/openclaw': source('openclaw'),
      'resource/agent': source('node_agent_internal'),
      'resource/host': source('node_agent_host'),
      'resource/heartbeat': source('node_agent_heartbeat'),
      'filter/routine_openclaw_logs': { error_mode: 'ignore', logs: { log_record: routineLogDrops } },
      'filter/host_metrics': { error_mode: 'ignore', metrics: { metric: [`name == "${HEARTBEAT_METRIC}"`] } },
      'filter/heartbeat': { error_mode: 'ignore', metrics: { metric: [`name != "${HEARTBEAT_METRIC}"`] } },
      'batch/logs': { timeout: '1s', send_batch_size: 512, send_batch_max_size: 1024 },
      'batch/traces': { timeout: '1s', send_batch_size: 512, send_batch_max_size: 1024 },
      'batch/app_metrics': { timeout: '2s', send_batch_size: 1000, send_batch_max_size: 2000 },
      'batch/agent': { timeout: '5s', send_batch_size: 500, send_batch_max_size: 1000 },
      'batch/host': { timeout: '3s', send_batch_size: 1000, send_batch_max_size: 2000 },
      'batch/heartbeat': { timeout: '1s', send_batch_size: 10, send_batch_max_size: 20 },
    },
    exporters: {
      'otlp_http/logs': {
        ...exporter(ctx, 96, 1024, '0s'),
        sending_queue: {
          ...exporter(ctx, 96, 1024, '0s').sending_queue,
          // The byte splitter permanently drops an indivisible log above max_size.
          // Send whole log records; one record per request also avoids aggregation
          // pushing a provider-sized packet over the HTTP transport envelope.
          batch: { flush_timeout: '1s', min_size: 1, max_size: 1, sizer: 'items' },
        },
      },
      'otlp_http/traces': exporter(ctx, 48, 1024, '12h'),
      'otlp_http/app_metrics': exporter(ctx, 32, 512, '6h'),
      'otlp_http/agent': exporter(ctx, 16, 256, '30m'),
      'otlp_http/host': exporter(ctx, 48, 512, '6h'),
      'otlp_http/heartbeat': exporter(ctx, 8, 64, '5m'),
    },
    service: {
      extensions: ['file_storage/fleet', 'health_check'],
      telemetry: { metrics: { level: 'normal', readers: [{ pull: { exporter: { prometheus: { host: '127.0.0.1', port: 8888 } } } }] } },
      pipelines: {
        'logs/openclaw': { receivers: ['otlp/openclaw'], processors: ['memory_limiter', 'resource/openclaw', 'filter/routine_openclaw_logs', 'batch/logs'], exporters: ['otlp_http/logs'] },
        'traces/openclaw': { receivers: ['otlp/openclaw'], processors: ['memory_limiter', 'resource/openclaw', 'batch/traces'], exporters: ['otlp_http/traces'] },
        'metrics/openclaw': { receivers: ['otlp/openclaw'], processors: ['memory_limiter', 'resource/openclaw', 'batch/app_metrics'], exporters: ['otlp_http/app_metrics'] },
        'metrics/agent': { receivers: ['prometheus/agent'], processors: ['memory_limiter', 'resource/agent', 'batch/agent'], exporters: ['otlp_http/agent'] },
        'metrics/host': { receivers: ['prometheus/node_exporter'], processors: ['memory_limiter', 'filter/host_metrics', 'resource/host', 'batch/host'], exporters: ['otlp_http/host'] },
        'metrics/heartbeat': { receivers: ['prometheus/node_exporter'], processors: ['memory_limiter', 'filter/heartbeat', 'resource/heartbeat', 'batch/heartbeat'], exporters: ['otlp_http/heartbeat'] },
      },
    },
  };
}

export async function writeCollectorConfig(ctx: RuntimeContext): Promise<string> {
  const directory = join(ctx.root, 'config');
  await ensurePrivateDir(directory);
  const output = join(directory, 'collector.json');
  const temporary = join(directory, `.collector-validate-${randomUUID()}.json`);
  const content = JSON.stringify(renderCollectorConfig(ctx), null, 2) + '\n';
  await writeAtomic(temporary, content);
  try {
    await execute(dependencyPaths(ctx).collector, ['validate', `--config=file:${temporary}`], { timeout: 15_000, maxBuffer: 1024 * 1024 });
    await writeAtomic(output, content);
  } finally {
    await unlink(temporary);
  }
  return output;
}
