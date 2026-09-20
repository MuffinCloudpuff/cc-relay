import { request as httpRequest } from 'node:http';

import { afterEach, describe, expect, it, vi } from 'vitest';

import { CONTROL_BODY_LIMIT_BYTES, createControlServer, type RuntimeAccount } from '../runtime/control';

const token = 'synthetic-control-token';
const authorization = { authorization: `Bearer ${token}` };

interface ControlResponse {
  status: number;
  json(): Promise<unknown>;
}

function controlRequest(
  port: number,
  path: string,
  options: { method?: string; headers?: Record<string, string>; body?: string } = {},
): Promise<ControlResponse> {
  return new Promise((resolve, reject) => {
    const request = httpRequest(
      {
        host: '127.0.0.1',
        port,
        path,
        method: options.method ?? 'GET',
        headers: options.headers,
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on('data', (chunk: Buffer) => chunks.push(chunk));
        response.on('end', () => {
          const body = Buffer.concat(chunks).toString('utf8');
          resolve({
            status: response.statusCode ?? 0,
            json: async () => JSON.parse(body),
          });
        });
      },
    );
    request.once('error', reject);
    request.end(options.body);
  });
}

function account(id: string, email: string, active = false): RuntimeAccount {
  return {
    id,
    email,
    is_active: active,
    quota: { models: { 'gemini-test': { percentage: 61, resetTime: '2026-09-21T00:00:00Z' } } },
  };
}

describe('headless control service', () => {
  const stops: Array<() => Promise<void>> = [];

  afterEach(async () => {
    await Promise.all(stops.splice(0).map((stop) => stop()));
  });

  async function start(accounts: RuntimeAccount[] = []) {
    const setActive = vi.fn(async (id: string) => {
      await Promise.resolve();
      for (const candidate of accounts) {
        candidate.is_active = candidate.id === id;
      }
    });
    const refreshCurrent = vi.fn(async (current: RuntimeAccount) => current);
    const onStop = vi.fn();
    const control = createControlServer(token, {
      getAccounts: async () => accounts,
      setActive,
      refreshCurrent,
      getGatewayStatus: async () => ({ running: true, port: 8402, active_accounts: accounts.length }),
      getSchedulingMode: () => 'balance',
      getHeaderProfile: () => 'official',
      onStop,
    });
    const port = await control.start(0);
    stops.push(() => control.stop());
    return { port, setActive, refreshCurrent, onStop };
  }

  it('reports a healthy isolated empty account pool', async () => {
    const service = await start();
    const response = await controlRequest(service.port, '/status', { headers: authorization });

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      available: true,
      running: true,
      current_account: null,
      accounts_count: 0,
      gateway: { running: true, port: 8402, active_accounts: 0 },
      scheduling_mode: 'balance',
      header_profile: 'official',
      body_limit_bytes: 33554432,
    });
  });

  it('requires bearer authentication and rejects browser origins', async () => {
    const service = await start();

    expect((await controlRequest(service.port, '/status')).status).toBe(401);
    expect(
      (
        await controlRequest(service.port, '/status', {
          headers: { ...authorization, origin: 'http://localhost:3000' },
        })
      ).status,
    ).toBe(403);
  });

  it('matches the tray next-account rotation without touching scheduler state', async () => {
    const service = await start([account('a', 'a@example.test', true), account('b', 'b@example.test')]);
    const response = await controlRequest(service.port, '/action', {
      method: 'POST',
      headers: { ...authorization, 'content-type': 'application/json' },
      body: JSON.stringify({ action: 'next-account' }),
    });

    expect(response.status).toBe(200);
    expect(service.setActive).toHaveBeenCalledWith('b');
    await expect(response.json()).resolves.toMatchObject({
      ok: true,
      current_account: { id: 'b', email: 'b@example.test' },
    });
  });

  it('serializes concurrent next-account requests', async () => {
    const service = await start([account('a', 'a@example.test', true), account('b', 'b@example.test')]);
    const request = () =>
      controlRequest(service.port, '/action', {
        method: 'POST',
        headers: { ...authorization, 'content-type': 'application/json' },
        body: JSON.stringify({ action: 'next-account' }),
      });

    const [first, second] = await Promise.all([request(), request()]);

    expect(first.status).toBe(200);
    expect(second.status).toBe(200);
    expect(service.setActive.mock.calls.map(([id]) => id)).toEqual(['b', 'a']);
  });

  it('validates the action envelope and requires confirmed stop', async () => {
    const service = await start([account('a', 'a@example.test', true)]);
    const headers = { ...authorization, 'content-type': 'application/json' };

    expect(
      (
        await controlRequest(service.port, '/action', {
          method: 'POST',
          headers,
          body: JSON.stringify({ action: 'next-account', unexpected: true }),
        })
      ).status,
    ).toBe(400);
    expect(
      (
        await controlRequest(service.port, '/action', {
          method: 'POST',
          headers,
          body: JSON.stringify({ action: 'stop' }),
        })
      ).status,
    ).toBe(400);
    expect(
      (
        await controlRequest(service.port, '/action', {
          method: 'POST',
          headers,
          body: JSON.stringify({ action: 'stop', confirm: true }),
        })
      ).status,
    ).toBe(202);
    await vi.waitFor(() => expect(service.onStop).toHaveBeenCalledTimes(1));
  });

  it('rejects oversized action bodies before dispatch', async () => {
    const service = await start();
    const response = await controlRequest(service.port, '/action', {
      method: 'POST',
      headers: { ...authorization, 'content-type': 'application/json' },
      body: JSON.stringify({ action: 'next-account', padding: 'x'.repeat(CONTROL_BODY_LIMIT_BYTES) }),
    });

    expect(response.status).toBe(413);
  });
});
