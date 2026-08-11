// Mock data + helpers for the new intelligence pages.
// All concepts are remapped onto coding-agent traces (not customer conversations).

// ─────────────  Palette mapping  ─────────────
// Reuse existing tokens so every page agrees with the trace minimap.
const INTENT_COLORS = [
  "var(--c-user)",
  "var(--c-write)",
  "var(--c-plan)",
  "var(--c-read)",
  "var(--c-exec)",
  "var(--c-git)",
  "var(--c-push)",
  "var(--c-think)",
  "#d9a441",
];

// ─────────────  Intents  ─────────────
// Each intent = a pattern observed across traces. Description is plain prose,
// not a regex — these are interpreted by an LLM grader.
const INTENTS = [
  { id: "in-debug",      name: "Debug investigation", description: "User pasted a stack trace or failing test and the agent walked the call graph to find a root cause.", color: "var(--c-user)", count: 312 },
  { id: "in-refactor",   name: "Refactor & cleanup",   description: "Tightening naming, splitting long files, removing duplication. No behaviour change intended.", color: "var(--c-write)", count: 247 },
  { id: "in-test",       name: "Test authoring",       description: "Writing new tests or extending coverage for newly-shipped logic.", color: "var(--c-plan)", count: 198 },
  { id: "in-doc",        name: "Doc propagation",      description: "Update README, changelog, or inline comments after a code change shipped.", color: "var(--c-read)", count: 156 },
  { id: "in-migration",  name: "API migration",        description: "Moving call-sites from a deprecated API or library to its replacement, in bulk.", color: "var(--c-exec)", count: 98 },
  { id: "in-thrash",     name: "Tool thrash",          description: "Agent alternated Read/Edit on the same file 10+ times without convergence — usually a sign of an unclear plan.", color: "#d9a441", count: 456 },
  { id: "in-spike",      name: "Spike / exploration",  description: "Try-and-toss exploration — agent prototyped an approach with no intent to land it.", color: "var(--c-push)", count: 289 },
  { id: "in-hotfix",     name: "Hotfix",               description: "Targeted patch against a live regression. Short, surgical, no scope creep.", color: "var(--c-think)", count: 134 },
];

// ─────────────  Evals (SOPs for coding agents)  ─────────────
const SOPS = [
  { id: "sop-secrets",    name: "No secrets in commits",        description: "Never stage .env, credentials, *.pem, or any file matching the secret heuristic.", color: "var(--c-error, #d24545)", violations: 612 },
  { id: "sop-tests",      name: "Run tests before commit",      description: "If changed paths include *.test.* or *_test.*, the agent must invoke the test runner before any git commit.", color: "#d9a441", violations: 487 },
  { id: "sop-utils",      name: "Use existing utilities",       description: "Before authoring a helper, grep the repo for an equivalent. Don't reinvent lodash, fmt, or path helpers.", color: "var(--c-plan)", violations: 394 },
  { id: "sop-destructive",name: "Confirm destructive ops",      description: "Pause and surface intent before rm -rf, schema migrations, or force-push.", color: "var(--c-exec)", violations: 312 },
  { id: "sop-scope",      name: "Stop at goal completion",      description: "When the stated goal is met, stop. Don't drift into adjacent refactors the user didn't ask for.", color: "var(--c-user)", violations: 268 },
  { id: "sop-docs",       name: "Update docs alongside code",   description: "Public API changes must be paired with a docs edit in the same commit.", color: "var(--c-read)", violations: 217 },
  { id: "sop-errors",     name: "No silent error swallowing",   description: "Bare except / catch-all without logging or rethrow is forbidden.", color: "var(--c-write)", violations: 184 },
  { id: "sop-issue",      name: "Reference issue in commits",   description: "Commit messages on tracked branches must include #<issue> or the branch's tracker ticket.", color: "var(--c-git)", violations: 142 },
  { id: "sop-forcepush",  name: "No force-push to main",        description: "Never run git push --force on the default branch. Always open a fresh branch instead.", color: "var(--c-push)", violations: 96 },
  { id: "sop-citations",  name: "Cite for non-obvious patterns",description: "When introducing a new pattern, link to the doc / RFC / prior commit it came from.", color: "var(--c-think)", violations: 71 },
];

// ─────────────  Alerts  ─────────────
const ALERTS = [
  { id: "al-fail",     name: "trace failure rate > 5%",     condIf: "trace_status='failed' rate exceeds 5% over the past 2 hours", condThen: "Send the top 5 failing repos + a fingerprint sample for each",   channel: "slack/openmake",   every: "2h", last: "about 3 hours ago", status: "active" },
  { id: "al-ctx",      name: "context overflow > 90%",      condIf: "any trace consumes more than 90% of its window over the past hour", condThen: "Include the offending trace IDs and the agents that hit ceiling", channel: "slack/openmake",   every: "1h", last: "about 8 hours ago", status: "active" },
  { id: "al-thrash",   name: "tool thrash > 10/trace",      condIf: "alternating Read/Edit on the same file exceeds 10 per trace", condThen: "List the files and the loop length",                                  channel: "slack/openmake",   every: "1h", last: "Never",              status: "active" },
  { id: "al-secret",   name: "SOP violation: secrets",      condIf: "Any SOP violation matching 'No secrets in commits' detected", condThen: "Send the diff hunk and the conversation ID for immediate review",      channel: "slack/openmake",   every: "1h", last: "about 6 hours ago", status: "active" },
  { id: "al-cost",     name: "cost per trace > $10",        condIf: "single-trace token cost exceeds $10 in the past 4 hours", condThen: "Include affected harnesses and the offending repo",                       channel: "slack/openmake",   every: "4h", last: "Never",              status: "paused" },
];

