import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import {
  access,
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  writeFile,
} from 'node:fs/promises';
import { request as httpRequest } from 'node:http';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const binary = path.join(
  root,
  'node_modules/electron/dist',
  process.platform === 'win32' ? 'electron.exe' : 'electron',
);
await mkdir(path.join(root, 'results'), { recursive: true });
const directory = await mkdtemp(path.join(root, 'results/smoke-'));
const processes = new Set();
const report = {
  isolated: true,
  noRealAccounts: true,
  legacy_check_count: 6,
  key_timing_check_count: 2,
  checks: [],
};

async function freePort() {
  for (let attempt = 0; attempt < 40; attempt++) {
    const port = 20000 + Math.floor(Math.random() * 20000);
    const server = createServer();
    try {
      await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(port, '127.0.0.1', resolve);
      });
      await new Promise((resolve) => server.close(resolve));
      return port;
    } catch {
      server.close();
    }
  }
  throw new Error('unable-to-reserve-safe-loopback-port');
}

function requestLoopback({ port, path: requestPath, method = 'GET', headers, body, timeoutMs }) {
  return new Promise((resolve, reject) => {
    const request = httpRequest(
      { host: '127.0.0.1', port, path: requestPath, method, headers },
      (response) => {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(chunk));
        response.on('end', () =>
          resolve({ body: Buffer.concat(chunks).toString('utf8'), status: response.statusCode ?? 0 }),
        );
      },
    );
    request.once('error', reject);
    if (timeoutMs) {
      request.setTimeout(timeoutMs, () => request.destroy(new Error('loopback-request-timeout')));
    }
    request.end(body);
  });
}

function requestOversizedHeader({ port, path: requestPath, headers, contentLength, timeoutMs }) {
  return new Promise((resolve, reject) => {
    const request = httpRequest({
      host: '127.0.0.1',
      port,
      path: requestPath,
      method: 'POST',
      headers: { ...headers, 'content-length': String(contentLength), expect: '100-continue' },
    });
    request.once('response', (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () =>
        resolve({ body: Buffer.concat(chunks).toString('utf8'), status: response.statusCode ?? 0 }),
      );
    });
    // Node's HTTP server sends 100 Continue before Fastify receives the request.
    // One byte is enough for Fastify to inspect the declared content length and reject it.
    request.once('continue', () => request.write('{'));
    request.once('error', reject);
    request.setTimeout(timeoutMs, () => request.destroy(new Error('oversized-header-timeout')));
    request.flushHeaders();
  });
}

function electronEnvironment(overrides = {}) {
  const env = { ...process.env, ...overrides };
  delete env.ELECTRON_RUN_AS_NODE;
  return env;
}

function spawnElectron(args, env) {
  const child = spawn(binary, args, {
    cwd: root,
    env: electronEnvironment(env),
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let diagnostic = '';
  for (const stream of [child.stdout, child.stderr]) {
    stream.on('data', (chunk) => {
      diagnostic = (diagnostic + chunk).slice(-12000);
    });
  }
  let exited = false;
  const exitPromise = new Promise((resolve) => {
    child.once('exit', (code, signal) => {
      exited = true;
      resolve({ code, signal });
    });
  });
  const instance = {
    child,
    exitPromise,
    get diagnostic() {
      return diagnostic;
    },
    get exited() {
      return exited;
    },
  };
  processes.add(instance);
  return instance;
}

async function waitForExit(instance, timeoutMs, timeoutMessage) {
  return Promise.race([
    instance.exitPromise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(timeoutMessage)), timeoutMs).unref(),
    ),
  ]);
}

async function startCore({ dataDir, userDataDir, controlFile }) {
  const gatewayPort = await freePort();
  const controlPort = await freePort();
  const token = randomUUID();
  const apiKey = randomUUID();
  const instance = spawnElectron([root], {
    AGM_CORE_DATA_DIR: dataDir,
    AGM_CORE_USER_DATA: userDataDir,
    AGM_CORE_API_KEY: apiKey,
    AGM_CORE_CONTROL_TOKEN: token,
    AGM_CORE_PORT: String(gatewayPort),
    AGM_CORE_CONTROL_PORT: String(controlPort),
    AGM_CORE_CONTROL_FILE: controlFile,
    AGM_CORE_HEADER_PROFILE: 'official',
  });
  const auth = { authorization: `Bearer ${token}` };

  let status;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (instance.exited) {
      throw new Error(`core-exited-during-startup:${instance.diagnostic}`);
    }
    try {
      const response = await requestLoopback({
        port: controlPort,
        path: '/status',
        headers: auth,
        timeoutMs: 700,
      });
      if (response.status === 200) {
        status = JSON.parse(response.body);
        break;
      }
    } catch {
      // Startup has not bound the control listener yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!status) {
    throw new Error(`control-never-became-ready:${instance.diagnostic}`);
  }

  return {
    ...instance,
    apiKey,
    auth,
    controlFile,
    controlPort,
    gatewayPort,
    status,
    token,
  };
}

