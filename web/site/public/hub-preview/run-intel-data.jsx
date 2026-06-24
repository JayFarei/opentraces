// Run-intelligence layer — frozen, read-only JSON envelopes the Hub fetches & renders.
// Three deterministic overlays on top of an existing trace:
//   1. opentraces.context_waste.v1  — annotate the timeline with wasteful patterns
//   2. run-health  { status, trace_id, signals, counts }  — narrative beats of a run
//   3. opentraces.trace_compare.v1  — side-by-side A/B delta
// Every envelope carries fidelity: "record" | "otel". Nothing here writes back.

const TRACE_ID = "6eaf9ff4-b9aa-4c15-95ee-08e79b470b6f";

// ── 1. Context-waste ──────────────────────────────────────────
// step_index values are the real 1-based indices in the trace; node_id mirrors
// the canonical tu:<trace>:<kind>:<id> shape the CLI emits.
const CONTEXT_WASTE = {
  schema: "opentraces.context_waste.v1",
  trace_id: TRACE_ID,
  fidelity: "otel",
  thresholds: { large_output_chars: 8000, repeated_read_min: 3, repeated_read_window_min: 20 },
  findings: [
    { finding_id: "cw-01", pattern: "large_output", severity: "medium", confidence: 0.94,
      step_index: 9, node_id: `tu:${TRACE_ID}:observation:obs-0009`,
      reason: "A single tool result dominated the prompt window.",
      evidence: "GraphView.tsx read in full — 22,109 chars (~5.5k tokens) returned in one observation" },
    { finding_id: "cw-02", pattern: "large_output", severity: "medium", confidence: 0.91,
      step_index: 6, node_id: `tu:${TRACE_ID}:observation:obs-0006`,
      reason: "Whole-file read where a ranged read would have sufficed.",
      evidence: "App.tsx read in full — 18,420 chars; only ~40 lines were referenced after" },
    { finding_id: "cw-03", pattern: "large_output", severity: "low", confidence: 0.72,
      step_index: 78, node_id: `tu:${TRACE_ID}:observation:obs-0078`,
      reason: "Large source file pulled into context.",
      evidence: "graph_api.py read in full — 14,201 chars" },
    { finding_id: "cw-04", pattern: "repeated_file_read", severity: "medium", confidence: 0.96,
      step_index: 43, node_id: `tu:${TRACE_ID}:tool:tool-0043`,
      reason: "Same file re-read with no edits in between — context already held it.",
      evidence: "ReviewView.tsx read 3× across steps 7, 29, 43 (≈18 min)" },
    { finding_id: "cw-05", pattern: "repeated_file_read", severity: "medium", confidence: 0.93,
      step_index: 109, node_id: `tu:${TRACE_ID}:tool:tool-0109`,
      reason: "Repeated full read of a file already in context.",
      evidence: "server.py read 4× in 9 min (steps 93, 101, 107, 109)" },
    { finding_id: "cw-06", pattern: "repeated_file_read", severity: "medium", confidence: 0.88,
      step_index: 136, node_id: `tu:${TRACE_ID}:tool:tool-0136`,
      reason: "Verification screenshot re-read across browser-check cycles.",
      evidence: "verify screenshot re-read 8× during agent-browser checks" },
    { finding_id: "cw-07", pattern: "repeated_file_read", severity: "low", confidence: 0.74,
      step_index: 80, node_id: `tu:${TRACE_ID}:tool:tool-0080`,
      reason: "Tightly-spaced re-reads of the same module.",
      evidence: "graph_api.py read 3× in 2 min (steps 77, 78, 80)" },
  ],
  summary: { context_waste_count: 7, large_output_count: 3, repeated_file_read_count: 4, repeated_search_count: 0 },
};

