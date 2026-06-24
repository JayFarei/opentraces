// Per-project (repo) mock data: full session lists, data-flow graphs, settings.
// Deterministic so every reload looks the same.

const RPD_AGENTS = [
  { name: "Claude Code", model: "Sonnet 4.5",     icon: "claude",   w: 0.40 },
  { name: "Cursor",      model: "Claude 4.1",     icon: "cursor",   w: 0.22 },
  { name: "Codex",       model: "GPT-5.3 Codex",  icon: "codex",    w: 0.18 },
  { name: "Gemini CLI",  model: "Gemini 2.5 Pro", icon: "gemini",   w: 0.12 },
  { name: "OpenCode",    model: "GPT-5 Mini",     icon: "opencode", w: 0.08 },
];

const RPD_TITLES = {
  "jayfarei/opentraces": [
    "Sticky grid-head + minimap pass",
    "Chunk Hub uploads for large ctx_tree payloads",
    "Pulse CLI — weekly momentum summary",
    "Fix retry loop in fetcher on 429",
    "Vertical minimap orientation",
    "Redaction pass before publish",
    "Capture hook for Codex harness",
    "Session schema v0.4 migration",
    "Trail blame — map commits to turns",
    "Keyboard shortcut survey refresh",
    "Doc propagation after CLI rename",
    "Streaming endpoint tests",
    "Hub push resumable uploads",
    "Context window waste detector",
    "Secret scanner false-positive triage",
    "Inline diff viewer for file edits",
    "Bucket compaction job",
    "Trace dedupe on re-capture",
    "Publish gate dry-run mode",
    "CLI onboarding flow polish",
    "Turn-level token accounting",
    "Fix ghost sessions on SIGKILL",
    "Harness version pinning",
    "Export trails as markdown",
    "Settings file watcher",
    "Per-project ignore globs",
    "Upload backoff jitter",
    "Trace index rebuild speedup",
    "Conversation filter sidebar counts",
    "Theme swap flicker fix",
    "Heatmap year roll",
    "Repo overview skeleton",
  ],
  generic: [
    "Refactor module boundaries",
    "Fix flaky integration test",
    "Add CLI flag for dry runs",
    "Profile slow startup path",
    "Update dependency pins",
    "Error message clarity pass",
    "Add retry with backoff",
    "Document configuration options",
    "Cache layer for hot reads",
    "Trim verbose logging",
    "Edge-case handling for empty input",
    "Release notes draft",
  ],
};

function rpdRng(key) {
  let seed = Math.abs(key.split("").reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 17));
  return () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
}

function rpdPickAgent(rnd) {
  let r = rnd();
  for (const a of RPD_AGENTS) {
    if (r < a.w) return { name: a.name, model: a.model, icon: a.icon };
    r -= a.w;
  }
  return { name: RPD_AGENTS[0].name, model: RPD_AGENTS[0].model, icon: RPD_AGENTS[0].icon };
}

const RPD_SESSION_CACHE = {};

// Full session history for a project. Newest first.
function buildRepoSessions(repoId) {
  if (RPD_SESSION_CACHE[repoId]) return RPD_SESSION_CACHE[repoId];
  const def = REPO_DEFS[repoId];
  if (!def) return [];
  const rnd = rpdRng(repoId + "·sessions");
  const titles = RPD_TITLES[repoId] || RPD_TITLES.generic;
  const n = def.total_traces;
  const dsNames = (def.datasets || []).map(d => DATASET_DEFS[d]?.name).filter(Boolean);

  const out = [];
  // Now per relTime() convention: Apr 29 2026, 14:30 UTC
  let cursor = Date.UTC(2026, 3, 29, 13, 40);
  for (let i = 0; i < n; i++) {
    const working = i < def.open_traces; // open sessions are the most recent
    const failed = !working && rnd() < 0.08;
    const status = working ? "working" : failed ? "failed" : (rnd() < 0.55 ? "committed" : "pushed");
    const turns = 4 + Math.floor(rnd() * 86);
    const tokens = 3000 + Math.floor(rnd() * 185000);
    const title = titles[i % titles.length] + (i >= titles.length ? " · pass " + (Math.floor(i / titles.length) + 1) : "");
    const dataset = dsNames.length > 0 && rnd() < 0.42 ? dsNames[Math.floor(rnd() * dsNames.length)] : null;
    out.push({
      id: hexId(rnd),
      title,
      repo: repoId,
      dataset,
      turns,
      agent: rpdPickAgent(rnd),
      status,
      timestamp: new Date(cursor),
      duration_s: 120 + Math.floor(rnd() * 4200),
      cost: 0.2 + rnd() * 9,
      tokens,
      steps: Math.round(turns * (1.6 + rnd())),
      commits: status === "working" ? 0 : Math.floor(rnd() * 5),
      pr: repoId === "jayfarei/opentraces" && !working && rnd() < 0.18
        ? [128, 127, 126, 125][Math.floor(rnd() * 4)] : null,
      fingerprint: buildFingerprint(rnd, 24 + Math.floor(rnd() * 36)),
    });
    // step back 2–22h, with occasional multi-day gaps (weekends)
    cursor -= (2 + Math.floor(rnd() * 20)) * 3600 * 1000;
    if (rnd() < 0.16) cursor -= (24 + Math.floor(rnd() * 40)) * 3600 * 1000;
  }
  RPD_SESSION_CACHE[repoId] = out;
  return out;
}

