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

// Structural validation of every registerTool({...}) call. The Pi runtime loads
// this extension straight from src/index.ts, so a malformed tool literal (e.g.
// a stray `"},{` that splits one object into two call arguments) silently ships
// a tool with no parameters/execute handler. tsc would catch it, but the build
// stays dependency-free, so we parse the call argument lists here instead.
function callArgSlices(src, token) {
  const slices = [];
  let i = 0;
  while ((i = src.indexOf(token, i)) !== -1) {
    let j = i + token.length;
    const start = j;
    let depth = 1;
    let quote = null;
    for (; j < src.length && depth > 0; j++) {
      const c = src[j];
      if (quote) {
        if (c === quote && src[j - 1] !== '\\') quote = null;
        continue;
      }
      if (c === '"' || c === "'" || c === '`') quote = c;
      else if (c === '(' || c === '{' || c === '[') depth++;
      else if (c === ')' || c === '}' || c === ']') depth--;
    }
    slices.push(src.slice(start, j - 1));
    i = j;
  }
  return slices;
}

function firstTopLevelObject(args) {
  const open = args.indexOf('{');
  if (open === -1) return null;
  let depth = 0;
  let quote = null;
  for (let j = open; j < args.length; j++) {
    const c = args[j];
    if (quote) {
      if (c === quote && args[j - 1] !== '\\') quote = null;
      continue;
    }
    if (c === '"' || c === "'" || c === '`') quote = c;
    else if (c === '{' || c === '[' || c === '(') depth++;
    else if (c === '}' || c === ']' || c === ')') {
      depth--;
      if (depth === 0) return { body: args.slice(open, j + 1), rest: args.slice(j + 1) };
    }
  }
  return null;
}

const toolCalls = callArgSlices(index, 'registerTool(');
if (toolCalls.length !== requiredTools.length) {
  throw new Error(`expected ${requiredTools.length} registerTool calls, found ${toolCalls.length}`);
}
for (const args of toolCalls) {
  const obj = firstTopLevelObject(args);
  if (!obj) throw new Error('registerTool called without an object literal argument');
  if (obj.rest.trim()) {
    throw new Error(`registerTool received extra arguments after its object literal (malformed literal?): ...${obj.rest.trim().slice(0, 40)}`);
  }
  const nameMatch = obj.body.match(/name:\s*["'`]([^"'`]+)["'`]/);
  const label = nameMatch ? nameMatch[1] : '<unknown>';
  if (!/\bparameters\s*:/.test(obj.body)) throw new Error(`registerTool ${label} is missing a parameters schema`);
  if (!/\bexecute\s*\(/.test(obj.body)) throw new Error(`registerTool ${label} is missing an execute handler`);
}

function requireSource(label, snippet) {
  if (!index.includes(snippet)) {
    throw new Error(`missing ${label}`);
  }
}

requireSource(
  'ot_search read-only trace query argv',
  '["trace", "query", "--lex", String(params.query), "--limit", String(params.limit ?? 5), "--json"]',
);
requireSource(
  'ot_standup read-only trace query argv',
  '["trace", "query", "--lex", "recent work", "--limit", "10", "--json"]',
);
requireSource(
  '/ot-search read-only trace query argv',
  '["trace", "query", "--lex", args || "recent work", "--json"]',
);

const searchAndStandupRegion = index.slice(
  index.indexOf('name: "ot_search"'),
  index.indexOf('pi.registerTool({\n    name: "ot_capsule"'),
);
for (const forbidden of ['--force-rebuild', 'trace index', 'refresh']) {
  if (searchAndStandupRegion.includes(forbidden)) {
    throw new Error(`Pi search/standup must not invoke query-time maintenance: ${forbidden}`);
  }
}

if (!pkg.pi?.extensions?.includes('./src/index.ts')) throw new Error('pi extension manifest missing');
if (!pkg.pi?.skills?.includes('./skills')) throw new Error('pi skills manifest missing');
if (!pkg.pi?.prompts?.includes('./prompts')) throw new Error('pi prompts manifest missing');
console.log('opentraces-pi tests ok');
