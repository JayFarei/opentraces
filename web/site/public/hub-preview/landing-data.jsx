// Mock data for landing pages — heatmap activity, traces, runs, samples

// Generate one year of contribution data (52 weeks × 7 days)
// Each cell: { count, repos: { repoId: count }, datasets: { datasetName: count } }
function buildHeatmap(cfg = {}) {
  const REPOS = cfg.repos || ["jayfarei/opentraces", "jayfarei/datafetch", "jayfarei/lazymem", "jayfarei/clarify", "jayfarei/open-data", "jayfarei/koreader-kiosk"];
  const DATASETS = cfg.datasets || ["claude-eval-v3", "keystrokes-2026", "edge-traces", "rag-fixtures", "shortcuts-bench"];
  const cells = [];
  // Seeded pseudo-random
  let seed = cfg.seed || 13371337;
  const rnd = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
  };
  // 52 weeks × 7 days = 364 days, ending today (Apr 29 2026)
  const end = new Date(Date.UTC(2026, 3, 29));
  for (let w = 0; w < 53; w++) {
    const week = [];
    for (let d = 0; d < 7; d++) {
      const daysAgo = (52 - w) * 7 + (6 - d);
      const date = new Date(end.getTime() - daysAgo * 86400000);
      // Bias toward weekdays + recent months
      const isWeekend = d === 0 || d === 6;
      const recencyBoost = Math.max(0, 1 - daysAgo / 200);
      let p = Math.min(0.95, (0.35 + recencyBoost * 0.4) * (cfg.density || 1));
      if (isWeekend) p *= (cfg.weekend || 0.3);
      let count = 0;
      const repos = {};
      const datasets = {};
      if (rnd() < p) {
        count = Math.max(1, Math.round((2 + rnd() * (5 + recencyBoost * 6)) * (cfg.scale || 1)));
        // Most active days span 2-3 DISTINCT projects (≈75%), so the
        // "By project" heatmap gets real color mixtures to portray.
        const pool = [...REPOS];
        for (let i = pool.length - 1; i > 0; i--) {
          const j = Math.floor(rnd() * (i + 1));
          [pool[i], pool[j]] = [pool[j], pool[i]];
        }
        const nRepos = Math.min(count, rnd() < 0.25 ? 1 : 2 + Math.floor(rnd() * 2));
        const weights = Array.from({ length: nRepos }, () => 0.35 + rnd());
        const wSum = weights.reduce((a, b) => a + b, 0);
        let remaining = count;
        for (let i = 0; i < nRepos && remaining > 0; i++) {
          const take = i === nRepos - 1
            ? remaining
            : Math.min(remaining, Math.max(1, Math.round(count * weights[i] / wSum)));
          repos[pool[i]] = take;
          remaining -= take;
        }
        // Independent: roughly 65% of traces are tied to a dataset
        let dsRemaining = Math.round(count * (0.5 + rnd() * 0.4));
        const nDs = dsRemaining > 0 ? 1 + Math.floor(rnd() * 2) : 0;
        for (let i = 0; i < nDs && dsRemaining > 0; i++) {
          const ds = DATASETS[Math.floor(rnd() * DATASETS.length)];
          const take = i === nDs - 1 ? dsRemaining : Math.max(1, Math.floor(rnd() * dsRemaining));
          datasets[ds] = (datasets[ds] || 0) + take;
          dsRemaining -= take;
        }
      }
      week.push({ date, count, repos, datasets });
    }
    cells.push(week);
  }
  return cells;
}

const HEATMAP = buildHeatmap();