// ── 2. Run health ─────────────────────────────────────────────
const RUN_HEALTH = {
  status: "ok",
  trace_id: TRACE_ID,
  fidelity: "otel",
  signals: [
    { kind: "resteer", severity: "info", confidence: 0.9, step_index: 45,
      node_id: `tu:${TRACE_ID}:user:msg-0045`,
      reason: "User redirected the agent toward live browser testing.",
      matched_text: "nothing has changed to otd web? have you use /agent-browser to test it?" },
    { kind: "failure", severity: "high", confidence: 0.86, step_index: 64,
      node_id: `tu:${TRACE_ID}:tool:tool-0064`,
      reason: "Browser eval matched no rows — the selector was stale.",
      matched_text: "agent-browser eval … rows.length === 0 (no clickable rows found)" },
    { kind: "recovery", severity: "medium", confidence: 0.82, step_index: 68,
      node_id: `tu:${TRACE_ID}:tool:tool-0068`,
      reason: "Recovered by resizing the viewport and re-dispatching hover.",
      matched_text: "set viewport 600 1000 2 … re-dispatch hover — controls now visible" },
    { kind: "loop", severity: "medium", confidence: 0.78, step_index: 73,
      node_id: `tu:${TRACE_ID}:tool:tool-0073`,
      reason: "Repeated screenshot/verify cycle on the same view without state change.",
      matched_text: "screenshot · wait 500 · re-check (×6 on the same panel)" },
    { kind: "resteer", severity: "info", confidence: 0.85, step_index: 87,
      node_id: `tu:${TRACE_ID}:user:msg-0087`,
      reason: "User questioned whether the pushed traces were really reviewed.",
      matched_text: "I pushed two traces and I'm unsure whether that actually worked…" },
    { kind: "failure", severity: "medium", confidence: 0.83, step_index: 130,
      node_id: `tu:${TRACE_ID}:tool:tool-0130`,
      reason: "npm run build failed — type error in the push route.",
      matched_text: "npm run build … error TS2345: Argument of type 'PushBody' is not assignable" },
    { kind: "recovery", severity: "medium", confidence: 0.84, step_index: 135,
      node_id: `tu:${TRACE_ID}:tool:tool-0135`,
      reason: "Build passed after correcting the push route types; UI verified.",
      matched_text: "agent-browser eval … push button found and enabled" },
  ],
  counts: { resteer: 2, recovery: 2, loop: 1, failure: 2 },
};

// ── 3. Trace compare (deterministic synthesis per trace id) ────
function _seedFrom(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return Math.abs(h) || 1;
}
function _rng(seed) {
  let s = seed % 2147483647; if (s <= 0) s += 2147483646;
  return () => { s = (s * 16807) % 2147483647; return (s - 1) / 2147483646; };
}

// Derive a stable per-trace "quality" record. Real values would come from the
// CLI; here they are seeded off the trace id so a given pair always compares
// the same way.
function buildTraceQuality(tr) {
  const r = _rng(_seedFrom(tr.id + "·q"));
  const tokens = tr.tokens || 60000;
  return {
    fidelity: r() > 0.5 ? "otel" : "record",
    metrics: {
      total_input_tokens: Math.round(tokens * (0.78 + r() * 0.06)),
      total_output_tokens: Math.round(tokens * (0.16 + r() * 0.06)),
      estimated_cost_usd: +(tr.cost ?? r() * 12).toFixed(2),
      total_duration_s: tr.duration_s || Math.round(400 + r() * 3600),
      cache_hit_rate: +(0.68 + r() * 0.3).toFixed(2),
    },
    quality: {
      personas: {
        conformance: +(0.58 + r() * 0.38).toFixed(2),
        training:    +(0.54 + r() * 0.42).toFixed(2),
        rl:          +(0.5  + r() * 0.46).toFixed(2),
        analytics:   +(0.6  + r() * 0.36).toFixed(2),
        domain:      +(0.5  + r() * 0.42).toFixed(2),
      },
      overall_utility: +(0.55 + r() * 0.4).toFixed(2),
      available: { conformance: true, training: true, rl: r() > 0.28, analytics: true, domain: r() > 0.45 },
    },
    bursts: {
      available: r() > 0.35,
      burst_count: 1 + Math.floor(r() * 6),
      files: 2 + Math.floor(r() * 22),
      patches: 1 + Math.floor(r() * 14),
    },
    signals: {
      error_signal_count: Math.floor(r() * 7),
      test_run_count: Math.floor(r() * 9),
      tool_call_count: tr.steps || Math.round(40 + r() * 180),
    },
    security: {
      flags_reviewed: Math.floor(r() * 11),
      redactions_applied: Math.floor(r() * 42),
    },
  };
}

