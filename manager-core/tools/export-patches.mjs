import { execFileSync, spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(process.argv[2] || path.join(root, '../old/manager-core-v0.20.0'));
const commit = '23696a2fe276b79009cb0f637f3af030b25c4b20';
const names = [
  'src/shared/platform/paths.ts',
  'src/server/main.ts',
  'src/modules/proxy-gateway/server/common/utils/request-header-profile.ts',
  'src/modules/proxy-gateway/server/modules/gemini/gemini-client.service.ts',
  'src/modules/proxy-gateway/antigravity/ClaudeRequestMapper.ts',
  'src/modules/proxy-gateway/antigravity/ClaudeResponseMapper.ts',
  'src/modules/proxy-gateway/antigravity/ClaudeStreamingMapper.ts',
  'src/modules/proxy-gateway/antigravity/OpenAIUsageMapper.ts',
  'src/tests/unit/claude-request-mapper-cache-compatibility.test.ts',
  'src/tests/unit/claude-response-usage.test.ts',
  'src/tests/unit/proxy-retry-mock.test.ts',
];
mkdirSync(path.join(root, 'results'), { recursive: true });
mkdirSync(path.join(root, 'patches'), { recursive: true });
const temporary = mkdtempSync(path.join(root, 'results/patch-export-'));
const pieces = [];
for (const name of names) {
  const original = spawnSync('git', ['-C', repo, 'show', `${commit}:${name}`], { maxBuffer: 8 * 1024 * 1024 });
  const oldFile = path.join(temporary, 'original');
  if (original.status === 0) writeFileSync(oldFile, original.stdout);
  const result = spawnSync('git', ['diff', '--no-index', '--no-prefix', '--',
    original.status === 0 ? oldFile : '/dev/null', path.join(root, 'upstream', name)],
  { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
  if (![0, 1].includes(result.status)) throw new Error(`Cannot export ${name}`);
  if (result.status === 0) continue;
  pieces.push(result.stdout.split(/\r?\n/).map(line => {
    if (line.startsWith('diff --git ')) return `diff --git a/${name} b/${name}`;
    if (line.startsWith('--- ')) return original.status === 0 ? `--- a/${name}` : '--- /dev/null';
    if (line.startsWith('+++ ')) return `+++ b/${name}`;
    return line;
  }).join('\n'));
}
writeFileSync(path.join(root, 'patches/001-headless-compatibility.patch'), pieces.join(''));
const protectedRoots = [
  'src/modules/proxy-gateway/server/modules/account-lease',
  'src/modules/proxy-gateway/server/shared/services',
  'src/modules/cloud-account/persistence',
  'src/modules/cloud-account/services',
  'src/shared/security',
  'src/modules/config',
];
const files = execFileSync('git', ['-C', repo, 'ls-tree', '-r', '--name-only', commit, ...protectedRoots], { encoding: 'utf8' }).trim().split('\n');
const manifest = { commit, normalization: 'UTF-8 text with CRLF normalized to LF', files: {} };
for (const name of files) {
  const baseline = execFileSync('git', ['-C', repo, 'show', `${commit}:${name}`], { maxBuffer: 8 * 1024 * 1024 });
  const current = readFileSync(path.join(root, 'upstream', name), 'utf8').replace(/\r\n/g, '\n');
  const expected = baseline.toString('utf8').replace(/\r\n/g, '\n');
  if (expected !== current) throw new Error(`Protected core changed: ${name}`);
  manifest.files[name] = createHash('sha256').update(expected).digest('hex');
}
writeFileSync(path.join(root, 'preserved-core.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(`Exported ${pieces.length} narrow file patches; ${files.length} protected original files unchanged except line endings.`);