// Recent traces with synthetic minimap fingerprints. Generates n traces
// walking back from "now" (~2 months at n=220); the first 24 are stable
// regardless of n (suffix randomness only kicks in past the title list).
function buildRecentTraces(n = 24, cfg = {}) {
  const titles = cfg.titles || [
    "Redesign keyboard shortcuts",
    "Stage panel reorder",
    "Fix retry loop in fetcher",
    "Add streaming endpoint tests",
    "RAG eval harness pass 3",
    "Migrate auth helpers",
    "Quantization regression",
    "Wire trace minimap to viewer",
    "Refactor commit grouping",
    "Tighten graph shortcuts",
    "Land docs propagation skill",
    "Investigate inbox empty bug",
    "Push action moved to staged panel",
    "Hover control buttons for shortcuts",
    "Trail visualization tweaks",
    "Privacy redaction pipeline",
    "Filter sidebar counts",
    "Theme swap flicker fix",
    "Brand wordmark refresh",
    "Sticky header compact mode",
    "Heatmap year roll",
    "Dataset sync indicator",
    "Workflow scheduler retries",
    "Repo overview skeleton",
  ];
  const repos = cfg.repos || ["jayfarei/opentraces", "jayfarei/datafetch", "jayfarei/lazymem", "jayfarei/clarify", "jayfarei/open-data", "jayfarei/koreader-kiosk"];
  const agents = [
    { name: "Claude Code", model: "Sonnet 4.5", icon: "claude" },
    { name: "Codex", model: "GPT-5.3 Codex", icon: "codex" },
    { name: "Cursor", model: "Claude 4.1", icon: "cursor" },
    { name: "Gemini CLI", model: "Gemini 2.5 Pro", icon: "gemini" },
    { name: "OpenCode", model: "GPT-5 Mini", icon: "opencode" },
  ];
  const statuses = ["committed", "committed", "committed", "pushed", "working", "failed"];
  const datasetByRepo = cfg.datasetByRepo || {
    "jayfarei/opentraces": ["shortcuts-bench"],
    "jayfarei/datafetch": ["edge-traces"],
    "jayfarei/lazymem": ["claude-eval-v3"],
    "jayfarei/clarify": ["rag-fixtures"],
    "jayfarei/open-data": [],
    "jayfarei/koreader-kiosk": ["keystrokes-2026"],
  };

  let seed = cfg.seed || 99999;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };

  const now = new Date(Date.UTC(2026, 3, 29, 14, 0));
  const out = [];
  const tails = ["", " — follow-up", " (round 2)", " — cleanup", " regression", " — polish", "", " retry"];
  let cursor = now.getTime();
  for (let i = 0; i < n; i++) {
    cursor -= cfg.stepMinMs != null
      ? cfg.stepMinMs + Math.floor(rnd() * (cfg.stepJitterMs || 3600e3))
      : (1 + Math.floor(rnd() * 8)) * 3600 * 1000 + Math.floor(rnd() * 7200 * 1000);
    const fingerprint = buildFingerprint(rnd, 28 + Math.floor(rnd() * 36));
    const repo = repos[Math.floor(rnd() * repos.length)];
    const dsList = datasetByRepo[repo] || [];
    const dataset = dsList.length > 0 && rnd() > 0.35 ? dsList[Math.floor(rnd() * dsList.length)] : null;
    const title = i < titles.length
      ? titles[i]
      : titles[i % titles.length] + tails[Math.floor(rnd() * tails.length)];
    out.push({
      id: hexId(rnd),
      title: title,
      repo,
      dataset,
      turns: 6 + Math.floor(rnd() * 80),
      agent: agents[Math.floor(rnd() * agents.length)],
      status: statuses[Math.floor(rnd() * statuses.length)],
      timestamp: new Date(cursor),
      duration_s: 200 + Math.floor(rnd() * 4000),
      cost: rnd() * 12,
      tokens: 4000 + Math.floor(rnd() * 180000),
      steps: 30 + Math.floor(rnd() * 200),
      comments: Math.floor(rnd() * 9),
      visibility: rnd() > 0.4 ? "public" : "private",
      fingerprint,
    });
  }
  return out;
}

function buildFingerprint(rnd, n) {
  // weighted classes for visual variety: think/read/exec/write/plan/user
  const classes = ["user","plan","think","read","exec","write"];
  const weights = [0.04, 0.10, 0.30, 0.18, 0.18, 0.20];
  const out = [];
  for (let i = 0; i < n; i++) {
    let r = rnd();
    let c = "think";
    for (let j = 0; j < classes.length; j++) {
      if (r < weights[j]) { c = classes[j]; break; }
      r -= weights[j];
    }
    out.push(c);
  }
  return out;
}

function hexId(rnd) {
  const a = Math.floor(rnd() * 0xfff).toString(16).padStart(3, "0");
  const b = Math.floor(rnd() * 0xfffff).toString(16).padStart(5, "0");
  return `${a}·${b}`;
}