// Where this project's sessions flowed. Everything is grounded in the real
// CLI surface: workflows project dataset rows (`dataset run`), trails anchor
// commits (`trail blame`), and everything stays retained in the bucket.
function buildRepoFlow(repoId) {
  const def = REPO_DEFS[repoId];
  if (!def) return null;
  if (repoId === "jayfarei/opentraces") {
    return {
      total: 47,
      routes: [
        { id: "wf-shortcut", label: "shortcut-redesign", kind: "workflow", count: 18, color: "var(--c-user)",
          dest: { label: "@shortcuts-bench", sub: "+312 rows projected", kind: "dataset", dsId: "ds-shortcuts-bench" } },
        { id: "trails", label: "trail anchors", kind: "git", count: 22, color: "var(--c-git)",
          dest: { label: "Git history", sub: "41 commits \u00b7 4 PR trails", kind: "pulls" } },
        { id: "bucket", label: "bucket only", kind: "none", count: 7, color: "var(--fg-mute)",
          dest: { label: "Retained evidence", sub: "searchable \u00b7 trace query", kind: "archive" } },
      ],
    };
  }
  // Derived: datasets soak ~40%, trail-anchored commits ~45%, rest bucket-only.
  const rnd = rpdRng(repoId + "\u00b7flow");
  const n = def.total_traces;
  const routes = [];
  let used = 0;
  (def.datasets || []).forEach((dsId, i) => {
    const c = Math.max(1, Math.round((n * 0.4) / def.datasets.length));
    used += c;
    const ds = DATASET_DEFS[dsId];
    routes.push({
      id: dsId, label: ds?.workflow_label || "workflow", kind: "workflow", count: c,
      color: ["var(--c-user)", "var(--c-read)", "var(--c-plan)"][i % 3],
      dest: { label: "@" + (ds?.name || dsId), sub: "+" + Math.round(40 + rnd() * 400) + " rows projected", kind: "dataset", dsId },
    });
  });
  const commits = Math.max(1, Math.round(n * 0.45));
  used += commits;
  routes.push({ id: "trails", label: "trail anchors", kind: "git", count: commits, color: "var(--c-git)",
    dest: { label: "Git history", sub: Math.round(commits * 2.6) + " traced commits", kind: "git" } });
  const rest = Math.max(0, n - used);
  if (rest > 0) routes.push({ id: "bucket", label: "bucket only", kind: "none", count: rest, color: "var(--fg-mute)",
    dest: { label: "Retained evidence", sub: "searchable \u00b7 trace query", kind: "archive" } });
  return { total: n, routes };
}

// Mock per-project settings, limited to what the real CLI exposes:
// tracking mode, review policy, harness hooks, ctx-tree capture (OTLP),
// scanning & redaction (sensitivity + literal redact strings), one HF
// dataset remote with private/public/gated visibility, bucket stats.
const RPD_SETTINGS = {
  "jayfarei/opentraces": {
    path: "~/code/opentraces",
    marker: ".opentraces.json",
    bucket: "~/.opentraces/",
    tracking: "global",          // global | manual | excluded
    review_policy: "review",     // review | auto
    visibility: "public",        // private | public | gated
    harnesses: [
      { id: "claude",   name: "Claude Code", icon: "claude",   installed: true,  version: "0.4.2", last: "12 min ago" },
      { id: "codex",    name: "Codex",       icon: "codex",    installed: true,  version: "0.3.9", last: "2 d ago" },
      { id: "cursor",   name: "Cursor",      icon: "cursor",   installed: true,  version: "0.4.0", last: "6 h ago" },
      { id: "gemini",   name: "Gemini CLI",  icon: "gemini",   installed: false, version: null,    last: null },
      { id: "opencode", name: "OpenCode",    icon: "opencode", installed: false, version: null,    last: null },
    ],
    ctx_capture: false,
    scrub: {
      sensitivity: "medium",     // low | medium | high
      last_scan: "12 min ago \u00b7 0 findings",
      redact_strings: ["INTERNAL_API_KEY", "ACME_STAGING_URL", "jay@acme-internal.io"],
    },
    remote: { repo: "jayfarei/opentraces", connected: true },
    bucket_stats: { size_gb: 1.2, avg_mb: 26 },
  },
};

function getRepoSettings(repoId) {
  if (RPD_SETTINGS[repoId]) return RPD_SETTINGS[repoId];
  const def = REPO_DEFS[repoId] || { nm: repoId };
  const base = RPD_SETTINGS["jayfarei/opentraces"];
  return {
    ...base,
    path: "~/code/" + def.nm,
    tracking: "global",
    review_policy: "review",
    visibility: "private",
    harnesses: base.harnesses.map((h, i) => ({ ...h, installed: i === 0, last: i === 0 ? def.last_push : null })),
    scrub: { ...base.scrub, redact_strings: [] },
    remote: { repo: repoId.split("/")[0] + "/" + def.nm + "-traces", connected: true },
    bucket_stats: { size_gb: +(0.1 + (def.total_traces || 5) * 0.022).toFixed(1), avg_mb: 24 },
  };
}

window.buildRepoSessions = buildRepoSessions;
window.buildRepoFlow = buildRepoFlow;
window.getRepoSettings = getRepoSettings;