async function stopCore(instance) {
  const response = await requestLoopback({
    port: instance.controlPort,
    path: '/action',
    method: 'POST',
    headers: { ...instance.auth, 'content-type': 'application/json' },
    body: JSON.stringify({ action: 'stop', confirm: true }),
  });
  assert.equal(response.status, 202);
  const exit = await waitForExit(instance, 7000, 'core-shutdown-timeout');
  assert.equal(exit.code, 0);
}

async function writeSyntheticSafeStorageKey(userDataDir) {
  const fixture = path.join(directory, 'write-synthetic-safe-storage-key.cjs');
  await writeFile(
    fixture,
    `const { app, safeStorage } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const userData = process.env.SMOKE_USER_DATA;
try {
  fs.mkdirSync(userData, { recursive: true });
  app.setPath('userData', userData);
  app.setPath('sessionData', userData);
  app.whenReady().then(() => {
    fs.writeFileSync(path.join(userData, '.mk'), safeStorage.encryptString('a'.repeat(64)), { mode: 0o600 });
    if (!fs.existsSync(path.join(userData, 'Local State'))) throw new Error('missing-local-state');
    app.exit(0);
  }).catch((error) => { process.stderr.write(String(error)); app.exit(1); });
} catch (error) { process.stderr.write(String(error)); app.exit(1); }
`,
    'utf8',
  );
  const fixtureProcess = spawnElectron([fixture], { SMOKE_USER_DATA: userDataDir });
  const exit = await waitForExit(fixtureProcess, 7000, 'synthetic-key-fixture-timeout');
  assert.equal(exit.code, 0, fixtureProcess.diagnostic);
  await access(path.join(userDataDir, '.mk'));
  await access(path.join(userDataDir, 'Local State'));
}

async function copyImportedEncryptionProfile(sourceUserDataDir, targetUserDataDir) {
  await mkdir(targetUserDataDir, { recursive: true, mode: 0o700 });
  await Promise.all([
    copyFile(path.join(sourceUserDataDir, '.mk'), path.join(targetUserDataDir, '.mk')),
    copyFile(path.join(sourceUserDataDir, 'Local State'), path.join(targetUserDataDir, 'Local State')),
  ]);
}

async function exists(file) {
  try {
    await access(file);
    return true;
  } catch {
    return false;
  }
}

