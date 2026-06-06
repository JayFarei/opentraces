// Trace Capsules — registry data.
//
// A Trace Capsule (`opentraces.capsule.v1`) is a sealed, replayable package of a
// failing agent session, anchored to an open repo so it becomes a replayable test:
//   action_trajectory · ctx_tree · trace_step · git snapshot
//
// These are mock capsules across the workspace's projects. Concepts mirror the
// blog post: a client agent reports a capsule against an upstream repo issue, the
// maintainer's agent pulls it, re-poses the intent against the snapshot after a
// fix, and the client agent unblocks when the replay passes.

// Seeded fingerprint (mirrors the trace minimap's class system).
function capFingerprint(seedStr, n) {
  let seed = Math.abs(seedStr.split("").reduce((a, c) => (a * 33 + c.charCodeAt(0)) | 0, 7));
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const classes = ["user", "plan", "think", "read", "exec", "write"];
  const weights = [0.05, 0.11, 0.28, 0.18, 0.18, 0.20];
  const out = [];
  for (let i = 0; i < n; i++) {
    let r = rnd(), c = "think";
    for (let j = 0; j < classes.length; j++) { if (r < weights[j]) { c = classes[j]; break; } r -= weights[j]; }
    out.push(c);
  }
  return out;
}

// Lifecycle: the agent-to-agent path a capsule walks. Order matters (stepper).
const CAPSULE_LIFECYCLE = [
  { key: "reported",  label: "Reported",  hint: "Client agent sealed the capsule" },
  { key: "triaged",   label: "Triaged",   hint: "Maintainer agent pulled & reproduced" },
  { key: "fixing",    label: "Fixing",    hint: "Fix in progress on a branch" },
  { key: "resolved",  label: "Resolved",  hint: "Replay passes against the fix" },
  { key: "unblocked", label: "Unblocked", hint: "Client agent resumed the work" },
];

// Agent references (match landing-data agents).
const A = {
  claude:   { name: "Claude Code", model: "Sonnet 4.5",   icon: "claude" },
  codex:    { name: "Codex",       model: "GPT-5.3 Codex", icon: "codex" },
  cursor:   { name: "Cursor",      model: "Claude 4.1",    icon: "cursor" },
  gemini:   { name: "Gemini CLI",  model: "Gemini 2.5 Pro",icon: "gemini" },
  opencode: { name: "OpenCode",    model: "GPT-5 Mini",    icon: "opencode" },
};

// A compact context envelope tree, lightly parameterised per capsule.
function ctxTree(rootLabel, leaf) {
  return [
    { depth: 0, kind: "root",  label: rootLabel, meta: "system + repo map" },
    { depth: 1, kind: "files", label: "open files", meta: "6 files · 18.2k tok" },
    { depth: 2, kind: "file",  label: leaf.file, meta: leaf.fileMeta },
    { depth: 1, kind: "tool",  label: "tool results", meta: "11 calls · 9.7k tok" },
    { depth: 2, kind: "read",  label: leaf.read, meta: "read" },
    { depth: 2, kind: "exec",  label: leaf.exec, meta: "exec" },
    { depth: 1, kind: "msg",   label: "conversation", meta: "user + agent · 4.1k tok" },
  ];
}

