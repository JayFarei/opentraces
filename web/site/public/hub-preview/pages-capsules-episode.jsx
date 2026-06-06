// Trace Capsules — the "usage episode" layer.
//
// Reframe (per strategy): a Capsule is not a sealed repro/test packet. It is a
// PRIVACY-BOUNDED USAGE EPISODE — the journey of one agent trying to use one
// product/dependency, and where it derailed. The journey is the asset; the
// replayable test is just optional evidence.
//
// A good episode carries: intent · product/dependency + version · environment ·
// docs/source consulted · commands/API calls attempted · error/mismatch ·
// workaround attempts · outcome · redaction metadata · (optional) repro/test.
//
// This file adds that episode content + a layered, inspectable redaction model
// ("what leaves the machine"), plus a tiny shared view-store the Tweaks panel
// and the detail view both read (perspective + density).

// ── shared view store (perspective + density) ───────────────────
// A vanilla observable so the floating Tweaks panel (its own React root) and the
// CapsuleDetail (inside the App root) stay in lockstep without prop-drilling.
window.CapView = (function () {
  const KEY = "ot-capview";
  let state = { perspective: "preshare", density: "comfortable" };
  try { const s = JSON.parse(localStorage.getItem(KEY) || "null"); if (s) state = { ...state, ...s }; } catch (e) {}
  const subs = new Set();
  return {
    get: () => state,
    set: (patch) => {
      state = { ...state, ...patch };
      try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {}
      subs.forEach(fn => fn(state));
    },
    subscribe: (fn) => { subs.add(fn); return () => subs.delete(fn); },
  };
})();

function useCapView() {
  const [s, setS] = React.useState(window.CapView.get());
  React.useEffect(() => window.CapView.subscribe(setS), []);
  return s;
}

// ── episode content (the journey) ───────────────────────────────
// intent + product/version + error already live on the capsule; here we add the
// connective tissue: environment, docs consulted, commands attempted, workarounds,
// and the human outcome line.
const EPISODE = {
  "cap-1": {
    env: "Vercel Edge runtime · Next 15.2.1 · node 20.11 (local)",
    docs: [
      { src: "nextjs.org/docs/app/api-reference/file-conventions/metadata/opengraph-image", note: "ImageResponse edge example" },
      { src: "@vercel/og · README", note: "runtime support matrix" },
    ],
    commands: [
      { cmd: "import { ImageResponse } from 'next/og'", kind: "read", result: "ok" },
      { cmd: "export const runtime = 'edge'", kind: "write", result: "ok" },
      { cmd: "next build", kind: "exec", result: "fail" },
    ],
    workarounds: [
      { text: "Set runtime = 'nodejs' instead", result: "Renders — but loses edge latency", kind: "partial" },
      { text: "Pin @vercel/og directly", result: "Same ReferenceError", kind: "fail" },
    ],
    outcome: "Blocked — no edge-compatible path found. Filed to vercel/next.js#58213.",
  },
  "cap-2": {
    env: "node 20 · openai-node 4.71.0",
    docs: [
      { src: "platform.openai.com/docs/api-reference/streaming", note: "delta accumulation" },
      { src: "openai-node · src/streaming.ts", note: "collectStream()" },
    ],
    commands: [
      { cmd: "client.chat.completions.create({ stream: true })", kind: "exec", result: "ok" },
      { cmd: "for await (const chunk of stream) …", kind: "read", result: "fail" },
    ],
    workarounds: [
      { text: "Pass max_tokens explicitly", result: "Completes, but caps the output", kind: "partial" },
    ],
    outcome: "Resolved — fix #1191 removed the default max_tokens. Passes on main.",
  },
  "cap-3": {
    env: "python 3.12 · langchain 0.3.18",
    docs: [
      { src: "python.langchain.com/docs/.../runnable_sequence", note: "composition rules" },
      { src: "langchain_core/runnables/base.py", note: "invoke()" },
    ],
    commands: [
      { cmd: "seq = node_a | node_b | route_back", kind: "write", result: "ok" },
      { cmd: "seq.invoke(payload)", kind: "exec", result: "fail" },
    ],
    workarounds: [
      { text: "Add a manual visited-set guard", result: "Works, but leaks graph logic into app code", kind: "partial" },
    ],
    outcome: "Unblocked — depth guard added (raises GraphCycleError). Client resumed & shipped.",
  },
  "cap-4": {
    env: "node 20 · @supabase/supabase-js 2.46 · local supabase",
    docs: [
      { src: "supabase.com/docs/guides/api/service-role", note: "bypasses RLS" },
      { src: "supabase/migrations/0007_rls.sql", note: "events policy" },
    ],
    commands: [
      { cmd: "createClient(url, SERVICE_ROLE_KEY)", kind: "write", result: "ok" },
      { cmd: "db.from('events').insert(row)", kind: "exec", result: "fail" },
    ],
    workarounds: [
      { text: "Add a permissive insert policy", result: "Works, but weakens RLS for everyone", kind: "partial" },
    ],
    outcome: "Captured locally — draft, not yet shared.",
  },
  "cap-5": {
    env: "cuda 12.4 · torch 2.6.0 · A100",
    docs: [
      { src: "pytorch.org/docs/stable/torch.compiler_dynamic_shapes", note: "dynamic shapes" },
      { src: "torch/_dynamo/symbolic_convert.py", note: "guard insertion" },
    ],
    commands: [
      { cmd: "torch.compile(block, dynamic=True)", kind: "write", result: "ok" },
      { cmd: "compiled(x)  # forward", kind: "exec", result: "fail" },
    ],
    workarounds: [
      { text: "Run the block in eager mode", result: "Works, loses the compile speedup", kind: "partial" },
      { text: "mark_dynamic() on dim 1", result: "Still graph-breaks", kind: "fail" },
    ],
    outcome: "In progress — branch fix is unproven. Still fails on main.",
  },
  "cap-6": {
    env: "node 20 · next 15.1.6",
    docs: [
      { src: "nextjs.org/docs/app/api-reference/next-config-js/trailingSlash", note: "config" },
      { src: "next · app-router/match.ts", note: "route matcher" },
    ],
    commands: [
      { cmd: "GET /docs/a/b", kind: "read", result: "ok" },
      { cmd: "GET /docs/a/b/", kind: "read", result: "fail" },
    ],
    workarounds: [
      { text: "Redirect in middleware", result: "Works, but double-redirects on deep paths", kind: "partial" },
    ],
    outcome: "Regressed — the fix was reverted; fails on main again.",
  },
  "cap-7": {
    env: "node 20 · openai-node 4.72.0",
    docs: [
      { src: "platform.openai.com/docs/guides/function-calling", note: "arguments is a JSON string" },
      { src: "openai-node · ChatCompletion.d.ts", note: "typings imply an object" },
    ],
    commands: [
      { cmd: "const args = call.function.arguments", kind: "read", result: "ok" },
      { cmd: "args.city", kind: "exec", result: "fail" },
    ],
    workarounds: [
      { text: "JSON.parse(arguments) first", result: "Works — but docs & typings still mislead", kind: "partial" },
    ],
    outcome: "Triaged — docs/typings mismatch confirmed. Recheck inconclusive (no key in sandbox).",
  },
  "cap-8": {
    env: "node 20 · internal exporter",
    docs: [
      { src: "edge-traces · schema", note: "ctx_tree column" },
      { src: "export/parquet.ts", note: "schema-on-write coercion" },
    ],
    commands: [
      { cmd: "npm run export -- --parquet", kind: "exec", result: "fail" },
    ],
    workarounds: [
      { text: "Coerce null reasoning to an empty span", result: "Keeps the column, loses the null signal", kind: "partial" },
    ],
    outcome: "Sealed locally — security scan still running.",
  },
};

