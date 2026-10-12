import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, readdir, stat, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createServer } from 'node:http';
import { renderHeartbeat, probeReadiness, parseThermal, renderThermal } from '../dist/health.js';

const exporters = ['logs', 'traces', 'app_metrics', 'agent', 'host', 'heartbeat'];
const exposition = value => exporters.map(e => `otelcol_exporter_queue_size{data_type="metrics",exporter="otlp_http/${e}"} ${value}`).join('\n');

test('heartbeat persists ages through unavailable/invalid observations and clears only valid drained queues', async t => {
  const root = await mkdtemp(join(tmpdir(), 'fleet-health-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const first = await renderHeartbeat('bill', exposition('4e1'), root, 1000);
  assert.match(first, /fleet_node_agent_heartbeat_timestamp_seconds\{node="bill"\} 1000/);
  assert.match(first, /fleet_node_agent_queue_metrics_available\{node="bill"\} 1/);
  const age = await renderHeartbeat('bill', exposition('1') + '\notelcol_exporter_queue_size{exporter="otlp_http/logs",partition="b"} 4', root, 1042);
  for (const signal of ['logs', 'traces', 'openclaw_metrics', 'agent_metrics', 'host_metrics', 'heartbeat']) {
    assert.ok(age.includes(`signal="${signal}"} 42`));
    assert.equal((await stat(join(root, `queue-oldest-${signal}.timestamp`))).mode & 0o777, 0o600);
  }
  const missing = await renderHeartbeat('bill', '', root, 1060);
  assert.match(missing, /fleet_node_agent_queue_metrics_available\{node="bill"\} 0/);
  assert.match(missing, /signal="heartbeat"\} 60/);
  const invalid = await renderHeartbeat('bill', exposition('0') + '\notelcol_exporter_queue_size{exporter="otlp_http/logs",partition="b"} NaN', root, 1070);
  assert.match(invalid, /fleet_node_agent_queue_metrics_available\{node="bill"\} 0/);
  assert.match(invalid, /signal="logs"\} 70/);
  assert.deepEqual(await readdir(root), ['queue-oldest-logs.timestamp']);
  const fraction = await renderHeartbeat('bill', exposition('0.4'), root, 1080);
  assert.match(fraction, /fleet_node_agent_queue_metrics_available\{node="bill"\} 0/);
  assert.equal(await readFile(join(root, 'queue-oldest-logs.timestamp'), 'utf8'), '1000\n');
  await renderHeartbeat('bill', exposition('0'), root, 1090);
  assert.deepEqual(await readdir(root), []);
  await writeFile(join(root, 'queue-oldest-logs.timestamp'), '99999\n');
  await renderHeartbeat('bill', exposition('1'), root, 1100);
  assert.equal(await readFile(join(root, 'queue-oldest-logs.timestamp'), 'utf8'), '1100\n');
});

test('a hung readiness endpoint times out without preventing independent heartbeat work', async t => {
  const server = createServer((request, response) => {
    if (request.url === '/ready') { response.writeHead(204); response.end(); }
    else if (request.url === '/failed') { response.writeHead(503); response.end(); }
    else if (request.url === '/hung-body') { response.writeHead(200); response.write('not complete'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const endpoint = `http://127.0.0.1:${server.address().port}`;
  assert.equal(await probeReadiness(`${endpoint}/ready`), true);
  assert.equal(await probeReadiness(`${endpoint}/failed`), false);
  const root = await mkdtemp(join(tmpdir(), 'fleet-readiness-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const started = Date.now();
  const hung = probeReadiness(`${endpoint}/hung`, 200);
  const heartbeat = await renderHeartbeat('bill', '', root, 1000);
  assert.match(heartbeat, /fleet_node_agent_heartbeat_timestamp_seconds\{node="bill"\} 1000/);
  assert.equal(await hung, false);
  assert.ok(Date.now() - started < 2000);
  assert.equal(await probeReadiness(`${endpoint}/hung-body`, 200), false);
});

test('thermal recognizes sensorless nominal output and exports unavailable rather than healthy on failure', () => {
  assert.deepEqual(parseThermal('Note: No thermal warning level has been recorded'), { level: 0, pressure: 'nominal' });
  assert.deepEqual(parseThermal('CPU_Scheduler_Limit = 100\nThermal Pressure: Serious'), { level: 2, pressure: 'serious' });
  const success = renderThermal('bill', 1000, 2, 'serious');
  assert.match(success, /fleet_macos_thermal_pressure_level\{node="bill",node_label="bill",source="pmset",pressure="serious"\} 2/);
  const failed = renderThermal('bill', 1001, null, 'unknown', 'unsupported');
  assert.match(failed, /fleet_macos_thermal_pressure_available\{node="bill",node_label="bill",source="pmset"\} 0/);
  assert.match(failed, /fleet_macos_thermal_collector_success\{node="bill",node_label="bill",source="pmset"\} 0/);
  assert.match(failed, /fleet_macos_thermal_collected_at_seconds\{node="bill",node_label="bill",source="pmset"\} 1001/);
  assert.match(failed, /error="unsupported"/);
});
