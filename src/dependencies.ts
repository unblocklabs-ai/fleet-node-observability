import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { access, chmod, copyFile, lstat, mkdir, mkdtemp, open, rename, rm, unlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { promisify } from 'node:util';
import type { RuntimeContext } from './config.js';
import { ensurePrivateDir } from './io.js';

const execute = promisify(execFile);
export const COLLECTOR_VERSION = '0.157.0';
export const NODE_EXPORTER_VERSION = '1.11.1';
const checksums = {
  arm64: {
    collector: '6c03308935573712a795b4229f756bc4288bbbb13850604f3c7287868af84d4b',
    nodeExporter: 'e987428618362c2d2540a68b722bd982ef1486c9961631298f20ea8fd57d3be4',
  },
  x64: {
    collector: 'e11e7482144c3ac1eb1f612d3d175589435cad968a791d6ef5c73be43e1b8c34',
    nodeExporter: '782318ceb48cb5501271a666d1b015a9406c02cd45dbc9513deca005b91e03a5',
  },
} as const;

export function dependencyPaths(ctx: RuntimeContext) {
  return { collector: join(ctx.root, 'bin/otelcol-contrib'), nodeExporter: join(ctx.root, 'bin/node_exporter') };
}

async function digest(path: string): Promise<string> {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk as Buffer);
  return hash.digest('hex');
}

async function download(url: string, path: string): Promise<void> {
  const response = await fetch(url, { signal: AbortSignal.timeout(120_000) });
  if (!response.ok || !response.body) throw new Error(`Dependency download failed: HTTP ${response.status}`);
  let bytes = 0;
  const limit = new Transform({
    transform(chunk: Buffer, _encoding, callback) {
      bytes += chunk.length;
      callback(bytes > 256 * 1024 * 1024 ? new Error('Dependency archive exceeds 256 MiB') : null, chunk);
    },
  });
  await pipeline(Readable.fromWeb(response.body), limit, createWriteStream(path, { flags: 'wx', mode: 0o600 }));
}

async function verifyVersion(path: string, version: string): Promise<void> {
  const info = await lstat(path);
  if (!info.isFile() || info.isSymbolicLink()) throw new Error(`Dependency is not a regular file: ${path}`);
  const result = await execute(path, ['--version'], { timeout: 10_000, maxBuffer: 64 * 1024 });
  if (!new RegExp(`\\b${version.replaceAll('.', '\\.')}\\b`).test(result.stdout + result.stderr)) {
    throw new Error(`Dependency at ${path} is not pinned version ${version}`);
  }
}

export async function verifyDependencies(ctx: RuntimeContext): Promise<void> {
  const paths = dependencyPaths(ctx);
  await Promise.all([verifyVersion(paths.collector, COLLECTOR_VERSION), verifyVersion(paths.nodeExporter, NODE_EXPORTER_VERSION)]);
}

/** Installs verified upstream binaries, never Homebrew's moving latest version. */
export async function installDependencies(ctx: RuntimeContext, options: {
  collectorArchive?: string;
  nodeExporterArchive?: string;
} = {}): Promise<void> {
  if (process.platform !== 'darwin' || (process.arch !== 'arm64' && process.arch !== 'x64')) {
    throw new Error('Native dependencies support macOS arm64 and x64 only');
  }
  try {
    await access(join(ctx.root, 'services/enabled'));
    throw new Error('Stop services before installing native dependencies');
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
  }
  const arch = process.arch === 'x64' ? 'amd64' : 'arm64';
  const pinned = checksums[process.arch];
  const paths = dependencyPaths(ctx);
  const artifacts = [
    {
      name: 'otelcol-contrib', version: COLLECTOR_VERSION, target: paths.collector,
      url: `https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${COLLECTOR_VERSION}/otelcol-contrib_${COLLECTOR_VERSION}_darwin_${arch}.tar.gz`,
      sha256: pinned.collector, archive: options.collectorArchive, member: 'otelcol-contrib',
    },
    {
      name: 'node_exporter', version: NODE_EXPORTER_VERSION, target: paths.nodeExporter,
      url: `https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.darwin-${arch}.tar.gz`,
      sha256: pinned.nodeExporter, archive: options.nodeExporterArchive,
      member: `node_exporter-${NODE_EXPORTER_VERSION}.darwin-${arch}/node_exporter`,
    },
  ];
  await ensurePrivateDir(join(ctx.root, 'bin'));
  const temporary = await mkdtemp(join(tmpdir(), 'fleet-node-dependencies-'));
  await chmod(temporary, 0o700);
  try {
    for (const artifact of artifacts) {
      // An explicitly supplied archive always gets checked, even on a current install.
      if (!artifact.archive) {
        try { await verifyVersion(artifact.target, artifact.version); continue; } catch { /* replace a missing/wrong binary */ }
      }
      const archive = join(temporary, `${artifact.name}.tar.gz`);
      if (artifact.archive) {
        const info = await lstat(artifact.archive);
        if (!info.isFile() || info.isSymbolicLink()) throw new Error('Dependency archive must be a regular file');
        await copyFile(artifact.archive, archive);
        await chmod(archive, 0o600);
      } else {
        await download(artifact.url, archive);
      }
      if (await digest(archive) !== artifact.sha256) throw new Error(`${artifact.name} archive SHA256 mismatch`);
      const extracted = join(temporary, artifact.name + '-extract');
      await mkdir(extracted, { mode: 0o700 });
      await execute('/usr/bin/tar', ['-xzf', archive, '-C', extracted, artifact.member], { timeout: 30_000, maxBuffer: 64 * 1024 });
      const executable = join(extracted, artifact.member);
      await verifyVersion(executable, artifact.version);
      const staged = artifact.target + '.tmp-' + randomUUID();
      try {
        await copyFile(executable, staged);
        await chmod(staged, 0o700);
        const file = await open(staged, 'r');
        try { await file.sync(); } finally { await file.close(); }
        await rename(staged, artifact.target);
        const directory = await open(join(ctx.root, 'bin'), 'r');
        try { await directory.sync(); } finally { await directory.close(); }
      } finally {
        await unlink(staged).catch((error: unknown) => {
          if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
        });
      }
    }
  } finally {
    // Only the freshly-created, exact temporary directory is removed.
    await rm(temporary, { recursive: true, force: true });
  }
}
