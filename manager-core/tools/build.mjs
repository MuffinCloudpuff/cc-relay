import { build } from 'esbuild';
import { transform } from '@swc/core';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'upstream/src');
await mkdir(path.join(root, 'dist'), { recursive: true });
const result = await build({
  absWorkingDir: root,
  entryPoints: ['runtime/main.ts'],
  outfile: 'dist/main.cjs',
  bundle: true,
  packages: 'external',
  platform: 'node',
  format: 'cjs',
  target: 'node22',
  sourcemap: true,
  metafile: true,
  alias: { '@': source },
  plugins: [{ name: 'typescript-decorator-metadata', setup(builder) {
    builder.onLoad({ filter: /\.ts$/ }, async ({ path: filename }) => {
      const result = await transform(await readFile(filename, 'utf8'), {
        filename,
        jsc: { parser: { syntax: 'typescript', decorators: true }, target: 'es2022',
          transform: { legacyDecorator: true, decoratorMetadata: false } },
        module: { type: 'es6' }, sourceMaps: false,
      });
      return { contents: result.code, loader: 'js' };
    });
  } }],
});
await writeFile(path.join(root, 'dist/metafile.json'), JSON.stringify(result.metafile, null, 2));
const inputs = Object.keys(result.metafile.inputs);
const forbidden = inputs.filter(name => /(?:^|\/)src\/(?:main\.ts|renderer\.ts|preload\.ts)$/.test(name));
if (forbidden.length) throw new Error('Desktop entrypoint leaked into headless build');
console.log(`Built original Manager core from ${inputs.length} source modules; no desktop entrypoint.`);
