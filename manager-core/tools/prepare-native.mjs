import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
const require = createRequire(import.meta.url);
const target = require('electron/package.json').version;
const sqlite = path.dirname(require.resolve('better-sqlite3/package.json'));
const installer = require.resolve('prebuild-install/bin.js');
execFileSync(process.execPath, [installer, '--runtime=electron', `--target=${target}`, `--arch=${process.arch}`],
  { cwd: sqlite, stdio: 'inherit', windowsHide: true });
console.log(`Prepared SQLite native module for Electron ${target}.`);