const CAPSULES = [
  {
    id: "cap-1", cid: "cap_7f3a9c2", v: "opentraces.capsule.v1",
    title: "next/og import throws ReferenceError in edge runtime",
    intent: "Render an OG image at the edge: import { ImageResponse } from 'next/og' and return it from a route handler with runtime = 'edge'.",
    repo: "vercel/next.js", issue: { number: 58213, state: "open" },
    agent: A.codex, owner: true, author: "you", visibility: "published",
    publishedAt: "4h ago", lifecycle: "triaged",
    scrub: { state: "clean", pii: 0, secrets: 0, anonymised: true, scanned: "4h ago" },
    fail: { step: 142, lane: "exec", summary: "ReferenceError: WebAssembly.instantiate is not a function",
      error: "ReferenceError: WebAssembly.instantiate is not a function\n  at Object.<anonymous> (node_modules/next/dist/compiled/@vercel/og/index.edge.js:1:9241)\n  at instantiateModule (edge-runtime/module.js:88:7)" },
    manifest: {
      trajectory: { steps: 142, bytes: "311 KB" },
      ctx: { nodes: 38, tokens: "31.8k", bytes: "204 KB" },
      failStep: { bytes: "12 KB" },
      git: { sha: "a1b2c3d", dirty: false, files: 0, bytes: "1.4 MB" },
    },
    stats: { views: 214, pulls: 4, shares: 3, subscribers: 2 },
    git: { branch: "main", sha: "a1b2c3d", base: "v15.2.1", dirty: false },
    fingerprint: capFingerprint("cap_7f3a9c2", 58), failFrac: 0.84,
    ctx: ctxTree("app/og/route.tsx", { file: "app/og/route.tsx", fileMeta: "edge handler · 41 lines", read: "next/og · index.edge.js", exec: "next build --experimental" }),
    replays: [
      { actor: "client", agent: A.codex, when: "4h ago", sha: "a1b2c3d", outcome: "fail", dur: "38s", note: "Original failure — sealed into capsule" },
    ],
    timeline: [
      { key: "reported", actor: "client", agent: A.codex, label: "Capsule sealed & attached to #58213", when: "4h ago", state: "done" },
      { key: "triaged",  actor: "maintainer", label: "next-bot pulled the capsule, reproduced on edge", when: "2h ago", state: "done" },
      { key: "fixing",   actor: "maintainer", label: "Awaiting fix", when: "—", state: "active" },
    ],
  },
  {
    id: "cap-2", cid: "cap_b41e0d8", v: "opentraces.capsule.v1",
    title: "Streaming completion truncated at 4096 tokens with stream:true",
    intent: "Stream a long completion with stream:true and accumulate deltas; the stream closes early at 4096 tokens with no finish_reason.",
    repo: "openai/openai-node", issue: { number: 1187, state: "closed" },
    agent: A.claude, owner: true, author: "you", visibility: "published",
    publishedAt: "2d ago", lifecycle: "resolved",
    scrub: { state: "clean", pii: 0, secrets: 0, anonymised: true, scanned: "2d ago" },
    fail: { step: 76, lane: "read", summary: "stream ended after 4096 tokens, finish_reason=null",
      error: "AssertionError: expected finish_reason to be 'stop', got null\n  chunks=212  last_delta='' (empty)\n  at collectStream (src/streaming.ts:144)" },
    manifest: {
      trajectory: { steps: 76, bytes: "168 KB" },
      ctx: { nodes: 24, tokens: "12.4k", bytes: "96 KB" },
      failStep: { bytes: "8 KB" },
      git: { sha: "9e02f7a", dirty: false, files: 0, bytes: "612 KB" },
    },
    stats: { views: 488, pulls: 7, shares: 6, subscribers: 5 },
    git: { branch: "main", sha: "9e02f7a", base: "v4.71.0", dirty: false },
    fingerprint: capFingerprint("cap_b41e0d8", 44), failFrac: 0.62,
    ctx: ctxTree("examples/stream.ts", { file: "src/streaming.ts", fileMeta: "Stream parser · 211 lines", read: "SSE chunks (212)", exec: "node examples/stream.ts" }),
    replays: [
      { actor: "client", agent: A.claude, when: "2d ago", sha: "9e02f7a", outcome: "fail", dur: "11s", note: "Original failure" },
      { actor: "maintainer", agent: A.codex, when: "19h ago", sha: "9e02f7a", outcome: "fail", dur: "10s", note: "Reproduced by openai-bot" },
      { actor: "maintainer", agent: A.codex, when: "6h ago", sha: "c4d1e90", outcome: "pass", dur: "12s", note: "Replayed against fix #1191 — max_tokens default removed" },
    ],
    timeline: [
      { key: "reported",  actor: "client", agent: A.claude, label: "Capsule sealed & attached to #1187", when: "2d ago", state: "done" },
      { key: "triaged",   actor: "maintainer", label: "openai-bot reproduced from the capsule", when: "19h ago", state: "done" },
      { key: "fixing",    actor: "maintainer", label: "Fix #1191 opened — stop padding default max_tokens", when: "10h ago", state: "done" },
      { key: "resolved",  actor: "maintainer", agent: A.codex, label: "Replay passes against c4d1e90", when: "6h ago", state: "done" },
      { key: "unblocked", actor: "client", label: "Awaiting client resume", when: "—", state: "active" },
    ],
  },
  {
    id: "cap-3", cid: "cap_2c9f5a1", v: "opentraces.capsule.v1",
    title: "RecursionError in RunnableSequence on a cyclic tool graph",
    intent: "Compose a RunnableSequence where a tool can route back to a prior node; invoking it overflows the stack instead of surfacing a cycle error.",
    repo: "langchain-ai/langchain", issue: { number: 24910, state: "closed" },
    agent: A.cursor, owner: false, author: "wei-zhang", visibility: "published",
    publishedAt: "5d ago", lifecycle: "unblocked",
    scrub: { state: "clean", pii: 0, secrets: 0, anonymised: true, scanned: "5d ago" },
    fail: { step: 203, lane: "exec", summary: "RecursionError: maximum recursion depth exceeded",
      error: "RecursionError: maximum recursion depth exceeded while calling a Python object\n  File \"langchain_core/runnables/base.py\", line 2841, in invoke\n  [Previous line repeated 974 more times]" },
    manifest: {
      trajectory: { steps: 203, bytes: "402 KB" },
      ctx: { nodes: 51, tokens: "44.2k", bytes: "288 KB" },
      failStep: { bytes: "21 KB" },
      git: { sha: "f73c0b1", dirty: false, files: 0, bytes: "2.1 MB" },
    },
    stats: { views: 1024, pulls: 12, shares: 19, subscribers: 14 },
    git: { branch: "master", sha: "f73c0b1", base: "v0.3.18", dirty: false },
    fingerprint: capFingerprint("cap_2c9f5a1", 60), failFrac: 0.91,
    ctx: ctxTree("graph/agent.py", { file: "langchain_core/runnables/base.py", fileMeta: "RunnableSequence · 2.9k lines", read: "graph/agent.py", exec: "pytest tests/test_cycles.py" }),
    replays: [
      { actor: "client", agent: A.cursor, when: "5d ago", sha: "f73c0b1", outcome: "fail", dur: "4s", note: "Original failure" },
      { actor: "maintainer", agent: A.gemini, when: "4d ago", sha: "f73c0b1", outcome: "fail", dur: "5s", note: "Reproduced" },
      { actor: "maintainer", agent: A.gemini, when: "3d ago", sha: "0b918ad", outcome: "pass", dur: "6s", note: "Cycle guard added — raises GraphCycleError" },
    ],
    timeline: [
      { key: "reported",  actor: "client", agent: A.cursor, label: "Capsule sealed by wei-zhang", when: "5d ago", state: "done" },
      { key: "triaged",   actor: "maintainer", label: "Reproduced from capsule", when: "4d ago", state: "done" },
      { key: "fixing",    actor: "maintainer", label: "PR #24933 — depth guard", when: "3d ago", state: "done" },
      { key: "resolved",  actor: "maintainer", agent: A.gemini, label: "Replay passes against 0b918ad", when: "3d ago", state: "done" },
      { key: "unblocked", actor: "client", agent: A.cursor, label: "Client agent resumed & shipped", when: "2d ago", state: "done" },
    ],
  },
  {
    id: "cap-4", cid: "cap_e8d130f", v: "opentraces.capsule.v1",
    title: "RLS policy blocks insert despite service-role key",
    intent: "Insert a row with the service-role client; the request is rejected by row-level security even though the key should bypass RLS.",
    repo: "supabase/supabase", issue: { number: null, state: "draft" },
    agent: A.gemini, owner: true, author: "you", visibility: "unpublished",
    publishedAt: "draft", lifecycle: "reported",
    scrub: { state: "clean", pii: 0, secrets: 0, anonymised: true, scanned: "20m ago" },
    fail: { step: 51, lane: "write", summary: "new row violates row-level security policy for table \"events\"",
      error: "PostgrestError: new row violates row-level security policy for table \"events\" (code 42501)\n  using: service_role key  ·  header apikey present" },
    manifest: {
      trajectory: { steps: 51, bytes: "121 KB" },
      ctx: { nodes: 19, tokens: "9.1k", bytes: "74 KB" },
      failStep: { bytes: "6 KB" },
      git: { sha: "55ad2e0", dirty: true, files: 2, bytes: "3.4 MB" },
    },
    stats: { views: 0, pulls: 0, shares: 0, subscribers: 0 },
    git: { branch: "fix/rls-events", sha: "55ad2e0", base: "develop", dirty: true },
    fingerprint: capFingerprint("cap_e8d130f", 40), failFrac: 0.78,
    ctx: ctxTree("lib/db.ts", { file: "lib/db.ts", fileMeta: "service client · 62 lines", read: "supabase/migrations/0007_rls.sql", exec: "supabase db reset" }),
    replays: [
      { actor: "client", agent: A.gemini, when: "20m ago", sha: "55ad2e0", outcome: "fail", dur: "2s", note: "Original failure — not yet published" },
    ],
    timeline: [
      { key: "reported", actor: "client", agent: A.gemini, label: "Sealed locally — review before publishing", when: "20m ago", state: "active" },
    ],
  },
  {
    id: "cap-5", cid: "cap_5a7c2bb", v: "opentraces.capsule.v1",
    title: "torch.compile graph break on dynamic shapes in attention",
    intent: "Wrap a multi-head attention block in torch.compile(dynamic=True); compilation falls back to eager with a graph break on every forward.",
    repo: "pytorch/pytorch", issue: { number: 142203, state: "open" },
    agent: A.opencode, owner: false, author: "n-kapoor", visibility: "published",
    publishedAt: "1d ago", lifecycle: "fixing",
    scrub: { state: "clean", pii: 0, secrets: 0, anonymised: true, scanned: "1d ago" },
    fail: { step: 188, lane: "exec", summary: "torch._dynamo graph break: dynamic shape on dim 1",
      error: "torch._dynamo.exc.Unsupported: dynamic shape operator on symbolic dim 1\n  from user code: attn.py:118 in forward\n  set TORCH_LOGS=+dynamo for the full trace" },
    manifest: {
      trajectory: { steps: 188, bytes: "377 KB" },
      ctx: { nodes: 47, tokens: "39.6k", bytes: "256 KB" },
      failStep: { bytes: "18 KB" },
      git: { sha: "d0c7e44", dirty: false, files: 0, bytes: "5.8 MB" },
    },
    stats: { views: 712, pulls: 9, shares: 11, subscribers: 8 },
    git: { branch: "main", sha: "d0c7e44", base: "v2.6.0", dirty: false },
    fingerprint: capFingerprint("cap_5a7c2bb", 56), failFrac: 0.86,
    ctx: ctxTree("models/attn.py", { file: "models/attn.py", fileMeta: "MHA block · 134 lines", read: "torch/_dynamo/symbolic_convert.py", exec: "python repro.py" }),
    replays: [
      { actor: "client", agent: A.opencode, when: "1d ago", sha: "d0c7e44", outcome: "fail", dur: "1m 04s", note: "Original failure" },
      { actor: "maintainer", agent: A.claude, when: "16h ago", sha: "d0c7e44", outcome: "fail", dur: "58s", note: "pytorch-bot reproduced the graph break" },
    ],
    timeline: [
      { key: "reported", actor: "client", agent: A.opencode, label: "Capsule sealed by n-kapoor", when: "1d ago", state: "done" },
      { key: "triaged",  actor: "maintainer", label: "pytorch-bot reproduced", when: "16h ago", state: "done" },
      { key: "fixing",   actor: "maintainer", agent: A.claude, label: "Branch dynamo/dyn-attn — guard rewrite in progress", when: "5h ago", state: "active" },
    ],
  },
  {
    id: "cap-6", cid: "cap_9d6f014", v: "opentraces.capsule.v1",
    title: "App Router catch-all route 404s on a trailing slash",
    intent: "Define app/docs/[...slug]/page.tsx; /docs/a/b resolves but /docs/a/b/ (trailing slash) returns 404 with trailingSlash:true.",
    repo: "vercel/next.js", issue: { number: 59881, state: "closed" },
    agent: A.claude, owner: true, author: "you", visibility: "published",
    publishedAt: "3d ago", lifecycle: "resolved",
    scrub: { state: "clean", pii: 0, secrets: 0, anonymised: true, scanned: "3d ago" },
    fail: { step: 64, lane: "read", summary: "GET /docs/a/b/ → 404 (expected 200)",
      error: "Expected 200, received 404\n  matched: none  ·  trailingSlash: true\n  at app-router/match.ts:212" },
    manifest: {
      trajectory: { steps: 64, bytes: "142 KB" },
      ctx: { nodes: 22, tokens: "11.2k", bytes: "88 KB" },
      failStep: { bytes: "7 KB" },
      git: { sha: "3a90fe2", dirty: false, files: 0, bytes: "1.4 MB" },
    },
    stats: { views: 356, pulls: 5, shares: 4, subscribers: 3 },
    git: { branch: "main", sha: "3a90fe2", base: "v15.1.6", dirty: false },
    fingerprint: capFingerprint("cap_9d6f014", 42), failFrac: 0.58,
    ctx: ctxTree("app/docs/[...slug]/page.tsx", { file: "app/docs/[...slug]/page.tsx", fileMeta: "catch-all · 28 lines", read: "next.config.js", exec: "next start" }),
    replays: [
      { actor: "client", agent: A.claude, when: "3d ago", sha: "3a90fe2", outcome: "fail", dur: "9s", note: "Original failure" },
      { actor: "maintainer", agent: A.codex, when: "2d ago", sha: "b8f1c03", outcome: "pass", dur: "10s", note: "Replay passes against normalize-trailing-slash fix" },
    ],
    timeline: [
      { key: "reported", actor: "client", agent: A.claude, label: "Capsule sealed & attached to #59881", when: "3d ago", state: "done" },
      { key: "triaged",  actor: "maintainer", label: "Reproduced by next-bot", when: "2d ago", state: "done" },
      { key: "fixing",   actor: "maintainer", label: "Fix merged — match normalizes trailing slash", when: "2d ago", state: "done" },
      { key: "resolved", actor: "maintainer", agent: A.codex, label: "Replay passes against b8f1c03", when: "2d ago", state: "done" },
      { key: "unblocked", actor: "client", label: "Awaiting client resume", when: "—", state: "active" },
    ],
  },
  {
    id: "cap-7", cid: "cap_3f0a8e5", v: "opentraces.capsule.v1",
    title: "Tool-call arguments arrive as a string, not an object",
    intent: "Parse tool_calls[0].function.arguments from a chat completion; the field is a JSON string and the typings imply it is already an object.",
    repo: "openai/openai-node", issue: { number: 1203, state: "open" },
    agent: A.codex, owner: false, author: "dmitra-s", visibility: "published",
    publishedAt: "9h ago", lifecycle: "triaged",
    scrub: { state: "clean", pii: 0, secrets: 0, anonymised: true, scanned: "9h ago" },
    fail: { step: 39, lane: "write", summary: "TypeError: arguments.city is undefined (arguments is a string)",
      error: "TypeError: Cannot read properties of undefined (reading 'city')\n  typeof arguments === 'string'  ·  '{\"city\":\"Berlin\"}'\n  at handleToolCall (src/agent.ts:88)" },
    manifest: {
      trajectory: { steps: 39, bytes: "96 KB" },
      ctx: { nodes: 16, tokens: "7.4k", bytes: "61 KB" },
      failStep: { bytes: "5 KB" },
      git: { sha: "1f4ab90", dirty: false, files: 0, bytes: "612 KB" },
    },
    stats: { views: 142, pulls: 2, shares: 5, subscribers: 4 },
    git: { branch: "main", sha: "1f4ab90", base: "v4.72.0", dirty: false },
    fingerprint: capFingerprint("cap_3f0a8e5", 38), failFrac: 0.52,
    ctx: ctxTree("src/agent.ts", { file: "src/agent.ts", fileMeta: "tool dispatcher · 120 lines", read: "ChatCompletion type defs", exec: "ts-node src/agent.ts" }),
    replays: [
      { actor: "client", agent: A.codex, when: "9h ago", sha: "1f4ab90", outcome: "fail", dur: "3s", note: "Original failure" },
      { actor: "maintainer", agent: A.claude, when: "5h ago", sha: "1f4ab90", outcome: "fail", dur: "3s", note: "Reproduced — docs vs typings mismatch confirmed" },
    ],
    timeline: [
      { key: "reported", actor: "client", agent: A.codex, label: "Capsule sealed by dmitra-s", when: "9h ago", state: "done" },
      { key: "triaged",  actor: "maintainer", agent: A.claude, label: "openai-bot reproduced; labelled docs", when: "5h ago", state: "done" },
      { key: "fixing",   actor: "maintainer", label: "Awaiting fix", when: "—", state: "active" },
    ],
  },
  {
    id: "cap-8", cid: "cap_c12b7a9", v: "opentraces.capsule.v1",
    title: "Edge-trace dataset drops ctx_tree on re-export",
    intent: "Re-export the edge-traces dataset to parquet; the ctx_tree column is silently dropped when a row has a null reasoning span.",
    repo: "jayfarei/opentraces", issue: { number: 318, state: "open" },
    agent: A.claude, owner: true, author: "you", visibility: "unpublished",
    publishedAt: "draft", lifecycle: "reported",
    scrub: { state: "scanning", pii: 1, secrets: 0, anonymised: false, scanned: "running…" },
    fail: { step: 97, lane: "write", summary: "ctx_tree column null for 12 / 2,300 rows after export",
      error: "DataLossWarning: column 'ctx_tree' dropped for 12 rows (null reasoning span)\n  exporter: parquet  ·  schema-on-write coercion\n  at export/parquet.ts:144" },
    manifest: {
      trajectory: { steps: 97, bytes: "204 KB" },
      ctx: { nodes: 33, tokens: "21.0k", bytes: "148 KB" },
      failStep: { bytes: "9 KB" },
      git: { sha: "7c0d2a8", dirty: true, files: 3, bytes: "880 KB" },
    },
    stats: { views: 0, pulls: 0, shares: 0, subscribers: 0 },
    git: { branch: "fix/ctx-export", sha: "7c0d2a8", base: "main", dirty: true },
    fingerprint: capFingerprint("cap_c12b7a9", 46), failFrac: 0.7,
    ctx: ctxTree("export/parquet.ts", { file: "export/parquet.ts", fileMeta: "exporter · 210 lines", read: "edge-traces schema", exec: "npm run export -- --parquet" }),
    replays: [
      { actor: "client", agent: A.claude, when: "35m ago", sha: "7c0d2a8", outcome: "fail", dur: "14s", note: "Original failure — scrub in progress" },
    ],
    timeline: [
      { key: "reported", actor: "client", agent: A.claude, label: "Sealed locally — security scan running", when: "35m ago", state: "active" },
    ],
  },
];

