import { chmod, link, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { app, safeStorage } from 'electron';

interface RuntimeEnvironment {
  dataDir: string;
  userDataDir: string;
  apiKey: string;
  controlToken: string;
  port: number;
  controlPort: number;
  controlFile?: string;
  headerProfile: 'official' | 'manager';
}

interface ControlManifest {
  url: string;
  token: string;
  pid: number;
}

let startupStage = 'configuration';

function requiredAbsolutePath(name: string): string {
  const value = process.env[name]?.trim();
  if (!value || !path.isAbsolute(value)) {
    throw new Error(`${name} must be an absolute path`);
  }
  return path.resolve(value);
}

function requiredSecret(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

function readPort(name: string, fallback: number): number {
  const value = process.env[name]?.trim();
  if (!value) {
    return fallback;
  }
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`${name} must be a TCP port`);
  }
  return port;
}

function readEnvironment(): RuntimeEnvironment {
  const profile = process.env.AGM_CORE_HEADER_PROFILE?.trim() || 'official';
  if (profile !== 'official' && profile !== 'manager') {
    throw new Error('AGM_CORE_HEADER_PROFILE must be official or manager');
  }

  const controlFile = process.env.AGM_CORE_CONTROL_FILE?.trim();
  if (controlFile && !path.isAbsolute(controlFile)) {
    throw new Error('AGM_CORE_CONTROL_FILE must be an absolute path');
  }

  return {
    dataDir: requiredAbsolutePath('AGM_CORE_DATA_DIR'),
    userDataDir: requiredAbsolutePath('AGM_CORE_USER_DATA'),
    apiKey: requiredSecret('AGM_CORE_API_KEY'),
    controlToken: requiredSecret('AGM_CORE_CONTROL_TOKEN'),
    port: readPort('AGM_CORE_PORT', 8402),
    controlPort: readPort('AGM_CORE_CONTROL_PORT', 18446),
    controlFile: controlFile ? path.resolve(controlFile) : undefined,
    headerProfile: profile,
  };
}

function parseManifest(value: string): ControlManifest | null {
  try {
    const candidate = JSON.parse(value) as Record<string, unknown>;
    if (
      typeof candidate.url !== 'string' ||
      typeof candidate.token !== 'string' ||
      !Number.isInteger(candidate.pid) ||
      (candidate.pid as number) < 1
    ) {
      return null;
    }
    return candidate as ControlManifest;
  } catch {
    return null;
  }
}

function isProcessActive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== 'ESRCH';
  }
}

async function assertManifestAvailable(file: string | undefined): Promise<void> {
  if (!file) {
    return;
  }

  let existing: string;
  try {
    existing = await readFile(file, 'utf8');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return;
    }
    throw error;
  }

  const manifest = parseManifest(existing);
  if (!manifest) {
    throw new Error('AGM_CORE_CONTROL_FILE already exists with invalid contents');
  }
  if (isProcessActive(manifest.pid)) {
    throw new Error('AGM_CORE_CONTROL_FILE is owned by an active runtime');
  }
  throw new Error('AGM_CORE_CONTROL_FILE contains a stale runtime manifest');
}

async function writeRestrictedManifest(file: string, port: number, token: string): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.tmp.${process.pid}.${Date.now()}`;
  try {
    await writeFile(
      temporary,
      JSON.stringify({ url: `http://127.0.0.1:${port}`, token, pid: process.pid }),
      { encoding: 'utf8', mode: 0o600 },
    );
    await chmod(temporary, 0o600).catch(() => undefined);
    // link() is exclusive: a concurrent or newer runtime cannot be overwritten.
    await link(temporary, file);
    await chmod(file, 0o600).catch(() => undefined);
  } finally {
    await rm(temporary, { force: true }).catch(() => undefined);
  }
}

async function removeOwnedManifest(file: string | undefined, token: string): Promise<void> {
  if (!file) {
    return;
  }

  try {
    const manifest = parseManifest(await readFile(file, 'utf8'));
    if (manifest?.pid === process.pid && manifest.token === token) {
      await rm(file, { force: true });
    }
  } catch {
    // Cleanup must never delete a manifest that cannot be proven to be ours.
  }
}

async function run(): Promise<void> {
  startupStage = 'isolated-paths';
  const environment = readEnvironment();
  // No asynchronous work may precede this: Electron initializes OSCrypt at readiness.
  mkdirSync(environment.dataDir, { recursive: true, mode: 0o700 });
  mkdirSync(environment.userDataDir, { recursive: true, mode: 0o700 });
  app.setPath('userData', environment.userDataDir);
  app.setPath('sessionData', environment.userDataDir);
  process.env.AGM_CORE_DATA_DIR = environment.dataDir;
  process.env.AGM_CORE_HEADER_PROFILE = environment.headerProfile;
  await assertManifestAvailable(environment.controlFile);

  startupStage = 'electron-ready';
  await app.whenReady();

  // The original development-mode fallback rotates unreadable keys. Never invoke it
  // on an imported store: validate first and leave the encrypted key untouched.
  startupStage = 'existing-encryption-key';
  let existingKey: Buffer | undefined;
  try {
    existingKey = await readFile(path.join(environment.userDataDir, '.mk'));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  if (existingKey) {
    if (!safeStorage.isEncryptionAvailable()) throw new Error('secure storage unavailable');
    const key = safeStorage.decryptString(existingKey);
    if (!/^[a-f0-9]{64}$/i.test(key)) throw new Error('invalid existing encryption key');
  }

  startupStage = 'runtime-services';
  const { startHeadlessRuntime } = await import('./headless');
  let runtime: Awaited<ReturnType<typeof startHeadlessRuntime>> | undefined;
  let shuttingDown = false;
  const shutdown = async () => {
    if (shuttingDown) {
      return;
    }
    shuttingDown = true;
    await runtime?.stop();
    await removeOwnedManifest(environment.controlFile, environment.controlToken);
  };

  runtime = await startHeadlessRuntime({
    ...environment,
    requestQuit: async () => {
      await shutdown();
      app.quit();
    },
  });

  if (environment.controlFile) {
    startupStage = 'control-manifest';
    try {
      await writeRestrictedManifest(environment.controlFile, runtime.controlPort, environment.controlToken);
    } catch (error) {
      await shutdown();
      throw error;
    }
  }

  app.on('before-quit', (event) => {
    if (!shuttingDown) {
      event.preventDefault();
      void shutdown().finally(() => app.exit(0));
    }
  });
  app.on('will-quit', () => {
    void removeOwnedManifest(environment.controlFile, environment.controlToken);
  });
}

void run().catch(() => {
  process.stderr.write(`[manager-core] startup-failed:${startupStage}\n`);
  app.exit(1);
});
