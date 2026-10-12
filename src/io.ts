import { constants } from 'node:fs';
import { open, mkdir, lstat, rename, unlink, chmod } from 'node:fs/promises';
import { dirname, basename, join } from 'node:path';
import { randomUUID } from 'node:crypto';

export function isErrno(error: unknown, code: string): boolean {
  return error instanceof Error && 'code' in error && error.code === code;
}

export async function ensurePrivateDir(path: string): Promise<void> {
  await mkdir(path, { recursive: true, mode: 0o700 });
  const st = await lstat(path);
  if (!st.isDirectory() || st.isSymbolicLink() || st.uid !== process.getuid?.()) throw new Error('Private directory must be owned by this account and not a symlink');
  await chmod(path, 0o700);
}

export async function syncDirectory(path: string): Promise<void> {
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

export async function writeAtomic(path: string, content: string | Uint8Array, mode = 0o600): Promise<void> {
  await ensurePrivateDir(dirname(path));
  try {
    const st = await lstat(path);
    if (!st.isFile() || st.isSymbolicLink() || st.uid !== process.getuid?.()) throw new Error('Refusing to replace nonregular or foreign-owned file');
  } catch (error) { if (!isErrno(error, 'ENOENT')) throw error; }
  const temporary = join(dirname(path), `.${basename(path)}.${randomUUID()}.tmp`);
  const handle = await open(temporary, 'wx', mode);
  try {
    await handle.writeFile(content);
    await handle.chmod(mode);
    await handle.sync();
    await handle.close();
    await rename(temporary, path);
    await syncDirectory(dirname(path));
  } finally {
    await handle.close().catch(() => {});
    await unlink(temporary).catch(error => { if (!isErrno(error, 'ENOENT')) throw error; });
  }
}

export async function writeExclusive(path: string, content: string | Uint8Array): Promise<void> {
  await ensurePrivateDir(dirname(path));
  const handle = await open(path, 'wx', 0o600);
  try { await handle.writeFile(content); await handle.sync(); } finally { await handle.close(); }
  await syncDirectory(dirname(path));
}

export async function readRegular(path: string, maxBytes: number, privateFile = false): Promise<string> {
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.size > maxBytes) throw new Error('Input must be a bounded regular file');
    if (privateFile && ((before.mode & 0o077) !== 0 || before.uid !== process.getuid?.())) throw new Error('Credential must be private and owned by this account');
    const buffer = Buffer.alloc(maxBytes + 1);
    let size = 0;
    while (size <= maxBytes) {
      const next = await handle.read(buffer, size, buffer.length - size, null);
      if (next.bytesRead === 0) break;
      size += next.bytesRead;
    }
    const after = await handle.stat();
    const current = await lstat(path);
    if (size > maxBytes || before.ino !== after.ino || before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs || after.ino !== current.ino || after.dev !== current.dev || after.mtimeMs !== current.mtimeMs) throw new Error('Input changed while reading');
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, size));
  } finally { await handle.close(); }
}

export function labels(values: Readonly<Record<string, string | number | boolean>>): string {
  return `{${Object.entries(values).map(([key, value]) => `${key}="${String(value).replace(/[\u0000-\u001f\u007f\\"]/g, c => ({ '\\': '\\\\', '"': '\\"', '\n': '\\n' }[c] ?? ' '))}"`).join(',')}}`;
}

export function metric(name: string, help: string, value: number | string, values: Readonly<Record<string, string | number | boolean>>): string {
  return `# HELP ${name} ${help}\n# TYPE ${name} gauge\n${name}${labels(values)} ${value}\n`;
}