// Saved searches / quick filters surfaced as chips.
const CAPSULE_FILTERS = [
  { id: "all",         label: "All capsules" },
  { id: "mine",        label: "Mine" },
  { id: "published",   label: "Published" },
  { id: "unpublished", label: "Unpublished" },
  { id: "open",        label: "Open issues" },
  { id: "passing",     label: "Replay passing" },
  { id: "failing",     label: "Replay failing" },
];

// ── Triage layer ────────────────────────────────────────────────
// The reframe: a verdict is oracle(intent, code@version), not a frozen badge.
// Each capsule carries a *head* verdict (re-run against current main), the
// survival/drift of its pinned snapshot, a dependency-version matrix (which
// version resolves the story), whether the executed verdict was posted back to
// the linked issue, and downstream blast radius (agents blocked / pulling).
//
//   head.onMain : "fail" | "pass" | "inconclusive"  — verdict against main now
//   survival    : "alive" (commit on main) | "reverted" (fix undone) | "lost" (force-pushed; running from bundle)
//   deps.matrix : [{ ver, s }]  s = "fail" | "pass" | "incon"
//   issue.posted: did the current verdict make it back to the issue thread?
//   impact      : { blocked, pulling }  downstream agents

const TRIAGE = {
  // still broken on main — unsolved, all versions fail
  "cap-1": {
    head: { onMain: "fail", recheck: "2h ago", behind: 12, survival: "alive" },
    deps: { runtime: "edge runtime", pkg: "next", matrix: [{ ver: "14.1", s: "fail" }, { ver: "14.2", s: "fail" }, { ver: "15.0", s: "fail" }] },
    issue: { posted: true, postedWhen: "2h ago" },
    impact: { blocked: 6, pulling: 41 },
  },
  // resolved & coherent — passes on main, fix landed
  "cap-2": {
    head: { onMain: "pass", recheck: "6h ago", behind: 8, survival: "alive" },
    deps: { runtime: "node 20", pkg: "openai", matrix: [{ ver: "4.70", s: "fail" }, { ver: "4.71", s: "fail" }, { ver: "4.72", s: "pass" }] },
    issue: { posted: true, postedWhen: "6h ago" },
    impact: { blocked: 0, pulling: 18 },
  },
  // fully unblocked — passes on main
  "cap-3": {
    head: { onMain: "pass", recheck: "2d ago", behind: 31, survival: "alive" },
    deps: { runtime: "python 3.12", pkg: "langchain", matrix: [{ ver: "0.3.17", s: "fail" }, { ver: "0.3.18", s: "fail" }, { ver: "0.3.19", s: "pass" }] },
    issue: { posted: true, postedWhen: "2d ago" },
    impact: { blocked: 0, pulling: 33 },
  },
  // draft — not published, captured locally
  "cap-4": {
    head: { onMain: "fail", recheck: "20m ago", behind: 0, survival: "alive" },
    deps: { runtime: "node 20", pkg: "@supabase/supabase-js", matrix: [{ ver: "2.45", s: "fail" }, { ver: "2.46", s: "fail" }] },
    issue: { posted: false },
    impact: { blocked: 1, pulling: 0 },
  },
  // in progress — still fails on main, branch fix unproven (nightly inconclusive)
  "cap-5": {
    head: { onMain: "fail", recheck: "5h ago", behind: 5, survival: "alive" },
    deps: { runtime: "cuda 12.4", pkg: "torch", matrix: [{ ver: "2.5", s: "fail" }, { ver: "2.6", s: "fail" }, { ver: "2.7.dev", s: "incon" }] },
    issue: { posted: true, postedWhen: "16h ago" },
    impact: { blocked: 9, pulling: 58 },
  },
  // INCOHERENT — marked Resolved & issue CLOSED, but the fix was reverted; fails on main again
  "cap-6": {
    head: { onMain: "fail", recheck: "40m ago", behind: 3, survival: "reverted" },
    deps: { runtime: "node 20", pkg: "next", matrix: [{ ver: "15.0", s: "fail" }, { ver: "15.1", s: "pass" }, { ver: "15.2", s: "fail" }] },
    issue: { posted: true, postedWhen: "2d ago", stale: true },
    impact: { blocked: 4, pulling: 22 },
  },
  // INCONCLUSIVE — recheck couldn't run (sandboxed creds); needs work to become runnable
  "cap-7": {
    head: { onMain: "inconclusive", recheck: "failed 1h ago", behind: 4, survival: "alive", reason: "no API key in replay sandbox" },
    deps: { runtime: "node 20", pkg: "openai", matrix: [{ ver: "4.71", s: "incon" }, { ver: "4.72", s: "incon" }] },
    issue: { posted: false },
    impact: { blocked: 2, pulling: 19 },
  },
  // draft — scrub still running, force-pushed base
  "cap-8": {
    head: { onMain: "fail", recheck: "35m ago", behind: 2, survival: "lost" },
    deps: { runtime: "node 20", pkg: "internal", matrix: [{ ver: "main", s: "fail" }] },
    issue: { posted: false },
    impact: { blocked: 1, pulling: 0 },
  },
};

