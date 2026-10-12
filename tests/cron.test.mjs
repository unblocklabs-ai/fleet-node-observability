import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { collectCron, parseJobs, renderCron } from '../dist/cron.js';

test('cron JSON allows diagnostics but requires a jobs array of objects', () => {
  assert.deepEqual(parseJobs('banner\n {"jobs":[{"id":"a","name":"braces { quoted }"}]}\ntrailer'), [{ id: 'a', name: 'braces { quoted }' }]);
  assert.throws(() => parseJobs('banner only'), /invalid_json/);
  assert.throws(() => parseJobs('{"jobs":null}'), /invalid_payload/);
  assert.throws(() => parseJobs('{"jobs":[null]}'), /invalid_payload/);
});

test('cron grouping, state, precision and label privacy retain central contract', () => {
  const schedule = { kind: 'cron', expr: '0 9 * * *', tz: 'America/New_York' };
  const output = renderCron('bill', [
    { id: 'a', name: 'A', schedule, state: { lastRunAtMs: 1_799_998_123_456, nextRunAtMs: 1_800_000_000_000, runningAtMs: 1_799_999_000_000, lastDurationMs: 90123, consecutiveErrors: 2, lastRunStatus: 'error' } },
    { id: 'b', name: 'B', enabled: true, schedule },
    { id: 'c', name: 'Disabled', enabled: false, schedule },
    { id: 'd', name: 'Safe\nname\tcontrols', schedule: { kind: 'stream', command: 'token=secret' }, state: { lastStatus: 'private-error-message' } },
  ], 1_700_000_000);
  assert.match(output, /openclaw_cron_schedule_group_jobs\{[^\n]*cron_schedule="0 9 \* \* \*",cron_timezone="America\/New_York"\} 2/);
  assert.match(output, /openclaw_cron_schedule_jobs_total\{[^\n]*cron_enabled="true"\} 3/);
  assert.match(output, /openclaw_cron_schedule_jobs_total\{[^\n]*cron_enabled="false"\} 1/);
  assert.match(output, /openclaw_cron_job_last_run_timestamp_seconds\{[^\n]*\} 1799998123.456/);
  assert.match(output, /openclaw_cron_job_running_since_timestamp_seconds\{[^\n]*\} 1799999000.000/);
  assert.match(output, /cron_status="error",cron_running="true"/);
  assert.match(output, /cron_job_name="Safe name controls"/);
  assert.match(output, /cron_schedule="stream"/);
  assert.match(output, /cron_status="other"/);
  assert.ok(!output.includes('token=secret'));
  assert.ok(!output.includes('private-error-message'));
});

test('invalid run timestamps remain absent rather than epoch or nonfinite values', () => {
  for (const value of [undefined, 0, -1, true, '123', NaN, Infinity]) {
    const output = renderCron('bill', [{ id: 'a', state: { lastRunAtMs: value, runningAtMs: value, nextRunAtMs: value } }]);
    const samples = output.split('\n').filter(line => !line.startsWith('#'));
    assert.ok(!samples.some(line => /^openclaw_cron_job_(last_run|running_since|next_run)_timestamp/.test(line)));
  }
});

test('actual CLI subprocess runs in node home and missing binary replaces success atomically', async t => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'fleet-cron-test-')));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const executable = join(root, 'openclaw');
  writeFileSync(executable, `#!${process.execPath}\nimport assert from 'node:assert/strict';\nassert.deepEqual(process.argv.slice(2), ['cron','list','--all','--json']);\nassert.equal(process.cwd(), ${JSON.stringify(root)});\nconsole.log('diagnostics'); console.log(JSON.stringify({jobs:[{id:'real',name:'Real fixture'}]}));\n`, { mode: 0o700 });
  const context = { root: join(root, 'runtime'), config: { node_label: 'bill', config_schema_version: 3, telemetry_endpoint: 'https://example.test', codex_usage_enabled: false },
    settings: { nodePath: process.execPath, nodeHome: root, openclawRoot: join(root, 'openclaw-root'), openclawBin: executable, codexBin: '/missing/codex', npmCliPath: '/missing/npm', npmPrefix: join(root, 'npm') } };
  assert.equal(await collectCron(context), 0);
  const path = join(context.root, 'textfiles/openclaw_cron_schedule.prom');
  assert.match(readFileSync(path, 'utf8'), /cron_job_id="real"/);
  context.settings.openclawBin = join(root, 'does-not-exist');
  assert.equal(await collectCron(context), 1);
  const failure = readFileSync(path, 'utf8');
  assert.match(failure, /collector_success\{[^\n]*\} 0/);
  assert.match(failure, /error_type="binary_missing"/);
  assert.ok(!failure.includes('cron_job_id'));
  assert.ok(!failure.includes('does-not-exist'));
});