const ALL_TRACES = buildRecentTraces(220);
const RECENT_TRACES = ALL_TRACES.slice(0, 24);

// Traces for one heatmap day — the heatmap cell is the source of truth
// (count + per-repo/dataset mix), so the drill-down always matches what
// the cell shows. Deterministic per date, so revisiting a day is stable.
function tracesForHeatmapDay(cell) {
  const date = cell.date;
  let seed = (Math.floor(date.getTime() / 86400000) * 2654435761) & 0x7fffffff;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const repoPool = [];
  Object.entries(cell.repos || {}).forEach(([r, n]) => { for (let i = 0; i < n; i++) repoPool.push(r); });
  const dsPool = Object.keys(cell.datasets || {});
  const src = window.ALL_TRACES || ALL_TRACES; // follows the active account
  const out = [];
  for (let i = 0; i < cell.count; i++) {
    const base = src[Math.floor(rnd() * src.length)];
    const hour = 7 + Math.floor(rnd() * 13);
    out.push({
      ...base,
      id: hexId(rnd),
      repo: repoPool.length ? repoPool[i % repoPool.length] : base.repo,
      dataset: dsPool.length && rnd() > 0.45 ? dsPool[Math.floor(rnd() * dsPool.length)] : null,
      timestamp: new Date(date.getTime() + hour * 3600e3 + Math.floor(rnd() * 3500e3)),
      fingerprint: buildFingerprint(rnd, 28 + Math.floor(rnd() * 36)),
    });
  }
  return out.sort((a, b) => b.timestamp - a.timestamp);
}

// Top agents this period
const TOP_AGENTS = [
  { name: "Claude Code", count: 47, icon: "claude" },
  { name: "Cursor", count: 31, icon: "cursor" },
  { name: "Codex", count: 22, icon: "codex" },
  { name: "Gemini CLI", count: 14, icon: "gemini" },
];

// Workflow runs (synthetic)
function buildWorkflowRuns(workflowId) {
  let seed = workflowId.split("").reduce((a,c) => a * 31 + c.charCodeAt(0), 7) | 0;
  seed = Math.abs(seed);
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const dsNames = Object.values(window.DATASET_DEFS || DATASET_DEFS).map(d => d.name);
  const out = [];
  let t = Date.UTC(2026, 3, 29, 8, 0);
  for (let i = 0; i < 14; i++) {
    t -= (8 + Math.floor(rnd() * 18)) * 3600 * 1000;
    const ok = rnd() > 0.18;
    out.push({
      id: hexId(rnd),
      timestamp: new Date(t),
      status: ok ? "ok" : "failed",
      duration_s: 30 + Math.floor(rnd() * 600),
      rows: 100 + Math.floor(rnd() * 12000),
      dataset: dsNames[Math.floor(rnd() * Math.min(3, dsNames.length))],
      fingerprint: buildFingerprint(rnd, 24 + Math.floor(rnd() * 24)),
    });
  }
  return out;
}

