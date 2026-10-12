import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { renderCollectorConfig, writeCollectorConfig, HEARTBEAT_METRIC } from '../dist/collector.js';

function context(root) {
  return { root, config: { config_schema_version: 3, node_label: 'bill', telemetry_endpoint: 'https://unused.invalid', codex_usage_enabled: false }, settings: {} };
}

test('central source identity and six independently persisted queue contracts survive rendering', () => {
  const config = renderCollectorConfig(context('/private/agent'));
  assert.deepEqual(Object.keys(config.exporters), ['otlp_http/logs', 'otlp_http/traces', 'otlp_http/app_metrics', 'otlp_http/agent', 'otlp_http/host', 'otlp_http/heartbeat']);
  assert.equal(config.processors['resource/heartbeat'].attributes[0].value, 'node_agent_heartbeat');
  assert.equal(config.processors['resource/host'].attributes[0].value, 'node_agent_host');
  assert.equal(config.processors['resource/agent'].attributes[0].value, 'node_agent_internal');
  assert.equal(config.processors['resource/openclaw'].attributes[0].value, 'openclaw');
  for (const exporter of Object.values(config.exporters)) {
    assert.equal(exporter.headers.Authorization, '${file:/private/agent/secrets/authorization-header}');
    assert.equal(exporter.sending_queue.storage, 'file_storage/fleet');
    assert.equal(exporter.sending_queue.block_on_overflow, false);
  }
  assert.equal(config.exporters['otlp_http/heartbeat'].retry_on_failure.max_elapsed_time, '5m');
  assert.equal(config.exporters['otlp_http/logs'].sending_queue.queue_size, 96 * 1024 * 1024);
  assert.equal(config.extensions['file_storage/fleet'].fsync, true);
});