// Merge triage onto a capsule + derive coherence flags.
function withTriage(cap) {
  const t = TRIAGE[cap.id] || {};
  const head = t.head || { onMain: "fail", recheck: "—", behind: 0, survival: "alive" };
  const snapshotVerdict = cap.replays[0].outcome; // the sealed/original verdict
  // Contradiction: workflow says done (resolved/unblocked) or issue closed, but it still fails on main.
  const claimsDone = cap.lifecycle === "resolved" || cap.lifecycle === "unblocked";
  const issueClosed = cap.issue && cap.issue.state === "closed";
  const incoherent = head.onMain === "fail" && (claimsDone || issueClosed);
  // Drift between capsule verdict and issue state.
  const issueDrift =
    (head.onMain === "pass" && cap.issue && cap.issue.state === "open") ||
    (head.onMain === "fail" && issueClosed);
  return { ...cap, triage: { ...t, head, snapshotVerdict, incoherent, issueDrift } };
}

// Blast-radius priority score — higher = more urgent worklist item.
function triageScore(cap) {
  const t = cap.triage;
  let s = 0;
  if (t.incoherent) s += 10000;                 // contradictions float to top
  if (t.head.onMain === "fail") s += 4000;      // still broken on main
  if (t.head.onMain === "inconclusive") s += 1500; // can't tell — needs work
  s += (t.impact?.pulling || 0) * 8;            // downstream demand
  s += (t.impact?.blocked || 0) * 60;           // blocked agents weigh most
  if (cap.visibility !== "published") s -= 500; // drafts sink
  return s;
}

