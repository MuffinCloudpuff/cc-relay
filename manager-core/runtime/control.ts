import { timingSafeEqual } from 'node:crypto';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';

export const CONTROL_BODY_LIMIT_BYTES = 16 * 1024;
export const GATEWAY_BODY_LIMIT_BYTES = 32 * 1024 * 1024;

export interface RuntimeQuotaModel {
  percentage: number;
  resetTime?: string | null;
}

export interface RuntimeAccount {
  id: string;
  email: string;
  is_active?: boolean;
  quota?: { models?: Record<string, RuntimeQuotaModel> };
}

export interface GatewayStatus {
  running: boolean;
  port: number;
  active_accounts: number;
}

export interface ManagerStatus {
  available: true;
  running: true;
  current_account: null | {
    id: string;
    email: string;
    models: Array<{ name: string; percentage: number; reset_time: string | null }>;
  };
  accounts_count: number;
  gateway: GatewayStatus;
  scheduling_mode: string;
  header_profile: string;
  body_limit_bytes: number;
}

export interface ControlDependencies {
  getAccounts(): Promise<RuntimeAccount[]>;
  setActive(accountId: string): void | Promise<void>;
  refreshCurrent(account: RuntimeAccount): Promise<RuntimeAccount | null>;
  getGatewayStatus(): Promise<GatewayStatus>;
  getSchedulingMode(): string;
  getHeaderProfile(): string;
  onStop(): void | Promise<void>;
}

export interface ControlServer {
  start(port: number): Promise<number>;
  stop(): Promise<void>;
  getStatus(): Promise<ManagerStatus>;
}

type ActionRequest =
  | { action: 'next-account'; confirm?: boolean }
  | { action: 'refresh-current'; confirm?: boolean }
  | { action: 'stop'; confirm?: boolean };

function json(response: ServerResponse, statusCode: number, body: unknown): void {
  response.writeHead(statusCode, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  });
  response.end(JSON.stringify(body));
}

function safeError(response: ServerResponse, statusCode: number, error: string): void {
  json(response, statusCode, { error });
}

function isAuthenticated(request: IncomingMessage, token: string): boolean {
  const header = request.headers.authorization;
  if (!header || !header.startsWith('Bearer ')) {
    return false;
  }

  const supplied = Buffer.from(header.slice('Bearer '.length), 'utf8');
  const expected = Buffer.from(token, 'utf8');
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

function hasBrowserOrigin(request: IncomingMessage): boolean {
  const origin = request.headers.origin;
  return typeof origin === 'string' && origin.trim() !== '';
}

async function readJsonBody(request: IncomingMessage): Promise<unknown> {
  const contentLength = request.headers['content-length'];
  if (typeof contentLength === 'string' && Number(contentLength) > CONTROL_BODY_LIMIT_BYTES) {
    throw new RangeError('body-too-large');
  }

  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > CONTROL_BODY_LIMIT_BYTES) {
      throw new RangeError('body-too-large');
    }
    chunks.push(buffer);
  }

  if (size === 0) {
    throw new SyntaxError('empty-body');
  }

  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new SyntaxError('invalid-json');
  }
}

function parseAction(value: unknown): ActionRequest | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }

  const candidate = value as Record<string, unknown>;
  const keys = Object.keys(candidate);
  if (keys.some((key) => key !== 'action' && key !== 'confirm')) {
    return null;
  }
  if (
    (candidate.action !== 'next-account' &&
      candidate.action !== 'refresh-current' &&
      candidate.action !== 'stop') ||
    (candidate.confirm !== undefined && typeof candidate.confirm !== 'boolean')
  ) {
    return null;
  }

  return candidate as ActionRequest;
}

function toSafeAccount(account: RuntimeAccount | undefined): ManagerStatus['current_account'] {
  if (!account) {
    return null;
  }

  return {
    id: account.id,
    email: account.email,
    models: Object.entries(account.quota?.models ?? {}).map(([name, quota]) => ({
      name,
      percentage: quota.percentage,
      reset_time: quota.resetTime ?? null,
    })),
  };
}