// Workflow definitions
const WORKFLOW_DEFS = {
  "wf-eval-suite": {
    name: "eval-suite",
    description: "Run the standard regression eval against the latest model snapshot. Pulls fixtures, executes a deterministic prompt sweep, scores against the golden set, and produces a delta report.",
    instructions: `# eval-suite

1. Pull fixtures from \`jayfarei/opentraces:fixtures/eval/\`.
2. For each fixture, run the agent with deterministic seeds.
3. Score the output against the golden set using the BLEU + structural diff scorer.
4. Aggregate into a single dataset row per fixture.
5. Compare against the previous run; emit a delta report into the trace.`,
    schedule: "Nightly at 02:00 UTC",
    lastFired: "3h ago",
    nextFire: "in 21h",
    triggers: ["schedule", "manual"],
    producedDatasets: ["claude-eval-v3", "edge-traces"],
  },
  "wf-nightly-bench": {
    name: "nightly-bench",
    description: "Throughput and latency benchmarks across the supported model matrix.",
    instructions: `# nightly-bench

1. Spin up isolated runners per model.
2. Issue a fixed prompt sequence with cold + warm cache passes.
3. Capture wall-clock latency, token throughput, error rate.
4. Append to bench history dataset.`,
    schedule: "Nightly at 04:00 UTC",
    lastFired: "5h ago",
    nextFire: "in 19h",
    triggers: ["schedule"],
    producedDatasets: ["keystrokes-2026"],
  },
  "wf-rag-grader": {
    name: "rag-grader",
    description: "Grade RAG pipeline outputs against a curated answer key. Surfaces regressions in retrieval and citation accuracy.",
    instructions: `# rag-grader

1. Load \`rag-fixtures\` dataset (questions + grounding docs).
2. Run the RAG pipeline under test for each question.
3. Grade citation precision, answer faithfulness, and groundedness via a judge model.
4. Emit per-question scores + an aggregate report.`,
    schedule: "On-demand",
    lastFired: "yesterday",
    nextFire: null,
    triggers: ["manual", "PR open"],
    producedDatasets: ["rag-fixtures"],
  },
  "wf-shortcut-redesign": {
    name: "shortcut-redesign",
    description: "Re-runs the keyboard shortcut survey across the viewer, capturing every binding and surfacing collisions.",
    instructions: `# shortcut-redesign

1. Walk the viewer source tree and extract \`useKeybinding\` calls.
2. Cross-reference against the OS shortcut conflict list.
3. Generate a markdown table of all bindings + collisions.`,
    schedule: "Weekly",
    lastFired: "2 days ago",
    nextFire: "in 5 days",
    triggers: ["schedule", "manual"],
    producedDatasets: ["shortcuts-bench"],
  },
  "wf-doc-extract": {
    name: "doc-extract",
    description: "Detect recent code changes and propagate doc updates across marketing, mkdocs, llms.txt and READMEs.",
    instructions: `# doc-extract

1. Compare HEAD against the last doc-sync commit.
2. For each changed surface, locate the corresponding doc files via the surface map.
3. Draft suggested edits and emit them as a unified diff for review.`,
    schedule: "On commit to main",
    lastFired: "12 min ago",
    nextFire: null,
    triggers: ["webhook", "manual"],
    producedDatasets: [],
  },
};