// Fallback episode synthesised from the capsule's own fields, so any capsule
// renders even without a curated entry.
function deriveEpisode(cap) {
  const e = EPISODE[cap.id];
  if (e) return e;
  const leaf = cap.ctx || [];
  const readNode = leaf.find(n => n.kind === "read");
  const execNode = leaf.find(n => n.kind === "exec");
  return {
    env: (cap.triage.deps?.runtime || "runtime") + " · " + (cap.triage.deps?.pkg || cap.repo.split("/")[1]),
    docs: [readNode && { src: readNode.label, note: "consulted" }].filter(Boolean),
    commands: [execNode && { cmd: execNode.label, kind: "exec", result: "fail" }].filter(Boolean),
    workarounds: [],
    outcome: cap.fail.summary,
  };
}

// ── layered, inspectable redaction ("what leaves the machine") ───
// Per strategy: secret scanners are necessary but not sufficient. Redaction is
// LAYERED by category — secrets, PII/customer names, internal URLs, file paths,
// prompt/message contents, proprietary logic — and each layer is inspectable:
// you can see what was caught and exactly how it was handled before sharing.
//
// action ∈ "stripped" | "masked" | "hashed" | "normalized" | "kept" | "clean" | "pending"
const REDACT_EXTRAS = {
  "cap-1": {
    urls: { count: 1, action: "stripped", samples: ["https://preview-•••••.vercel.app"] },
    paths: { count: 3, action: "normalized", samples: ["/Users/•••/site/app/og  →  <repo>/app/og"] },
    biz: { count: 0, action: "clean" },
  },
  "cap-2": {
    urls: { count: 0, action: "clean" },
    paths: { count: 2, action: "normalized", samples: ["/home/•••/svc  →  <repo>"] },
    biz: { count: 1, action: "stripped", samples: ["prompt body (1 system message)"] },
  },
  "cap-3": {
    urls: { count: 0, action: "clean" },
    paths: { count: 4, action: "normalized", samples: ["~/work/•••/graph  →  <repo>/graph"] },
    biz: { count: 2, action: "stripped", samples: ["routing-policy node names", "internal tool descriptions"] },
  },
  "cap-4": {
    urls: { count: 2, action: "stripped", samples: ["https://••••••.supabase.co", "postgres://•••@db.•••:5432"] },
    paths: { count: 2, action: "normalized", samples: ["/Users/•••/api  →  <repo>/api"] },
    biz: { count: 1, action: "pending", samples: ["events table column names — under review"] },
  },
  "cap-5": {
    urls: { count: 0, action: "clean" },
    paths: { count: 5, action: "normalized", samples: ["/mnt/•••/models  →  <repo>/models"] },
    biz: { count: 1, action: "stripped", samples: ["model architecture dims (d_model, n_heads)"] },
  },
  "cap-6": {
    urls: { count: 1, action: "stripped", samples: ["https://docs.•••••.internal"] },
    paths: { count: 2, action: "normalized", samples: ["/srv/•••/docs  →  <repo>/app/docs"] },
    biz: { count: 0, action: "clean" },
  },
  "cap-7": {
    urls: { count: 0, action: "clean" },
    paths: { count: 1, action: "normalized", samples: ["/Users/•••/agent  →  <repo>/src"] },
    biz: { count: 1, action: "stripped", samples: ["tool schema (weather lookup)"] },
  },
  "cap-8": {
    urls: { count: 1, action: "pending", samples: ["s3://•••-edge-traces — under review"] },
    paths: { count: 3, action: "normalized", samples: ["/data/•••/export  →  <repo>/export"] },
    biz: { count: 2, action: "pending", samples: ["customer dataset row sample", "column dictionary"] },
  },
};

