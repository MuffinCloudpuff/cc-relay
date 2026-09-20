import { defineConfig } from 'vitest/config';
import { transform } from '@swc/core';
import path from 'node:path';

process.env.AGM_CORE_DATA_DIR = path.resolve('results/unit-data');

export default defineConfig({
  resolve: { alias: { '@': path.resolve('upstream/src'),
    electron: path.resolve('upstream/src/mocks/electron.ts'),
    keytar: path.resolve('upstream/src/mocks/empty.ts'),
    'better-sqlite3': path.resolve('upstream/src/mocks/empty.ts'),
  } },
  plugins: [{ name: 'decorator-metadata', enforce: 'pre', async transform(code, id) {
    if (!id.endsWith('.ts') || id.includes('node_modules')) return;
    return transform(code, { filename: id,
      jsc: { parser: { syntax: 'typescript', decorators: true }, target: 'es2022',
        transform: { legacyDecorator: true, decoratorMetadata: false } },
      module: { type: 'es6' }, sourceMaps: true });
  } }],
  test: { environment: 'node', include: [
    'test/**/*.test.ts',
    'upstream/src/tests/unit/account-lease-*.test.ts',
    'upstream/src/tests/unit/proxy-retry*.test.ts',
    'upstream/src/tests/unit/model-routing*.test.ts',
    'upstream/src/tests/unit/rate-limit*.test.ts',
    'upstream/src/tests/unit/internal-sse.test.ts',
    'upstream/src/tests/unit/claude-request-mapper-*.test.ts',
    'upstream/src/tests/unit/claude-response-usage.test.ts',
  ], testTimeout: 15000 },
});