function _leaf(a, b, opts) {
  opts = opts || {};
  const av = opts.aAvail !== false, bv = opts.bAvail !== false;
  const dp = opts.dp == null ? 2 : opts.dp;
  const aa = av ? a : null, bb = bv ? b : null;
  const delta = (av && bv) ? +(b - a).toFixed(dp) : null;
  return { a: aa, b: bb, delta };
}

function buildCompare(a, b) {
  const qa = buildTraceQuality(a), qb = buildTraceQuality(b);
  const personas = ["conformance", "training", "rl", "analytics", "domain"];
  const pOut = {};
  personas.forEach(p => {
    const aAvail = qa.quality.available[p], bAvail = qb.quality.available[p];
    pOut[p] = { ..._leaf(qa.quality.personas[p], qb.quality.personas[p], { aAvail, bAvail }), available: aAvail && bAvail };
  });
  const bAvA = qa.bursts.available, bAvB = qb.bursts.available;
  return {
    schema: "opentraces.trace_compare.v1",
    trace_a: a.id, trace_b: b.id,
    fidelity: qb.fidelity, fidelity_a: qa.fidelity, fidelity_b: qb.fidelity,
    quality_included: true,
    meta_a: { id: a.id, title: a.title, repo: a.repo, agent: a.agent, when: a.timestamp },
    meta_b: { id: b.id, title: b.title, repo: b.repo, agent: b.agent, when: b.timestamp },
    delta: {
      metrics: {
        total_input_tokens:  _leaf(qa.metrics.total_input_tokens,  qb.metrics.total_input_tokens,  { dp: 0 }),
        total_output_tokens: _leaf(qa.metrics.total_output_tokens, qb.metrics.total_output_tokens, { dp: 0 }),
        estimated_cost_usd:  _leaf(qa.metrics.estimated_cost_usd,  qb.metrics.estimated_cost_usd,  { dp: 2 }),
        total_duration_s:    _leaf(qa.metrics.total_duration_s,    qb.metrics.total_duration_s,    { dp: 0 }),
        cache_hit_rate:      _leaf(qa.metrics.cache_hit_rate,      qb.metrics.cache_hit_rate,      { dp: 2 }),
      },
      quality: { personas: pOut, overall_utility: _leaf(qa.quality.overall_utility, qb.quality.overall_utility, { dp: 2 }) },
      bursts: {
        available_a: bAvA, available_b: bAvB,
        burst_count: _leaf(qa.bursts.burst_count, qb.bursts.burst_count, { dp: 0, aAvail: bAvA, bAvail: bAvB }),
        files:       _leaf(qa.bursts.files,       qb.bursts.files,       { dp: 0, aAvail: bAvA, bAvail: bAvB }),
        patches:     _leaf(qa.bursts.patches,     qb.bursts.patches,     { dp: 0, aAvail: bAvA, bAvail: bAvB }),
      },
      signals: {
        error_signal_count: _leaf(qa.signals.error_signal_count, qb.signals.error_signal_count, { dp: 0 }),
        test_run_count:     _leaf(qa.signals.test_run_count,     qb.signals.test_run_count,     { dp: 0 }),
        tool_call_count:    _leaf(qa.signals.tool_call_count,    qb.signals.tool_call_count,    { dp: 0 }),
      },
      security: {
        flags_reviewed:     _leaf(qa.security.flags_reviewed,     qb.security.flags_reviewed,     { dp: 0 }),
        redactions_applied: _leaf(qa.security.redactions_applied, qb.security.redactions_applied, { dp: 0 }),
      },
    },
  };
}

