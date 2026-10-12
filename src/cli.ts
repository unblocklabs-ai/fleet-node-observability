#!/usr/bin/env node
import { parseArgs } from 'node:util';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { assertNode24, defaultRoot, loadContext } from './config.js';
import { configureOpenClaw, initialize, packageVersion, validateLayout, validateRuntime } from './provision.js';
import { collectHeartbeat, collectReadiness, collectThermal } from './health.js';
import { collectCron } from './cron.js';
import { collectSessions } from './sessions.js';
import { collectCodex } from './codex.js';
import { collectComparison } from './comparison.js';
import { installDependencies, verifyDependencies } from './dependencies.js';
import { writeCollectorConfig } from './collector.js';
import { prepareServices, restartServices, serviceStatus, startServices, stopServices } from './services.js';
import { runCommand } from './process.js';

const help = `fleet-node — standalone macOS telemetry (Node 24.18+)

  init --config FILE --node PATH --npm-cli PATH --prefix PATH
       --openclaw PATH --codex PATH [--home PATH] [--openclaw-root PATH]
       (--token-file FILE | --authorization-file FILE) [--root PATH]
  prepare [--collector-archive FILE --exporter-archive FILE] [--root PATH]
  collect heartbeat|readiness|thermal|cron|sessions|codex|comparison [--root PATH]
  validate | status | stop | start | restart [--root PATH]
  configure-openclaw [--root PATH]
  --version | --help

init writes private external state; prepare downloads verified native dependencies and
generates a one-time sudo system-service installer. No automatic OpenClaw edits/restarts.
Updates: stop, ordinary npm install, validate, start. On failure leave stopped and
npm-install the previous package version. State and credentials are never in npm.
`;

async function main(): Promise<number> {
  process.umask(0o077);
  assertNode24();
  const { values, positionals } = parseArgs({ options: {
    help: { type: 'boolean' }, version: { type: 'boolean' }, root: { type: 'string' }, config: { type: 'string' }, node: { type: 'string' }, 'npm-cli': { type: 'string' }, prefix: { type: 'string' }, openclaw: { type: 'string' }, codex: { type: 'string' }, home: { type: 'string' }, 'openclaw-root': { type: 'string' }, 'token-file': { type: 'string' }, 'authorization-file': { type: 'string' }, 'collector-archive': { type: 'string' }, 'exporter-archive': { type: 'string' },
  }, allowPositionals: true });
  if (values.version) { console.log(await packageVersion()); return 0; }
  if (values.help || positionals.length === 0) { console.log(help); return 0; }
  const root = values.root ?? defaultRoot();
  const command = positionals[0];
  const required = (key: 'config' | 'node' | 'npm-cli' | 'prefix' | 'openclaw' | 'codex'): string => {
    const value = values[key]; if (!value) throw new Error(`--${key} is required`); return value;
  };
  if (command === 'init') {
    const home = values.home ?? homedir();
    await initialize({ root, configPath: required('config'), settings: { nodePath: required('node'), npmCliPath: required('npm-cli'), npmPrefix: required('prefix'), nodeHome: home, openclawRoot: values['openclaw-root'] ?? join(home, '.openclaw'), openclawBin: required('openclaw'), codexBin: required('codex') }, ...(values['token-file'] ? { tokenFile: values['token-file'] } : {}), ...(values['authorization-file'] ? { authorizationFile: values['authorization-file'] } : {}) });
    console.log(`Initialized ${root}; services are not installed or started.`); return 0;
  }
  const context = await loadContext(root);
  if (command === 'collect') {
    const collectors = { heartbeat: collectHeartbeat, readiness: collectReadiness, thermal: collectThermal, cron: collectCron, sessions: collectSessions, codex: collectCodex, comparison: collectComparison };
    const job = positionals[1];
    if (!job || !Object.hasOwn(collectors, job) || positionals.length !== 2) throw new Error('Unknown collector');
    if (job === 'codex' && !context.config.codex_usage_enabled) throw new Error('Codex usage is disabled');
    if (job === 'cron') {
      // Reuse the installed five-minute launchd schedule. A separate deadline-bound
      // child runs even if the cron source fails; no extra privileged service setup.
      const results = await Promise.allSettled([
        collectCron(context),
        runCommand(context.settings.nodePath, [fileURLToPath(import.meta.url), 'collect', 'comparison', '--root', context.root], { timeoutMs: 45000, maxBytes: 8192 }),
      ]);
      return results.some(result => result.status === 'rejected' || typeof result.value === 'number' && result.value !== 0) ? 1 : 0;
    }
    return collectors[job as keyof typeof collectors](context);
  }
  if (positionals.length !== 1) throw new Error('Unexpected positional arguments');
  if (command === 'prepare') {
    await validateRuntime(context.settings); await validateLayout(root, context.settings);
    await installDependencies(context, { ...(values['collector-archive'] ? { collectorArchive: values['collector-archive'] } : {}), ...(values['exporter-archive'] ? { nodeExporterArchive: values['exporter-archive'] } : {}) });
    await writeCollectorConfig(context);
    console.log(JSON.stringify(await prepareServices(context), null, 2)); return 0;
  }
  if (command === 'stop') { await stopServices(context); console.log('Maintenance gate closed; old jobs quiescent. Safe to run npm install.'); return 0; }
  if (command === 'status') { console.log(JSON.stringify({ version: await packageVersion(), ...await serviceStatus(context) }, null, 2)); return 0; }
  if (command === 'configure-openclaw') { console.log(JSON.stringify({ backup: await configureOpenClaw(context), restartRequired: true })); return 0; }
  if (['validate', 'start', 'restart'].includes(command ?? '')) {
    await validateRuntime(context.settings); await validateLayout(root, context.settings); await verifyDependencies(context);
    const collectorConfig = join(root, 'config/collector.json');
    await runCommand(join(root, 'bin/otelcol-contrib'), ['validate', `--config=file:${collectorConfig}`], { timeoutMs: 15000 });
    if (command === 'start') await startServices(context);
    if (command === 'restart') await restartServices(context);
    console.log(command === 'validate' ? 'Runtime, dependencies and Collector configuration valid.' : 'Maintenance gate open; launchd owns scheduling and supervision.'); return 0;
  }
  throw new Error('Unknown command; use --help');
}

try { process.exitCode = await main(); }
catch (error) { console.error(error instanceof Error ? error.message : 'fleet-node failed'); process.exitCode = 1; }