// ─────────────  Self-Improving heal jobs  ─────────────
const HEAL_JOBS = [
  { id: "hj-9",  name: "Tool thrash spike: trail.jsx",         when: "18/05/2026, 08:13",  status: "completed" },
  { id: "hj-8",  name: "Long-think loop on minimap.tsx",        when: "18/05/2026, 02:13",  status: "completed" },
  { id: "hj-7",  name: "SOP violation: secrets in fixtures",    when: "18/05/2026, 09:13",  status: "creating_pr" },
  { id: "hj-6",  name: "Refactor regression: minimap perf",     when: "17/05/2026, 16:13",  status: "completed" },
  { id: "hj-5",  name: "Trace failure rate > 5%",                when: "17/05/2026, 20:13",  status: "failed" },
  { id: "hj-4",  name: "API migration: legacy fetcher",         when: "17/05/2026, 11:02",  status: "completed" },
  { id: "hj-3",  name: "Doc drift after streaming endpoint",    when: "16/05/2026, 22:41",  status: "skipped" },
];

const HEAL_CONFIG = {
  repo: "jayfarei/opentraces",
  branch: "main",
  connected_at: "06/05/2026",
  model: "claude-sonnet-4-5-20250514",
  heals_per_day: 10,
  cooldown_min: 60,
  active: true,
};

// ─────────────  Spotlight suggested searches  ─────────────
const SPOTLIGHT_SUGGESTIONS = [
  "traces that hit the context window limit",
  "refactors that introduced new failures",
  "long think loops over 30 steps",
  "commits that skipped the test runner",
  "agents thrashing the same file repeatedly",
  "uncomfortable workarounds the agent applied",
  "RAG eval rows that drifted from the golden set",
  "tool calls that exceeded a 30s wall-clock",
];

const SPOTLIGHT_RECENT = [
  { q: "traces that hit the context window limit", when: "11 min ago", hits: 14 },
  { q: "agents thrashing the same file repeatedly", when: "2h ago",   hits: 31 },
  { q: "commits that skipped the test runner",     when: "yesterday", hits: 8 },
];

// ─────────────  Series generator (for stream charts)  ─────────────
// Produces a stacked-area dataset: { times: [...], series: [{ id, name, color, values: [...] }] }
function buildStreamSeries(items, seedBase = 4242, nBuckets = 60) {
  const out = items.map((it, i) => {
    let seed = (seedBase + it.id.charCodeAt(0) * 137 + i * 31) | 0;
    seed = Math.abs(seed);
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
    // Smooth wandering line with phase + amplitude based on the item's count
    const base = Math.max(2, (it.count || it.violations || 50) / 25);
    const amp  = base * (0.35 + rnd() * 0.5);
    const phase = rnd() * Math.PI * 2;
    const freq = 0.06 + rnd() * 0.05;
    const values = [];
    for (let t = 0; t < nBuckets; t++) {
      const wobble = Math.sin(phase + t * freq) * amp;
      const drift = Math.sin(phase * 0.5 + t * freq * 0.3) * amp * 0.4;
      values.push(Math.max(0, base + wobble + drift));
    }
    return { id: it.id, name: it.name, color: it.color, values };
  });
  const times = Array.from({ length: nBuckets }, (_, i) => i);
  return { times, series: out };
}

// Pair each intent / SOP with two synthetic matched trace conversations.
// We reuse real trace ids from RECENT_TRACES where possible for cross-linking.
function buildMatched(items, sourceTraces, seedBase = 17) {
  const out = {};
  items.forEach((it, idx) => {
    const rows = [];
    let seed = (seedBase + it.id.charCodeAt(0) * 19 + idx * 7) | 0;
    seed = Math.abs(seed);
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
    const n = 4 + Math.floor(rnd() * 3);
    for (let i = 0; i < n; i++) {
      const t = sourceTraces[Math.floor(rnd() * sourceTraces.length)];
      const fragments = MATCHED_FRAGMENTS[it.id] || GENERIC_MATCH_FRAGMENTS;
      const f = fragments[Math.floor(rnd() * fragments.length)];
      rows.push({
        traceId: t.id,
        title: t.title,
        repo: t.repo,
        agent: t.agent,
        when: relTime(t.timestamp),
        userMsg: f.user,
        agentMsg: f.agent,
      });
    }
    out[it.id] = rows;
  });
  return out;
}

