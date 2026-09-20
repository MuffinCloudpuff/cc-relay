import path from 'node:path';

import {
  createControlServer,
  type ControlServer,
  type GatewayStatus,
  type RuntimeAccount,
} from './control';

interface OriginalServices {
  ConfigManager: {
    loadConfig(): { proxy: Record<string, unknown> & { port: number; api_key: string; scheduling_mode?: string } };
  };
  CloudAccountRepo: {
    init(): Promise<void>;
    getAccounts(): Promise<RuntimeAccount[]>;
    setActive(id: string): void;
  };
  refreshAccountQuota(accountId: string): Promise<RuntimeAccount>;
  bootstrapNestServer(config: Record<string, unknown>): Promise<{ success: boolean; port: number }>;
  stopNestServer(): Promise<boolean>;
  getNestServerStatus(): Promise<GatewayStatus>;
  getAgentDir(): string;
}

export interface HeadlessRuntimeOptions {
  dataDir: string;
  apiKey: string;
  controlToken: string;
  port: number;
  controlPort: number;
  controlFile?: string;
  headerProfile: 'official' | 'manager';
  requestQuit(): void | Promise<void>;
}

export interface HeadlessRuntime {
  controlPort: number;
  stop(): Promise<void>;
}

function samePath(left: string, right: string): boolean {
  const normalize = (value: string) => path.resolve(value).replace(/[\\/]+$/, '');
  return process.platform === 'win32'
    ? normalize(left).toLowerCase() === normalize(right).toLowerCase()
    : normalize(left) === normalize(right);
}

async function loadOriginalServices(): Promise<OriginalServices> {
  const [config, accounts, cloudHandler, server, paths] = await Promise.all([
    import('../upstream/src/modules/config/ipc/manager'),
    import('../upstream/src/modules/cloud-account/persistence/cloudHandler'),
    import('../upstream/src/modules/cloud-account/ipc/handler'),
    import('../upstream/src/server/main'),
    import('../upstream/src/shared/platform/paths'),
  ]);

  return {
    ConfigManager: config.ConfigManager,
    CloudAccountRepo: accounts.CloudAccountRepo,
    refreshAccountQuota: cloudHandler.refreshAccountQuota,
    bootstrapNestServer: server.bootstrapNestServer,
    stopNestServer: server.stopNestServer,
    getNestServerStatus: server.getNestServerStatus,
    getAgentDir: paths.getAgentDir,
  };
}

async function refreshCurrentAccount(
  services: OriginalServices,
  account: RuntimeAccount,
): Promise<RuntimeAccount> {
  // The original action owns token refresh, quota retry, credit preservation, and account status.
  return services.refreshAccountQuota(account.id);
}

export async function startHeadlessRuntime(options: HeadlessRuntimeOptions): Promise<HeadlessRuntime> {
  const services = await loadOriginalServices();
  if (!samePath(services.getAgentDir(), options.dataDir)) {
    throw new Error('AGM_CORE_DATA_DIR override is not active');
  }

  const config = services.ConfigManager.loadConfig();
  const proxy = {
    ...config.proxy,
    enabled: true,
    port: options.port,
    api_key: options.apiKey,
  };

  // This initializes only the explicitly selected cloud-account database and its secure store.
  await services.CloudAccountRepo.init();
  const startResult = await services.bootstrapNestServer(proxy);
  if (!startResult.success) {
    throw new Error('gateway startup failed');
  }

  let control: ControlServer | undefined;
  let stopped = false;
  const stop = async () => {
    if (stopped) {
      return;
    }
    stopped = true;
    await Promise.allSettled([control?.stop(), services.stopNestServer()]);
  };

  control = createControlServer(options.controlToken, {
    getAccounts: () => services.CloudAccountRepo.getAccounts(),
    setActive: (id) => services.CloudAccountRepo.setActive(id),
    refreshCurrent: (account) => refreshCurrentAccount(services, account),
    getGatewayStatus: () => services.getNestServerStatus(),
    getSchedulingMode: () => String(config.proxy.scheduling_mode ?? 'balance'),
    getHeaderProfile: () => options.headerProfile,
    onStop: async () => {
      await stop();
      await options.requestQuit();
    },
  });

  try {
    const controlPort = await control.start(options.controlPort);
    return { controlPort, stop };
  } catch (error) {
    await stop();
    throw error;
  }
}