// Dataset details
const DATASET_DEFS = {
  "ds-claude-eval-v3": {
    name: "claude-eval-v3",
    description: "Standard regression eval rows for the Claude family.",
    rows: 12407,
    cols: 8,
    workflow: "wf-eval-suite",
    workflow_label: "eval-suite",
    remote: "synced",
    remote_kind: "hf",
    remote_repo: "jayfarei/claude-eval-v3",
    remote_url: "https://huggingface.co/datasets/jayfarei/claude-eval-v3",
    auto_policy: false,
    inbox_count: 0,
    staging_count: 3,
    last_run: "3h ago",
    last_update: "3h ago",
    pending_push: 0,
    local_commits: 14,
    remote_commits: 14,
    sample_columns: ["fixture_id", "prompt", "expected", "actual", "score", "judge_notes", "duration_ms", "tokens"],
    sample_rows: [
      ["fix.0421", "Summarize tax filing requirements for an S-Corp…", "S-Corps file 1120-S annually…", "S-Corps must file Form 1120-S each year…", 0.92, "minor formatting drift", 1840, 412],
      ["fix.0422", "Write a recursive descent parser for…", "function parse(input) {…", "function parse(input) {…", 0.98, "matches golden", 2210, 587],
      ["fix.0423", "Translate the following SQL to a Pandas equivalent", "df.groupby('region')['revenue'].sum()", "df.groupby('region').revenue.sum()", 0.88, "equivalent but stylistic mismatch", 980, 198],
      ["fix.0424", "Identify the bug in this snippet", "off-by-one in range(0, n)", "off-by-one in range(0, n)", 1.00, "exact", 1130, 244],
    ],
  },
  "ds-keystrokes-2026": {
    name: "keystrokes-2026",
    description: "Per-keystroke latency captures across the editor matrix.",
    rows: 8127,
    cols: 6,
    workflow: "wf-nightly-bench",
    workflow_label: "nightly-bench",
    remote: "synced",
    remote_kind: "hf",
    remote_repo: "jayfarei/keystrokes-2026",
    remote_url: "https://huggingface.co/datasets/jayfarei/keystrokes-2026",
    auto_policy: true,
    inbox_count: 0,
    staging_count: 0,
    last_run: "5h ago",
    last_update: "5h ago",
    pending_push: 0,
    local_commits: 31,
    remote_commits: 31,
    sample_columns: ["session", "editor", "key", "p50_ms", "p99_ms", "errors"],
    sample_rows: [
      ["s.0001", "viewer/web", "ArrowDown", 4, 12, 0],
      ["s.0001", "viewer/web", "Enter", 6, 18, 0],
      ["s.0002", "viewer/tui", "j", 2, 7, 0],
      ["s.0002", "viewer/tui", "k", 2, 7, 0],
    ],
  },
  "ds-edge-traces": {
    name: "edge-traces",
    description: "Sample of long-tail / unusual traces flagged by the eval suite.",
    rows: 2284,
    cols: 7,
    workflow: "wf-eval-suite",
    workflow_label: "eval-suite",
    remote: "ahead",
    remote_kind: "hf",
    remote_repo: "openai/edge-traces",
    remote_url: "https://huggingface.co/datasets/openai/edge-traces",
    auto_policy: false,
    inbox_count: 12,
    staging_count: 5,
    last_run: "3h ago",
    last_update: "2 days ago",
    pending_push: 3,
    local_commits: 9,
    remote_commits: 6,
    sample_columns: ["trace_id", "anomaly", "severity", "agent", "duration_s", "tokens", "notes"],
    sample_rows: [
      ["6ea·f9ff4", "long-think-loop", "low", "Claude Code", 4116, 21900, "minimap had 30+ contiguous think steps"],
      ["a31·0c4d1", "tool-thrash", "med", "Cursor", 822, 8810, "alternating Read/Edit on same file 14×"],
      ["d04·7711a", "context-overrun", "high", "Gemini CLI", 1730, 184000, "hit 92% of 200k window"],
    ],
  },
  "ds-rag-fixtures": {
    name: "rag-fixtures",
    description: "Curated questions + grounding docs for grading the RAG pipeline.",
    rows: 947,
    cols: 5,
    workflow: "wf-rag-grader",
    workflow_label: "rag-grader",
    remote: "behind",
    remote_kind: "hf",
    remote_repo: "langchain-ai/rag-fixtures",
    remote_url: "https://huggingface.co/datasets/langchain-ai/rag-fixtures",
    auto_policy: false,
    inbox_count: 4,
    staging_count: 0,
    last_run: "yesterday",
    last_update: "yesterday",
    pending_push: 0,
    local_commits: 4,
    remote_commits: 7,
    sample_columns: ["question_id", "question", "expected_citation", "difficulty", "tags"],
    sample_rows: [
      ["q.001", "What is the LangChain Hub release cadence?", "docs/releases.md#cadence", "easy", "release,langchain"],
      ["q.002", "Which retriever does the agent template default to?", "templates/agent/retriever.md", "med", "agent,retriever"],
      ["q.003", "How does multi-tenant isolation work?", "docs/multitenant.md#isolation", "hard", "auth,tenancy"],
    ],
  },
  "ds-shortcuts-bench": {
    name: "shortcuts-bench",
    description: "Snapshot of every keybinding in the viewer + collisions.",
    rows: 312,
    cols: 4,
    workflow: "wf-shortcut-redesign",
    workflow_label: "shortcut-redesign",
    remote: "local",
    remote_kind: null,
    remote_repo: null,
    remote_url: null,
    auto_policy: false,
    inbox_count: 27,
    staging_count: 0,
    last_run: "2 days ago",
    last_update: "2 days ago",
    pending_push: 0,
    local_commits: 2,
    remote_commits: 0,
    sample_columns: ["binding", "scope", "action", "collisions"],
    sample_rows: [
      ["space", "viewer/global", "next-step", "0"],
      ["q", "viewer/global", "exit", "0"],
      ["g g", "viewer/graph", "go-to-top", "1 (vim default)"],
      ["?", "viewer/global", "help", "0"],
    ],
  },
};