window.CAPSULES = CAPSULES;
window.CAPSULE_LIFECYCLE = CAPSULE_LIFECYCLE;
window.CAPSULE_FILTERS = CAPSULE_FILTERS;
window.withTriage = withTriage;
window.triageScore = triageScore;

// ── Client watchlist contract ───────────────────────────────────
// The asymmetry: the maintainer console (triage queue) is for the reader who
// explores and acts on a failure. The CLIENT agent that filed the blocker asks
// exactly one question — "am I unblocked, and what's the one action to resume?"
// The watchlist is the human-glanceable render of `ot capsule inbox --json`:
// one row per *intent*, a single status bit, and an action only when green.
//
//   goal      : the client's task phrasing (its intent, not the capsule id)
//   watching  : client subscribed to someone else's capsule (push on unblock)
//   workaround: stop-gap the client can run while still blocked

const WATCH = {
  "cap-1": { goal: "add an OG image route in the edge runtime", workaround: "render on the node runtime instead" },
  "cap-2": { goal: "stream a long completion without truncation" },
  "cap-3": { goal: "route a tool back to a prior node in a graph", watching: true },
  "cap-5": { goal: "compile the attention kernel with dynamic shapes", watching: true, workaround: "run the block in eager mode" },
  "cap-6": { goal: "serve a catch-all docs route with trailing slashes" },
};