test('failed native config validation cannot replace working private configuration', async t => {
  const root = await mkdtemp(join(tmpdir(), 'fleet-render-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'bin'));
  await mkdir(join(root, 'config'));
  const target = join(root, 'config/collector.json');
  await writeFile(target, 'prior-config\n', { mode: 0o600 });
  await writeFile(join(root, 'bin/otelcol-contrib'), '#!/bin/sh\nexit 1\n', { mode: 0o700 });
  await assert.rejects(writeCollectorConfig(context(root)));
  assert.equal(await readFile(target, 'utf8'), 'prior-config\n');
});

async function port() {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const value = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return value;
}
async function documents(path) {
  try { return (await readFile(path, 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
}
function metrics(values) { return values.flatMap(doc => doc.resourceMetrics ?? []).flatMap(resource => resource.scopeMetrics ?? []).flatMap(scope => scope.metrics ?? []).map(metric => metric.name); }

test('pinned real Collector routes heartbeats separately and filters only structured routine successes', { skip: !process.env.FLEET_COLLECTOR_BINARY }, async t => {
  const root = await mkdtemp(join(tmpdir(), 'fleet-collector-live-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const metricsServer = createServer((_request, response) => {
    response.setHeader('Content-Type', 'text/plain; version=0.0.4');
    response.end(`# TYPE ${HEARTBEAT_METRIC} gauge\n${HEARTBEAT_METRIC}{node="bill"} ${Math.floor(Date.now() / 1000)}\n# TYPE node_load1 gauge\nnode_load1 1\n`);
  });
  metricsServer.listen(0, '127.0.0.1'); await once(metricsServer, 'listening');
  t.after(() => new Promise(resolve => metricsServer.close(resolve)));
  const [otlp, health, internal] = await Promise.all([port(), port(), port()]);
  const config = renderCollectorConfig(context(root));
  config.extensions.health_check.endpoint = `127.0.0.1:${health}`;
  config.receivers['otlp/openclaw'].protocols.http.endpoint = `127.0.0.1:${otlp}`;
  config.service.telemetry.metrics.readers[0].pull.exporter.prometheus.port = internal;
  const scrape = config.receivers['prometheus/node_exporter'].config;
  scrape.global = { scrape_interval: '200ms', scrape_timeout: '100ms' };
  scrape.scrape_configs[0].static_configs[0].targets = [`127.0.0.1:${metricsServer.address().port}`];
  config.receivers['prometheus/agent'].config.scrape_configs[0].static_configs[0].targets = [`127.0.0.1:${internal}`];
  config.exporters = Object.fromEntries(['logs', 'traces', 'app_metrics', 'agent', 'host', 'heartbeat'].map(name => [`file/${name}`, { path: join(root, name + '.json') }]));
  for (const pipeline of Object.values(config.service.pipelines)) pipeline.exporters = pipeline.exporters.map(name => name.replace('otlp_http/', 'file/'));
  await writeFile(join(root, 'collector.json'), JSON.stringify(config));
  const child = spawn(process.env.FLEET_COLLECTOR_BINARY, ['--config=file:' + join(root, 'collector.json')], { stdio: ['ignore', 'ignore', 'pipe'] });
  let errors = ''; child.stderr.on('data', data => { errors += data; });
  const exited = once(child, 'exit');
  t.after(async () => { if (child.exitCode === null) child.kill('SIGTERM'); await exited; });
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try { ready = (await fetch(`http://127.0.0.1:${health}`, { signal: AbortSignal.timeout(500) })).ok; } catch {}
    if (ready) break;
    if (child.exitCode !== null) assert.fail(errors);
    await delay(50);
  }
  assert.equal(ready, true, errors);
  const attrs = values => Object.entries(values).map(([key, value]) => ({ key, value: typeof value === 'number' ? { intValue: String(value) } : { stringValue: value } }));
  const security = { 'openclaw.security.action': 'gateway.auth.succeeded', 'openclaw.security.outcome': 'success', 'openclaw.security.policy.decision': 'allow' };
  const tool = { 'openclaw.logger': 'agents/tool-policy', 'openclaw.removedToolCount': 2, 'openclaw.ruleKind': 'allow' };
  const cases = [
    ['drop-security', 9, 'openclaw.security.event', security], ['keep-security-warn', 13, 'openclaw.security.event', security],
    ['drop-tool', 9, 'log', tool], ['keep-tool-warn', 13, 'log', tool], ['keep-near-miss', 9, 'log', { ...tool, 'openclaw.removedToolCount': 0 }],
    ['keep-status', 9, 'STATUS: partial', {}], ['keep-task', 9, 'qmd sync completed for agent bill', {}],
  ];
  const payload = { resourceLogs: [{ resource: {}, scopeLogs: [{ scope: {}, logRecords: cases.map(([id, severityNumber, body, attributes]) => ({ timeUnixNano: String(BigInt(Date.now()) * 1_000_000n), severityNumber, body: { stringValue: body }, attributes: attrs({ 'test.case': id, ...attributes }) })) }] }] };
  const response = await fetch(`http://127.0.0.1:${otlp}/v1/logs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  assert.equal(response.ok, true);
  let records = [];
  for (let i = 0; i < 100; i++) {
    records = (await documents(join(root, 'logs.json'))).flatMap(doc => doc.resourceLogs ?? []).flatMap(resource => resource.scopeLogs ?? []).flatMap(scope => scope.logRecords ?? []);
    if (records.length >= 5 && metrics(await documents(join(root, 'host.json'))).includes('node_load1') && metrics(await documents(join(root, 'heartbeat.json'))).includes(HEARTBEAT_METRIC)) break;
    await delay(100);
  }
  const ids = records.map(record => record.attributes.find(attribute => attribute.key === 'test.case').value.stringValue).sort();
  assert.deepEqual(ids, cases.map(item => item[0]).filter(id => id.startsWith('keep-')).sort());
  assert.equal(metrics(await documents(join(root, 'host.json'))).includes(HEARTBEAT_METRIC), false);
  assert.equal(metrics(await documents(join(root, 'heartbeat.json'))).includes(HEARTBEAT_METRIC), true);
  assert.equal(metrics(await documents(join(root, 'heartbeat.json'))).includes('node_load1'), false);
});