// Repository details
const REPO_DEFS = {
  "jayfarei/opentraces": {
    ns: "JayFarei", nm: "opentraces",
    description: "Open schema + CLI for repo-local agent trace capture, review, and upload to the Hugging Face Hub.",
    default_branch: "main",
    open_traces: 3,
    total_traces: 47,
    contributors: 1,
    last_push: "12 min ago",
    workflows: ["wf-shortcut-redesign", "wf-doc-extract"],
    datasets: ["ds-shortcuts-bench"],
    working_tree: {
      modified: 4,
      added: 1,
      deleted: 0,
      current_trace: { id: "a31·0c4d1", title: "Sticky grid-head + minimap pass" },
    },
  },
  "jayfarei/datafetch": {
    ns: "JayFarei", nm: "datafetch",
    description: "Search-as-Code adaptive retrieval that crystallises query shape from agent usage, per-tenant, over a polymorphic document store.",
    default_branch: "main",
    open_traces: 1,
    total_traces: 23,
    contributors: 4,
    last_push: "2h ago",
    workflows: ["wf-eval-suite"],
    datasets: ["ds-edge-traces"],
    working_tree: { modified: 0, added: 0, deleted: 0, current_trace: null },
  },
  "jayfarei/lazymem": {
    ns: "JayFarei", nm: "lazymem",
    description: "A ratatUI dashboard for seeing where memory is going across your projects.",
    default_branch: "main",
    open_traces: 0,
    total_traces: 12,
    contributors: 7,
    last_push: "yesterday",
    workflows: ["wf-eval-suite", "wf-nightly-bench"],
    datasets: ["ds-claude-eval-v3"],
    working_tree: { modified: 0, added: 0, deleted: 0, current_trace: null },
  },
  "jayfarei/clarify": {
    ns: "JayFarei", nm: "clarify",
    description: "A Claude Code workflow & skill for a relentless, concept-by-concept clarity pass on a piece of writing.",
    default_branch: "main",
    open_traces: 2,
    total_traces: 18,
    contributors: 3,
    last_push: "4h ago",
    workflows: ["wf-rag-grader"],
    datasets: ["ds-rag-fixtures"],
    working_tree: { modified: 2, added: 0, deleted: 1, current_trace: { id: "d04·7711a", title: "Clarity pass on the persistence RFC" } },
  },
  "jayfarei/open-data": {
    ns: "JayFarei", nm: "open-data",
    description: "A skill for designing futureproof persistence systems where data outlives applications.",
    default_branch: "main",
    open_traces: 0,
    total_traces: 6,
    contributors: 2,
    last_push: "3 days ago",
    workflows: [],
    datasets: [],
    working_tree: { modified: 0, added: 0, deleted: 0, current_trace: null },
  },
  "jayfarei/koreader-kiosk": {
    ns: "JayFarei", nm: "koreader-kiosk",
    description: "Turn your Kindle into a real-time TfL bus departure board.",
    default_branch: "main",
    open_traces: 1,
    total_traces: 9,
    contributors: 5,
    last_push: "1h ago",
    workflows: ["wf-nightly-bench"],
    datasets: ["ds-keystrokes-2026"],
    working_tree: { modified: 7, added: 2, deleted: 0, current_trace: { id: "f23·3a884", title: "E-ink partial refresh ghosting" } },
  },
};

function buildInboxItems(datasetId, count) {
  let seed = datasetId.split("").reduce((a,c) => a * 31 + c.charCodeAt(0), 13) | 0;
  seed = Math.abs(seed);
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const reasons = [
    "novel-output", "schema-drift", "low-confidence", "judge-flagged",
    "duplicate-near", "outlier-cost", "long-think", "tool-thrash"
  ];
  const out = [];
  let t = Date.UTC(2026, 3, 29, 13, 0);
  for (let i = 0; i < count; i++) {
    t -= Math.floor(15 + rnd() * 240) * 60 * 1000;
    out.push({
      id: hexId(rnd),
      reason: reasons[Math.floor(rnd() * reasons.length)],
      added_at: new Date(t),
      preview: ["fix." + (1000 + Math.floor(rnd() * 999)), "score=" + (0.4 + rnd() * 0.6).toFixed(2), "duration=" + (200 + Math.floor(rnd() * 4000)) + "ms"],
      confidence: rnd(),
      staged: false,
    });
  }
  return out;
}