const REDACT_META = {
  secrets: { label: "Secrets & API keys", icon: "lock" },
  pii: { label: "PII & customer names", icon: "user" },
  urls: { label: "Internal URLs & hosts", icon: "globe" },
  paths: { label: "Local file paths", icon: "file" },
  prompt: { label: "Prompt & message bodies", icon: "conversation" },
  biz: { label: "Proprietary logic", icon: "brain" },
};

const REDACT_TONE = {
  stripped: { word: "stripped", tone: "git" },
  masked: { word: "masked", tone: "git" },
  hashed: { word: "hashed", tone: "git" },
  normalized: { word: "normalized", tone: "read" },
  kept: { word: "kept · intentional", tone: "plan" },
  clean: { word: "none found", tone: "mute" },
  pending: { word: "needs review", tone: "user" },
};

// Build the full layered redaction report for a capsule.
function buildRedaction(cap) {
  const x = REDACT_EXTRAS[cap.id] || {};
  const sec = cap.scrub.secrets;
  const pii = cap.scrub.pii;
  const layers = [
    { key: "secrets", count: sec, action: sec > 0 ? "stripped" : "clean",
      samples: sec > 0 ? ["sk-••••••••  (blocked before seal)"] : [],
      note: sec > 0 ? "Caught by the secret scanner — never written to the capsule." : "Scanner found no keys or tokens." },
    { key: "pii", count: pii, action: pii > 0 ? "masked" : "clean",
      samples: pii > 0 ? ["user email → usr_••••@••••"] : [],
      note: pii > 0 ? "Names & emails masked; identifiers hashed." : "No personal data detected." },
    { key: "urls", count: x.urls?.count ?? 0, action: x.urls?.action || "clean", samples: x.urls?.samples || [],
      note: "Hostnames stripped; only the public package & repo remain." },
    { key: "paths", count: x.paths?.count ?? 0, action: x.paths?.action || "clean", samples: x.paths?.samples || [],
      note: "Absolute paths rewritten to repo-relative." },
    { key: "prompt", count: x.biz ? 1 : 0, action: "kept",
      samples: ["intent + the failing step only"],
      note: "Narrow by default — only the failing intent leaves, not the full session." },
    { key: "biz", count: x.biz?.count ?? 0, action: x.biz?.action || "clean", samples: x.biz?.samples || [],
      note: "Proprietary algorithm / schema details removed or held for review." },
  ];
  const reviewed = layers.filter(l => l.action !== "pending").length;
  const pending = layers.filter(l => l.action === "pending");
  const totalHandled = layers.reduce((a, l) => a + (l.action === "clean" || l.action === "kept" ? 0 : l.count), 0);
  return { layers, reviewed, pendingCount: pending.length, totalHandled, scanned: cap.scrub.scanned, anonymised: cap.scrub.anonymised, state: cap.scrub.state };
}

Object.assign(window, { useCapView, EPISODE, deriveEpisode, REDACT_META, REDACT_TONE, buildRedaction });
