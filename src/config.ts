import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve, sep } from 'node:path';
import { readRegular } from './io.js';

export interface NodeConfig {
  config_schema_version: 3;
  node_label: string;
  telemetry_endpoint: string;
  codex_usage_enabled: boolean;
}
export interface LocalSettings {
  nodePath: string;
  nodeHome: string;
  openclawRoot: string;
  openclawBin: string;
  codexBin: string;
  npmCliPath: string;
  npmPrefix: string;
}
export interface RuntimeContext { root: string; config: NodeConfig; settings: LocalSettings }

export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected JSON object');
  return value as Record<string, unknown>;
}
export function absolutePath(value: string): string {
  if (!isAbsolute(value) || value.split(sep).includes('..') || /[\r\n\0]/.test(value)) throw new Error('Expected absolute path without parent traversal or control characters');
  return resolve(value);
}
export function normalizeLabel(value: string): string {
  let label = value.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  if (/^\d/.test(label)) label = `node_${label}`;
  if (!/^[a-z][a-z0-9_]*$/.test(label)) throw new Error('Invalid node label');
  return label;
}
export function parseConfig(value: unknown): NodeConfig {
  const p = object(value);
  const expected = ['config_schema_version', 'node_label', 'telemetry_endpoint', 'codex_usage_enabled'];
  if (Object.keys(p).sort().join(',') !== expected.sort().join(',') || p.config_schema_version !== 3 || typeof p.codex_usage_enabled !== 'boolean' || typeof p.node_label !== 'string' || typeof p.telemetry_endpoint !== 'string') throw new Error('Expected exact schema-3 node configuration');
  const endpoint = p.telemetry_endpoint;
  const url = new URL(endpoint);
  if (endpoint !== endpoint.trim() || /[\s\\]/.test(endpoint) || url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname) || /[?#]/.test(endpoint) || /:\//.test(url.hostname) || /:\/?$/.test(endpoint) || url.port === '0') throw new Error('telemetry_endpoint must be an HTTPS base URL without credentials, path, query or fragment');
  return { config_schema_version: 3, node_label: normalizeLabel(p.node_label), telemetry_endpoint: endpoint.replace(/\/$/, ''), codex_usage_enabled: p.codex_usage_enabled };
}
export function parseSettings(value: unknown): LocalSettings {
  const p = object(value);
  const keys = ['nodePath', 'nodeHome', 'openclawRoot', 'openclawBin', 'codexBin', 'npmCliPath', 'npmPrefix'] as const;
  if (Object.keys(p).sort().join(',') !== [...keys].sort().join(',')) throw new Error('Invalid local runtime settings');
  const values = keys.map(key => {
    const v = p[key];
    if (typeof v !== 'string') throw new Error(`Missing local ${key}`);
    return [key, absolutePath(v)] as const;
  });
  return Object.fromEntries(values) as unknown as LocalSettings;
}
export function defaultRoot(): string { return join(homedir(), '.local/share/fleet-node'); }
export async function loadContext(root: string): Promise<RuntimeContext> {
  root = absolutePath(root);
  const config = parseConfig(JSON.parse(await readRegular(join(root, 'config/node.json'), 64 * 1024)) as unknown);
  const settings = parseSettings(JSON.parse(await readRegular(join(root, 'config/local.json'), 64 * 1024)) as unknown);
  return { root, config, settings };
}
export function runtimeEnv(settings: LocalSettings): NodeJS.ProcessEnv {
  return { HOME: settings.nodeHome, USER: settings.nodeHome.split('/').at(-1), LOGNAME: settings.nodeHome.split('/').at(-1), LANG: 'en_US.UTF-8', PATH: [dirname(settings.nodePath), dirname(settings.openclawBin), dirname(settings.codexBin), '/usr/bin', '/bin', '/usr/sbin', '/sbin'].join(':'), TMPDIR: process.env.TMPDIR ?? '/tmp' };
}
export function assertNode24(version = process.versions.node): void {
  const [major, minor] = version.split('.').map(Number);
  if (major !== 24 || minor === undefined || minor < 18) throw new Error('Explicit supported Node 24.18+ (below 25) is required');
}
