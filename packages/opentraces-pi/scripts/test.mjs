import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('..', import.meta.url).pathname;
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const index = readFileSync(join(root, 'src/index.ts'), 'utf8');

const requiredTools = ['ot_search', 'ot_trace', 'ot_standup', 'ot_capsule', 'ot_dataset', 'ot_capture_status'];
const requiredCommands = ['ot-search', 'ot-trace', 'ot-standup', 'ot-capsule', 'ot-dataset', 'ot-capture-status', 'ot-setup'];
for (const tool of requiredTools) {
  if (!index.includes(`name: "${tool}"`) && !index.includes(`name: '${tool}'`)) {
    throw new Error(`missing tool ${tool}`);
  }
}
for (const command of requiredCommands) {
  if (!index.includes(`registerCommand("${command}"`) && !index.includes(`registerCommand('${command}'`)) {
    throw new Error(`missing command ${command}`);
  }
}
if (!pkg.pi?.extensions?.includes('./src/index.ts')) throw new Error('pi extension manifest missing');
if (!pkg.pi?.skills?.includes('./skills')) throw new Error('pi skills manifest missing');
if (!pkg.pi?.prompts?.includes('./prompts')) throw new Error('pi prompts manifest missing');
console.log('opentraces-pi tests ok');
