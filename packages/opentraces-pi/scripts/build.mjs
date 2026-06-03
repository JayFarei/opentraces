import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('..', import.meta.url).pathname;
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

assert(pkg.name === 'opentraces-pi', 'package name must be opentraces-pi');
assert(pkg.keywords?.includes('pi-package'), 'keywords must include pi-package');
assert(pkg.repository?.directory === 'packages/opentraces-pi', 'repository.directory missing');
for (const resource of ['src/index.ts', 'skills/opentraces/SKILL.md', 'prompts/ot-search.md']) {
  readFileSync(join(root, resource), 'utf8');
}

mkdirSync(join(root, 'dist'), { recursive: true });
writeFileSync(
  join(root, 'dist/index.js'),
  `// Build marker for opentraces-pi. Pi loads the TypeScript extension from src/index.ts.\nexport {};\n`,
  'utf8',
);
writeFileSync(
  join(root, 'dist/index.d.ts'),
  `export {};\n`,
  'utf8',
);
console.log('opentraces-pi build ok');