const GENERIC_MATCH_FRAGMENTS = [
  { user: "Hmm, this still isn't working — what am I missing?", agent: "Looking at the stack trace, the early-return on line 42 swallows the error. Want me to rewire it?" },
  { user: "Can you just commit what we have?", agent: "Tests on the changed paths haven't run yet. I'll fire them off first, then commit." },
];

const MATCHED_FRAGMENTS = {
  "in-debug": [
    { user: "I keep getting a 500 from /traces — can you trace it back?", agent: "The handler swallows JSON parse failures and returns 500. Two upstream callers pass an empty body — that's the source. Patching." },
    { user: "Stack trace below. No idea where this is coming from.", agent: "It's the new minimap reducer — the initial state is undefined on first paint. I'll seed it from props." },
  ],
  "in-refactor": [
    { user: "Sidebar feels too sprawling — can we split it?", agent: "I'll extract DatasetsSection and RepositoriesSection into their own files. No behaviour change, just a 3-file split." },
    { user: "Names in trace-utils are inconsistent. Tidy up?", agent: "Renaming classifyStep → classifyTraceStep across 14 call-sites. Verifying with a tsc pass." },
  ],
  "in-test": [
    { user: "Add tests for the new fingerprint generator", agent: "Drafted 6 cases: empty input, single class, all-think, mixed, weighted, and the long-tail edge. Running the suite now." },
    { user: "Coverage on conversation.jsx is thin", agent: "Adding 4 tests covering filter toggle, tool expansion, jump-latest, and the empty state." },
  ],
  "in-thrash": [
    { user: "Just commit it.", agent: "I've re-read trail.jsx 11 times in this session. Stopping before another pass — let me lay out a plan first." },
    { user: "Why is this taking so long?", agent: "I bounced between minimap.tsx and trace-utils.jsx 14 times. Re-grouping the change set into one coherent diff." },
  ],
  "in-doc": [
    { user: "We just changed the trace schema — please mirror in docs.", agent: "Updating docs/schema.md and the README example block. The llms.txt will refresh from the doc-extract workflow on next push." },
  ],
  "in-migration": [
    { user: "Move every call-site of the old fetcher to the new client.", agent: "Found 47 call-sites. Migrating in topological order to keep CI green between commits." },
  ],
  "in-spike": [
    { user: "Quick spike: what if minimap was a stream graph?", agent: "Threw together a prototype on branch spike/stream-minimap. Looks promising at 28 steps, falls apart over 80. Discarding." },
  ],
  "in-hotfix": [
    { user: "Prod is throwing 500s on /landing — patch it now.", agent: "Hot-patched the null-guard on dataset_id. Pushing to release/2026-05-18. PR open for the proper fix." },
  ],
  "sop-secrets": [
    { user: "Ship it.", agent: "Blocked the commit — .env.local is staged and contains an OPENAI_API_KEY. Unstaging and committing the rest." },
    { user: "Why didn't you commit?", agent: "There's an AWS access key in fixtures/sample.json. I won't push that. Want me to scrub it and continue?" },
  ],
  "sop-tests": [
    { user: "Just commit.", agent: "Tests on trace-utils.jsx haven't been run yet — I changed classifyStep. Firing npm test first." },
  ],
  "sop-utils": [
    { user: "Add a function to debounce the search input", agent: "There's already a debounce helper in src/lib/timing.ts — I'll import that instead of writing a new one." },
  ],
  "sop-destructive": [
    { user: "Wipe the cache directory", agent: "About to run rm -rf .cache/ — confirm you want me to delete 47 files including the local trace index?" },
  ],
  "sop-scope": [
    { user: "Done with that, thanks.", agent: "Stopping here. I noticed the sidebar tree could use a refactor too, but that's a separate ticket." },
  ],
  "sop-docs": [
    { user: "Merge the API change", agent: "Holding the merge — the docs for /traces still describe the old shape. Drafting the doc edit alongside." },
  ],
  "sop-errors": [
    { user: "Just wrap it in a try-except.", agent: "I'd rather not catch-all. I'll narrow to the specific error and log the rest so we still see anomalies in Pulse." },
  ],
  "sop-issue": [
    { user: "Commit message?", agent: "Adding (refs #318) to the commit — this trail came from that ticket." },
  ],
  "sop-forcepush": [
    { user: "Just force push.", agent: "I won't force-push main. Branching to fix/landing-flicker and opening a PR instead." },
  ],
  "sop-citations": [
    { user: "Why this pattern?", agent: "Picking the reducer split from RFC 0014 — adding a comment that points there so future readers don't have to re-derive it." },
  ],
};

window.INTENTS = INTENTS;
window.SOPS = SOPS;
window.ALERTS = ALERTS;
window.HEAL_JOBS = HEAL_JOBS;
window.HEAL_CONFIG = HEAL_CONFIG;
window.SPOTLIGHT_SUGGESTIONS = SPOTLIGHT_SUGGESTIONS;
window.SPOTLIGHT_RECENT = SPOTLIGHT_RECENT;
window.buildStreamSeries = buildStreamSeries;
window.buildMatched = buildMatched;
window.INTENT_COLORS = INTENT_COLORS;