// Per-trace run-health counts for the Traces landing (deterministic from id).
function buildHealthCounts(tr) {
  const r = _rng(_seedFrom(tr.id + "·h"));
  const failed = tr.status === "failed";
  const failure = failed ? 1 + Math.floor(r() * 2) : (r() > 0.7 ? 1 : 0);
  const loop = r() > 0.62 ? 1 + Math.floor(r() * 2) : 0;
  const resteer = r() > 0.4 ? 1 + Math.floor(r() * 3) : 0;
  const recovery = Math.min(failure, r() > 0.35 ? 1 + Math.floor(r() * 2) : 0);
  return { resteer, recovery, loop, failure };
}

// Section descriptor consumed by the compare renderer.
// lowerBetter: true → lower b is an improvement (green); false → higher is better; null → neutral.
const COMPARE_SECTIONS = [
  { title: "Metrics", group: "metrics", rows: [
    { key: "total_input_tokens",  label: "Input tokens",   fmt: "tok", lowerBetter: true },
    { key: "total_output_tokens", label: "Output tokens",  fmt: "tok", lowerBetter: true },
    { key: "estimated_cost_usd",  label: "Est. cost",      fmt: "usd", lowerBetter: true },
    { key: "total_duration_s",    label: "Duration",       fmt: "dur", lowerBetter: true },
    { key: "cache_hit_rate",      label: "Cache hit rate", fmt: "pct", lowerBetter: false },
  ]},
  { title: "Quality", group: "quality", rows: [
    { key: "conformance", persona: true, label: "Conformance",      fmt: "score", lowerBetter: false },
    { key: "training",    persona: true, label: "Training value",   fmt: "score", lowerBetter: false },
    { key: "rl",          persona: true, label: "RL signal",        fmt: "score", lowerBetter: false },
    { key: "analytics",   persona: true, label: "Analytics",        fmt: "score", lowerBetter: false },
    { key: "domain",      persona: true, label: "Domain expert",    fmt: "score", lowerBetter: false },
    { key: "overall_utility", overall: true, label: "Overall utility", fmt: "score", lowerBetter: false, strong: true },
  ]},
  { title: "Bursts", group: "bursts", rows: [
    { key: "burst_count", label: "Burst count",   fmt: "int", lowerBetter: null },
    { key: "files",       label: "Files touched", fmt: "int", lowerBetter: null },
    { key: "patches",     label: "Patches",       fmt: "int", lowerBetter: null },
  ]},
  { title: "Signals", group: "signals", rows: [
    { key: "error_signal_count", label: "Error signals", fmt: "int", lowerBetter: true },
    { key: "test_run_count",     label: "Test runs",     fmt: "int", lowerBetter: null },
    { key: "tool_call_count",    label: "Tool calls",    fmt: "int", lowerBetter: null },
  ]},
  { title: "Security", group: "security", rows: [
    { key: "flags_reviewed",     label: "Flags reviewed", fmt: "int", lowerBetter: false },
    { key: "redactions_applied", label: "Redactions",     fmt: "int", lowerBetter: null },
  ]},
];

// Visual config shared by the run-intel components.
const HEALTH_KINDS = {
  failure:  { label: "failure",  color: "var(--c-fail)",    glyph: "✕" },
  loop:     { label: "loop",     color: "var(--c-loop)",    glyph: "↻" },
  resteer:  { label: "resteer",  color: "var(--c-resteer)", glyph: "⤺" },
  recovery: { label: "recovery", color: "var(--c-recover)", glyph: "✓" },
};
const HEALTH_ORDER = ["failure", "loop", "resteer", "recovery"];

const WASTE_PATTERN_LABEL = {
  large_output: "large output",
  repeated_file_read: "repeated read",
  repeated_search: "repeated search",
};

window.TRACE_ID = TRACE_ID;
window.CONTEXT_WASTE = CONTEXT_WASTE;
window.RUN_HEALTH = RUN_HEALTH;
window.buildTraceQuality = buildTraceQuality;
window.buildCompare = buildCompare;
window.buildHealthCounts = buildHealthCounts;
window.COMPARE_SECTIONS = COMPARE_SECTIONS;
window.HEALTH_KINDS = HEALTH_KINDS;
window.HEALTH_ORDER = HEALTH_ORDER;
window.WASTE_PATTERN_LABEL = WASTE_PATTERN_LABEL;
