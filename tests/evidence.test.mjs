import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, writeFile, appendFile, readFile, rm, chmod, rename } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';

// Owns the native-file -> packaged CLI -> durable outbox -> OTLP boundary.
// Synthetic private strings must never cross it; no real transcript fixtures.
test('evidence subprocess is opt-in, private, replayable, and resumes partial records and rejected exports', async t => {
  const root = await mkdtemp(join(tmpdir(), 'fleet-evidence-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const sessions = join(root, 'openclaw/agents/main/agent/codex-home/sessions');
  const native = join(sessions, new Date().toISOString().slice(0, 10).replaceAll('-', '/'));
  await mkdir(native, { recursive: true });
  await mkdir(join(root, 'config'), { recursive: true });
  await writeFile(join(root, 'config/node.json'), JSON.stringify({ config_schema_version: 3, node_label: 'bill', telemetry_endpoint: 'https://example.test', codex_usage_enabled: false }));
  await writeFile(join(root, 'config/local.json'), JSON.stringify({ nodePath: process.execPath, nodeHome: root, openclawRoot: join(root, 'openclaw'), openclawBin: '/missing/openclaw', codexBin: '/missing/codex', npmCliPath: '/missing/npm', npmPrefix: join(root, 'npm') }));
  const run = () => new Promise((done, reject) => {
    const child = spawn(process.execPath, [process.env.FLEET_EVIDENCE_CLI ?? resolve('dist/cli.js'), 'collect', 'evidence', '--root', root], { stdio: ['ignore', 'pipe', 'pipe'] });
    let output = ''; child.stdout.on('data', b => output += b); child.stderr.on('data', b => output += b);
    child.on('error', reject); child.on('close', code => { assert.ok(!output.includes('PRIVATE')); done(code); });
  });
  const now = Date.now() - 5000;
  const line = (id, phase, item = {}) => JSON.stringify({ timestamp: new Date(now + (phase === 'completed' ? 10 : 0)).toISOString(), payload: {
    type: `item_${phase}`, thread_id: 'same-thread', turn_id: 'same-turn', started_at_ms: now, completed_at_ms: phase === 'completed' ? now + 10 : undefined,
    item: { id, type: 'DynamicToolCall', tool: 'read_file', namespace: 'functions', status: 'failed', success: false, arguments: 'PRIVATE ARGUMENT', content_items: [{ type: 'inputText', text: 'OpenClaw did not register a handler for this app-server tool call.' }], ...item },
  } });
  const path = join(native, 'session.jsonl');
  const meta = JSON.stringify({ type: 'session_meta', payload: { cli_version: '1.2.3', base_instructions: { text: 'PRIVATE INSTRUCTIONS' } } }) + '\n';
  const initial = meta + line('a', 'started') + '\n' + line('b', 'completed') + '\n';
  await writeFile(path, initial);
  assert.equal(await run(), 0); // opt-out makes no state and no network requests
  await assert.rejects(readFile(join(root, 'state/evidence/evidence.sqlite')), { code: 'ENOENT' });
  await writeFile(join(root, 'config/evidence.enabled'), 'v1\n', { mode: 0o600 });
  await chmod(join(root, 'config/evidence.enabled'), 0o600);
  // Replay a schema-2 checkpoint that previously skipped a spanning record,
  // without deleting accepted history or carrying forward its dropping cursor.
  await mkdir(join(root, 'state/evidence'), { recursive: true, mode: 0o700 });
  const legacy = new DatabaseSync(join(root, 'state/evidence/evidence.sqlite'));
  legacy.exec("CREATE TABLE files(path TEXT PRIMARY KEY,agent TEXT,offset INTEGER DEFAULT 0,inode TEXT DEFAULT '',anchor TEXT DEFAULT '',dropping INTEGER DEFAULT 0,checked INTEGER DEFAULT 0,day TEXT NOT NULL DEFAULT '',visited INTEGER NOT NULL DEFAULT 0,harness_version TEXT NOT NULL DEFAULT 'unknown',instruction_hash TEXT); CREATE TABLE sent(key TEXT PRIMARY KEY,accepted_ms INTEGER); PRAGMA user_version=2;");
  legacy.prepare('INSERT INTO files(path,agent,offset,dropping) VALUES (?,?,?,?)').run(path, 'main', Buffer.byteLength(initial), 1);
  legacy.prepare('INSERT INTO sent VALUES (?,?)').run('retained-history', Date.now());
  legacy.close();
  await chmod(join(root, 'state/evidence/evidence.sqlite'), 0o600);
  const packets = []; let rejectBatch = true;
  const metrics = createServer((req,res) => res.end('otelcol_exporter_queue_size{data_type="logs",exporter="otlp_http/logs"} 0\notelcol_exporter_send_failed_log_records{exporter="otlp_http/logs"} 2\notelcol_receiver_refused_log_records{receiver="otlp/openclaw",transport="http"} 0\n'));
  await new Promise((resolve,reject)=>{metrics.once('error',reject);metrics.listen(8888,'127.0.0.1',resolve)});
  t.after(()=>new Promise(resolve=>metrics.close(resolve)));
  const server = createServer(async (req, res) => {
    assert.equal(req.url, '/v1/logs'); let body = ''; for await (const chunk of req) body += chunk;
    assert.ok(!body.includes('PRIVATE')); assert.ok(!body.includes('same-thread'));
    const logs = JSON.parse(body).resourceLogs[0].scopeLogs[0].logRecords;
    packets.push(...logs.map(log => JSON.parse(log.attributes[0].value.stringValue)));
    res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(rejectBatch ? { partialSuccess: { rejectedLogRecords: '1' } } : {}));
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(4318, '127.0.0.1', resolve); });
  t.after(() => new Promise(resolve => server.close(resolve)));
  assert.equal(await run(), 1);
  assert.equal(packets.find(p=>p.kind==='coverage').transport.otelcol_exporter_send_failed_log_records,2);
  const inspect = () => new DatabaseSync(join(root, 'state/evidence/evidence.sqlite'), { readOnly: true });
  let db = inspect(); assert.ok(db.prepare('SELECT count(*) n FROM outbox').get().n > 0); db.close();
  rejectBatch = false;
  const end = line('a', 'completed');
  await appendFile(path, end.slice(0, 80));
  assert.equal(await run(), 0);
  assert.equal(packets.filter(p => p.kind === 'observation' && p.phase === 'completed').length, 2); // rejected + replayed b
  await appendFile(path, end.slice(80) + '\n');
  assert.equal(await run(), 0);
  const observations = packets.filter(p => p.kind === 'observation');
  const aStart = observations.find(p => p.phase === 'started');
  assert.ok(observations.some(p => p.phase === 'completed' && p.attempt_id === aStart.attempt_id));
  assert.equal(new Set(observations.map(p => p.attempt_id)).size, 2);
  assert.equal(observations.find(p => p.phase === 'completed').duration_ms, 10);
  assert.deepEqual(observations.find(p => p.phase === 'completed').signals, ['missing_handler']);
  assert.equal(observations.find(p => p.phase === 'completed').harness_version, '1.2.3');
  assert.match(observations.find(p => p.phase === 'completed').instruction_hash, /^[a-f0-9]{64}$/);
  const prior = packets.length;
  await writeFile(path + '.replacement', initial + end + '\n' + line('search', 'completed', { type: 'CommandExecution', command: ['/bin/zsh', '-lc', 'rg PRIVATE PARAMETER'], exit_code: 1, aggregated_output: '', content_items: [] }) + '\n');
  await rename(path + '.replacement', path);
  assert.equal(await run(), 0);
  const newObservations = packets.slice(prior).filter(p => p.kind === 'observation');
  assert.equal(newObservations.length, 1);
  assert.deepEqual(newObservations[0].signals, []);
  assert.equal(newObservations[0].executable, 'rg');
  assert.equal(newObservations[0].diagnostics.output_present, false);
  db = inspect(); assert.equal(db.prepare('SELECT count(*) n FROM outbox').get().n, 0); db.close();
  assert.ok(!(await readFile(join(root, 'state/evidence/evidence.sqlite'))).includes(Buffer.from('PRIVATE')));
  // A large older inventory must not hide new live receipts until backfill ends.
  const old = join(sessions, '2020/01/01');
  await mkdir(old, { recursive: true });
  const ignored = JSON.stringify({ payload: { type: 'user_message', text: 'PRIVATE '.repeat(9000) } }) + '\n';
  for (let i = 0; i < 70; i++) await writeFile(join(old, `${i}.jsonl`), ignored.repeat(8));
  await appendFile(path, line('novel', 'completed', { content_items: [{ text: 'TypeError: PRIVATE bearer sk-PRIVATE\nECONNRESET PRIVATE\ninvalid arguments: PRIVATE' }] }) + '\n');
  await appendFile(path, line('http429', 'completed', { content_items: [{ text: 'Traceback (most recent call last):\nPRIVATE request(timeout=30)\nrequests.exceptions.HTTPError: 429 Client Error: PRIVATE' }] }) + '\n');
  await appendFile(path, line('composed-search', 'completed', { type: 'CommandExecution', command: ['/bin/zsh', '-lc', 'rg PRIVATE; python PRIVATE'], exit_code: 1, aggregated_output: '', content_items: [] }) + '\n');
  const beforeBackfill = packets.length;
  assert.equal(await run(), 0);
  const fresh = packets.slice(beforeBackfill).find(p => p.kind === 'observation' && p.diagnostics.error_types.includes('TypeError'));
  assert.deepEqual(fresh.diagnostics.error_types, ['TypeError']);
  assert.deepEqual(fresh.diagnostics.codes, ['ECONNRESET']);
  assert.deepEqual(fresh.diagnostics.markers, ['invalid_arguments']);
  assert.equal(fresh.harness_version, '1.2.3'); // context survives offset resumption
  const http = packets.slice(beforeBackfill).find(p => p.http_status === 429);
  assert.deepEqual(http.diagnostics.error_types, ['HTTPError']);
  assert.deepEqual(http.signals, []); // timeout parameter is not a timeout outcome
  assert.ok(packets.slice(beforeBackfill).some(p => p.kind === 'observation' && p.population === 'command_execution' && p.executable === 'other'));
  assert.ok(packets.slice(beforeBackfill).some(p => p.kind === 'coverage' && p.pending_files > 0));
  // A real record may span many I/O chunks. Its tail and ninth text item are
  // evidence, not optional excerpts; a partial record must wait without loss.
  const large = line('large-tail', 'completed', { aggregated_output: 'PRIVATE🙂\u2028\u2029\n'.repeat(70000) + 'FileNotFoundError: PRIVATE\n',
    content_items: [...Array.from({ length: 8 }, () => ({ text: 'PRIVATE' })), { text: 'invalid arguments: PRIVATE' }] });
  const beforeLarge = packets.length;
  await appendFile(path, large.slice(0, 600000));
  assert.equal(await run(), 0);
  assert.equal(packets.slice(beforeLarge).filter(p => p.kind === 'observation').length, 0);
  await appendFile(path, large.slice(600000) + '\n');
  assert.equal(await run(), 0);
  const recovered = packets.slice(beforeLarge).filter(p => p.kind === 'observation');
  assert.equal(recovered.length, 1);
  assert.deepEqual(recovered[0].diagnostics.error_types, ['FileNotFoundError']);
  assert.deepEqual(recovered[0].diagnostics.markers, ['invalid_arguments']);
  assert.equal(recovered[0].diagnostics.truncated, false);
  assert.equal(recovered[0].schema, 4);
  assert.equal(recovered[0].diagnostics.inspection, 'inspected');
  const diagnosticStart = packets.length;
  await appendFile(path, line('shell-error', 'completed', { aggregated_output: 'zsh:1: command not found: PRIVATE\nInvalid Context 0: PRIVATE\nerror: unrecognized arguments: PRIVATE', content_items: [] }) + '\n'
    + line('empty-failure', 'completed', { aggregated_output: '', content_items: [] }) + '\n'
    + line('success-uninspected', 'completed', { status: 'completed', success: true, content_items: [{text:'PRIVATE'}] }) + '\n');
  assert.equal(await run(), 0);
  const inspected = packets.slice(diagnosticStart).filter(p => p.kind === 'observation');
  assert.deepEqual(inspected.find(p=>p.diagnostics.markers.length).diagnostics.markers, ['command_not_found','cli_usage_error','patch_context_mismatch']);
  assert.equal(inspected.find(p=>p.outcome === 'success').diagnostics.inspection, 'not_inspected');
  assert.ok(inspected.some(p=>p.diagnostics.inspection === 'empty'));
  db = inspect();
  assert.equal(db.prepare('PRAGMA user_version').get().user_version, 4);
  assert.equal(db.prepare("SELECT value FROM counters WHERE key='unsupported'").get()?.value ?? 0, 0);
  assert.equal(db.prepare("SELECT value FROM counters WHERE key='parse_errors'").get()?.value ?? 0, 0);
  assert.ok(db.prepare('SELECT 1 FROM sent WHERE key=?').get('retained-history'));
  db.close();
  if (process.env.FLEET_EVIDENCE_FIXTURE) await writeFile(process.env.FLEET_EVIDENCE_FIXTURE, JSON.stringify(packets));
});