try {
  const initialDataDir = path.join(directory, 'initial-data');
  const initialUserDataDir = path.join(directory, 'initial-user-data');
  const initialControlFile = path.join(directory, 'initial-control.json');
  const initial = await startCore({
    dataDir: initialDataDir,
    userDataDir: initialUserDataDir,
    controlFile: initialControlFile,
  });

  assert.equal(initial.status.accounts_count, 0);
  assert.equal(initial.status.gateway.running, true);
  report.checks.push('original-gateway-started-with-isolated-empty-store');
  const manifest = JSON.parse(await readFile(initialControlFile, 'utf8'));
  assert.equal(manifest.token, initial.token);
  assert.equal(manifest.pid, initial.child.pid);
  report.checks.push('authenticated-control-manifest');
  assert.equal((await requestLoopback({ port: initial.controlPort, path: '/status' })).status, 401);
  assert.equal(
    (await requestLoopback({
      port: initial.controlPort,
      path: '/status',
      headers: { ...initial.auth, Origin: 'http://evil.invalid' },
    })).status,
    403,
  );
  report.checks.push('control-auth-and-origin-rejection');
  const request = {
    model: 'gemini-3.8-flash-high',
    max_tokens: 16,
    messages: [
      {
        role: 'user',
        content: [
          {
            type: 'image',
            source: { type: 'base64', media_type: 'image/png', data: 'AAAA'.repeat(400000) },
          },
        ],
      },
    ],
  };
  const largeBody = JSON.stringify(request);
  const large = await requestLoopback({
    port: initial.gatewayPort,
    path: '/v1/messages',
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'content-length': String(Buffer.byteLength(largeBody)),
      'x-api-key': initial.apiKey,
    },
    body: largeBody,
    timeoutMs: 15000,
  });
  assert.notEqual(large.status, 413);
  assert.notEqual(large.status, 401);
  assert.equal(large.status, 429);
  report.checks.push('greater-than-1MiB-reaches-original-service');
  const oversized = await requestOversizedHeader({
    port: initial.gatewayPort,
    path: '/v1/messages',
    headers: { 'content-type': 'application/json', 'x-api-key': initial.apiKey },
    contentLength: 32 * 1024 * 1024 + 1,
    timeoutMs: 15000,
  });
  assert.equal(oversized.status, 413);
  report.gateway_body_limit_result = 'http-413-before-upload';
  report.checks.push('32MiB-limit-enforced');
  await stopCore(initial);
  report.checks.push('confirmed-stop-shuts-down-core');

  // The first run creates the source profile's Local State. Add a deterministic
  // safeStorage-encrypted key, then copy exactly those two imported profile files.
  await writeSyntheticSafeStorageKey(initialUserDataDir);
  const restoredDataDir = path.join(directory, 'restored-data');
  const restoredUserDataDir = path.join(directory, 'restored-user-data');
  await copyImportedEncryptionProfile(initialUserDataDir, restoredUserDataDir);

  const restoredFirst = await startCore({
    dataDir: restoredDataDir,
    userDataDir: restoredUserDataDir,
    controlFile: path.join(directory, 'restored-first-control.json'),
  });
  assert.equal(restoredFirst.status.accounts_count, 0);
  await stopCore(restoredFirst);
  const restoredSecond = await startCore({
    dataDir: restoredDataDir,
    userDataDir: restoredUserDataDir,
    controlFile: path.join(directory, 'restored-second-control.json'),
  });
  assert.equal(restoredSecond.status.accounts_count, 0);
  await stopCore(restoredSecond);
  report.checks.push('copied-synthetic-key-and-local-state-restart-with-same-user-data');

  const importedKeyPath = path.join(restoredUserDataDir, '.mk');
  const databasePath = path.join(restoredDataDir, 'cloud_accounts.db');
  const databaseBefore = await readFile(databasePath);
  const corruptKey = Buffer.from('not-a-safe-storage-payload', 'utf8');
  await writeFile(importedKeyPath, corruptKey, { mode: 0o600 });
  const rejected = spawnElectron([root], {
    AGM_CORE_DATA_DIR: restoredDataDir,
    AGM_CORE_USER_DATA: restoredUserDataDir,
    AGM_CORE_API_KEY: randomUUID(),
    AGM_CORE_CONTROL_TOKEN: randomUUID(),
    AGM_CORE_PORT: String(await freePort()),
    AGM_CORE_CONTROL_PORT: String(await freePort()),
    AGM_CORE_CONTROL_FILE: path.join(directory, 'corrupt-key-control.json'),
    AGM_CORE_HEADER_PROFILE: 'official',
  });
  const rejectedExit = await waitForExit(rejected, 7000, 'corrupt-key-rejection-timeout');
  assert.equal(rejectedExit.code, 1);
  assert.match(rejected.diagnostic, /startup-failed:existing-encryption-key/);
  assert.deepEqual(await readFile(importedKeyPath), corruptKey);
  assert.deepEqual(await readFile(databasePath), databaseBefore);
  assert.equal(await exists(path.join(directory, 'corrupt-key-control.json')), false);
  assert.equal((await readdir(restoredUserDataDir)).some((name) => name.startsWith('.mk.bak.')), false);
  report.checks.push('corrupt-imported-key-fails-closed-without-mutation');
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.error = error instanceof Error ? error.message : String(error);
  process.exitCode = 1;
} finally {
  for (const instance of processes) {
    if (!instance.exited) {
      instance.child.kill();
      await instance.exitPromise;
    }
  }
  report.check_count = report.checks.length;
  await writeFile(path.join(root, 'results/runtime-smoke.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}
