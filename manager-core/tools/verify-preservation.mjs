import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const manifest = JSON.parse(readFileSync(path.join(root, 'preserved-core.json'), 'utf8'));
for (const [file, expected] of Object.entries(manifest.files)) {
  const actual = createHash('sha256').update(readFileSync(path.join(root, 'upstream', file), 'utf8').replace(/\r\n/g, '\n')).digest('hex');
  if (actual !== expected) throw new Error(`Protected original core changed: ${file}`);
}
console.log(`${Object.keys(manifest.files).length} protected original core files match ${manifest.commit}.`);