// Derive a client-facing watch item, or null if this capsule isn't on the
// client's watchlist (drafts/unpublished, or not filed/subscribed by them).
function deriveWatch(cap) {
  const w = WATCH[cap.id];
  if (!w) return null;
  if (cap.visibility !== "published") return null;
  if (!cap.owner && !w.watching) return null;
  const t = cap.triage;
  const solver = (t.deps?.matrix || []).find(m => m.s === "pass");
  const resolved = t.head.onMain === "pass";
  return {
    id: cap.id,
    goal: w.goal,
    state: resolved ? "resolved" : "blocked",
    resumed: cap.lifecycle === "unblocked",
    watching: !!w.watching,
    error: cap.fail.summary,
    resolvedIn: solver ? `${t.deps.pkg}@${solver.ver}` : null,
    pin: solver ? `pin ${t.deps.pkg}>=${solver.ver}` : null,
    regressed: t.head.survival === "reverted",
    workaround: w.workaround || null,
    issue: cap.issue && cap.issue.number != null ? `${cap.repo}#${cap.issue.number}` : null,
    issueState: cap.issue ? cap.issue.state : null,
    recheck: t.head.recheck,
  };
}

// The structured payload a client agent actually polls.
function inboxJSON(items) {
  return items.map(w => {
    if (w.state === "resolved")
      return { intent: w.goal, state: "resolved", resolved_in: w.resolvedIn, action: `${w.pin}; re-pose intent against new HEAD` };
    const o = { intent: w.goal, state: "blocked" };
    if (w.issue) o.issue = w.issue;
    if (w.regressed) o.note = "previously fixed, regressed on latest";
    if (w.workaround) o.workaround = w.workaround;
    return o;
  });
}

window.WATCH = WATCH;
window.deriveWatch = deriveWatch;
window.inboxJSON = inboxJSON;