window.buildInboxItems = buildInboxItems;
window.RECENT_TRACES = RECENT_TRACES;
window.ALL_TRACES = ALL_TRACES;
window.tracesForHeatmapDay = tracesForHeatmapDay;
window.HEATMAP = HEATMAP;
// Arenas — reproducible task environments promoted from task families.
// Each pins repos + verify commands; tasks queue in from classifiers.
const ARENA_DEFS = {
  "ar-flaky-tests": { name: "flaky-test-arena", tasks: 48, tier: "silver", pass_rate: 0.62, last_run: "3h ago", source: "flaky-test-hunter" },
  "ar-refactor":    { name: "refactor-guard",   tasks: 26, tier: "gold",   pass_rate: 0.78, last_run: "6h ago", source: "family grader" },
  "ar-edge-og":     { name: "edge-og-arena",    tasks: 12, tier: "bronze", pass_rate: 0.41, last_run: "1d ago", source: "family grader" },
  "ar-rag-verify":  { name: "rag-verify",       tasks: 9,  tier: "bronze", pass_rate: 0.55, last_run: "2d ago", source: "thrash-detector" },
};

window.TOP_AGENTS = TOP_AGENTS;
window.ARENA_DEFS = ARENA_DEFS;
window.WORKFLOW_DEFS = WORKFLOW_DEFS;
window.DATASET_DEFS = DATASET_DEFS;
window.REPO_DEFS = REPO_DEFS;
window.buildWorkflowRuns = buildWorkflowRuns;
window.buildFingerprint = buildFingerprint;

// ── Account-switchable data: personal (Jay) vs org (OpenMake) ──
// org-data.jsx (loaded before this file) supplies the ORG_* configs.
const ORG_READY = !!window.ORG_REPO_DEFS;
const ORG_ALL_TRACES = ORG_READY ? buildRecentTraces(520, {
  repos: Object.keys(window.ORG_REPO_DEFS),
  datasetByRepo: window.ORG_DATASET_BY_REPO,
  titles: window.ORG_TRACE_TITLES,
  seed: 424242,
  stepMinMs: 10 * 60e3, stepJitterMs: 110 * 60e3, // minutes apart, not hours
}) : null;
const ORG_HEATMAP = ORG_READY ? buildHeatmap({
  repos: Object.keys(window.ORG_REPO_DEFS),
  datasets: Object.values(window.ORG_DATASET_DEFS).map(d => d.name),
  seed: 20260712, density: 1.55, weekend: 0.6, scale: 3.4,
}) : null;

window.ACCOUNT_DATA = {
  personal: {
    HEATMAP, ALL_TRACES, RECENT_TRACES,
    REPO_DEFS, DATASET_DEFS, WORKFLOW_DEFS, TOP_AGENTS,
    homeRepo: "jayfarei/opentraces",
  },
  org: ORG_READY ? {
    HEATMAP: ORG_HEATMAP,
    ALL_TRACES: ORG_ALL_TRACES,
    RECENT_TRACES: ORG_ALL_TRACES.slice(0, 24),
    REPO_DEFS: window.ORG_REPO_DEFS,
    DATASET_DEFS: window.ORG_DATASET_DEFS,
    WORKFLOW_DEFS: window.ORG_WORKFLOW_DEFS || WORKFLOW_DEFS,
    TOP_AGENTS: window.ORG_TOP_AGENTS || TOP_AGENTS,
    homeRepo: "openmake/forge",
  } : null,
};
window.applyAccountData = function (a) {
  const d = window.ACCOUNT_DATA[a] || window.ACCOUNT_DATA.personal;
  window.OT_ACCOUNT = d === window.ACCOUNT_DATA.org ? "org" : "personal";
  window.ACCOUNT_HOME_REPO = d.homeRepo;
  window.HEATMAP = d.HEATMAP;
  window.ALL_TRACES = d.ALL_TRACES;
  window.RECENT_TRACES = d.RECENT_TRACES;
  window.REPO_DEFS = d.REPO_DEFS;
  window.DATASET_DEFS = d.DATASET_DEFS;
  window.WORKFLOW_DEFS = d.WORKFLOW_DEFS;
  window.TOP_AGENTS = d.TOP_AGENTS;
};
try { window.applyAccountData(localStorage.getItem("ot-account") || "personal"); }
catch (e) { window.applyAccountData("personal"); }
