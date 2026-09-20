import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const commit = '23696a2fe276b79009cb0f637f3af030b25c4b20';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(process.argv[2] || path.join(root, '../old/manager-core-v0.20.0'));
const target = path.join(root, 'upstream');
if (existsSync(target)) throw new Error('upstream already exists; preserve it and choose a clean build directory.');
const actual = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
if (actual !== commit) throw new Error('Source repository is not the pinned upstream revision');
const archive = path.join(root, 'upstream.tar');
execFileSync('git', ['-C', repo, 'archive', '--format=tar', `--output=${archive}`, commit,
  'src', 'package.json', 'package-lock.json', 'LICENSE', 'tsconfig.json', 'tsconfig.node.json']);
mkdirSync(target);
execFileSync('tar', ['-xf', archive, '-C', target]);
rmSync(archive);
for (const name of readdirSync(path.join(root, 'patches')).filter(name => name.endsWith('.patch')).sort()) {
  const patch = readFileSync(path.join(root, 'patches', name));
  execFileSync('git', ['apply', '--check', '-'], { cwd: target, input: patch });
  execFileSync('git', ['apply', '-'], { cwd: target, input: patch });
}
console.log(`Prepared upstream ${commit} with reviewed local patches.`);