export function createControlServer(token: string, dependencies: ControlDependencies): ControlServer {
  if (token.trim() === '') {
    throw new Error('AGM_CORE_CONTROL_TOKEN is required');
  }

  const getStatus = async (): Promise<ManagerStatus> => {
    const [accounts, gateway] = await Promise.all([
      dependencies.getAccounts(),
      dependencies.getGatewayStatus(),
    ]);
    const current = accounts.find((account) => account.is_active);

    return {
      available: true,
      running: true,
      current_account: toSafeAccount(current),
      accounts_count: accounts.length,
      gateway: {
        running: gateway.running,
        port: gateway.port,
        active_accounts: gateway.active_accounts,
      },
      scheduling_mode: dependencies.getSchedulingMode(),
      header_profile: dependencies.getHeaderProfile(),
      body_limit_bytes: GATEWAY_BODY_LIMIT_BYTES,
    };
  };

  // Account selection and refresh mutate the same original repository state.
  // Serializing control actions prevents concurrent tabs from selecting the same next account.
  let actionQueue: Promise<void> = Promise.resolve();
  const serializeAction = <T>(operation: () => Promise<T>): Promise<T> => {
    const result = actionQueue.then(operation, operation);
    actionQueue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  };

  const server = createServer(async (request, response) => {
    if (!isAuthenticated(request, token)) {
      response.setHeader('www-authenticate', 'Bearer');
      safeError(response, 401, 'unauthorized');
      return;
    }

    if (hasBrowserOrigin(request)) {
      safeError(response, 403, 'browser-origin-rejected');
      return;
    }

    if (request.method === 'GET' && request.url === '/status') {
      try {
        json(response, 200, await getStatus());
      } catch {
        safeError(response, 503, 'manager-runtime-unavailable');
      }
      return;
    }

    if (request.method !== 'POST' || request.url !== '/action') {
      safeError(response, 404, 'not-found');
      return;
    }

    if (!request.headers['content-type']?.toLowerCase().startsWith('application/json')) {
      safeError(response, 415, 'json-required');
      return;
    }

    let action: ActionRequest | null;
    try {
      action = parseAction(await readJsonBody(request));
    } catch (error) {
      safeError(response, error instanceof RangeError ? 413 : 400, 'invalid-action-request');
      return;
    }
    if (!action) {
      safeError(response, 400, 'invalid-action-request');
      return;
    }

    try {
      await serializeAction(async () => {
      if (action.action === 'next-account') {
        const accounts = await dependencies.getAccounts();
        if (accounts.length === 0) {
          json(response, 200, { ok: true, current_account: null });
          return;
        }

        const currentIndex = accounts.findIndex((account) => account.is_active);
        const next = accounts[(currentIndex + 1 + accounts.length) % accounts.length];
        await dependencies.setActive(next.id);
        json(response, 200, { ok: true, current_account: toSafeAccount({ ...next, is_active: true }) });
        return;
      }

      if (action.action === 'refresh-current') {
        const current = (await dependencies.getAccounts()).find((account) => account.is_active);
        if (!current) {
          json(response, 200, { ok: true, current_account: null });
          return;
        }

        json(response, 200, {
          ok: true,
          current_account: toSafeAccount((await dependencies.refreshCurrent(current)) ?? current),
        });
        return;
      }

      if (!action.confirm) {
        safeError(response, 400, 'stop-confirmation-required');
        return;
      }

      response.once('finish', () => {
        void Promise.resolve(dependencies.onStop()).catch(() => undefined);
      });
      json(response, 202, { ok: true });
      });
    } catch {
      safeError(response, 503, 'manager-runtime-unavailable');
    }
  });

  return {
    async start(port: number): Promise<number> {
      await new Promise<void>((resolve, reject) => {
        const onError = (error: Error) => {
          server.off('listening', onListening);
          reject(error);
        };
        const onListening = () => {
          server.off('error', onError);
          resolve();
        };
        server.once('error', onError);
        server.once('listening', onListening);
        server.listen({ host: '127.0.0.1', port });
      });
      const address = server.address();
      if (!address || typeof address === 'string') {
        throw new Error('control service did not bind a TCP port');
      }
      return address.port;
    },
    async stop(): Promise<void> {
      if (!server.listening) {
        return;
      }
      await new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
    },
    getStatus,
  };
}
