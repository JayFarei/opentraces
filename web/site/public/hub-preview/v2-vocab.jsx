// v2-vocab.jsx — cross-cutting primitives for the v40-aligned Hub.
// Three facts thread through every surface:
//   1. the address grammar <trace>[:step|:A-B] as the universal join key
//   2. the source facet observed | manufactured
//   3. every label carries verifier@version + method + trust standing
// Plus the two-layer vocabulary (plain label first, technical underneath)
// and honesty stamps that never relabel upward.

// ── Two-layer vocabulary ────────────────────────────────────────
// Plain label first, technical name underneath. Presentation only.
const V2_VOCAB = {
  trace:    { plain: "Trace",            tech: "trace" },
  bucket:   { plain: "Private home",     tech: "bucket" },
  dataset:  { plain: "Dataset",          tech: "dataset" },
  capsule:  { plain: "Replay",           tech: "capsule" },
  bench:    { plain: "Bench",            tech: "bench.v0" },
  check:    { plain: "Check",            tech: "guarantee + scenario + verifier + label" },
  evidence: { plain: "Evidence",         tech: "run record → page + feed" },
  world:    { plain: "World signal",     tech: "world_fact" },
  label:    { plain: "Label",            tech: "label" },
  box:      { plain: "Box",              tech: "box" },
};

// Hero header with the two-layer treatment: plain title, technical name
// underneath in mono. Used by every v2 surface.
function V2Hero({ plain, tech, subtitle, scope, actions }) {
  return (
    <header className="page-hero v2-hero">
      <div className="ph-text">
        <div className="v2-hero-titles">
          <h1 className="ph-title">{plain}</h1>
          {tech && <span className="v2-tech mono">{tech}</span>}
        </div>
        {subtitle && <p className="ph-sub">{subtitle}</p>}
        {scope && <div className="ph-scope mono">{scope}</div>}
      </div>
      {actions && <div className="ph-actions">{actions}</div>}
    </header>
  );
}

// ── Address grammar ─────────────────────────────────────────────
// <trace-id>[:step|:last|:A-B] — the universal join key. Every entity
// links back through it.
function parseAddress(addr) {
  if (!addr) return null;
  const i = addr.indexOf(":");
  if (i < 0) return { trace: addr, range: null };
  return { trace: addr.slice(0, i), range: addr.slice(i + 1) };
}

function AddressLink({ addr, onOpen, dim }) {
  const p = parseAddress(addr);
  if (!p) return <span className="addr-none mono">—</span>;
  return (
    <span
      role="button"
      tabIndex={0}
      className={"addr-link mono" + (dim ? " dim" : "")}
      title={"Open " + addr + " in the trace viewer"}
      onClick={(e) => { e.stopPropagation(); onOpen && onOpen(p.trace, p.range); }}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); onOpen && onOpen(p.trace, p.range); } }}
    >
      <span className="addr-trace">{p.trace}</span>
      {p.range && <span className="addr-range">:{p.range}</span>}
    </span>
  );
}

// ── Source facet ────────────────────────────────────────────────
// observed — captured from real agent work; manufactured — produced by an
// arena run returning to the bucket as manufactured record.
function FacetChip({ facet, small }) {
  return (
    <span className={"facet-chip f-" + facet + (small ? " sm" : "")} title={
      facet === "observed"
        ? "observed — captured from real agent work"
        : "manufactured — produced by a run on a disposable box; returned to the bucket as manufactured record"
    }>
      <span className="facet-dot" />{facet}
    </span>
  );
}

// ── Verdicts ────────────────────────────────────────────────────
// pass | fail | skip — a nonzero product rc is `failed` (a product
// observation); blocked_missing_surface names the missing thing.
const V2_VERDICT = {
  pass: { label: "pass", tone: "git" },
  fail: { label: "fail", tone: "error" },
  skip: { label: "skip", tone: "plan" },
  blocked_missing_surface: { label: "blocked — missing surface", tone: "think" },
};
function VerdictPill({ v, big, detail }) {
  const d = V2_VERDICT[v] || V2_VERDICT.fail;
  return (
    <span className={"verdict-pill vp-" + d.tone + (big ? " big" : "")} title={detail || undefined}>
      <span className="vp-dot" />{d.label}
    </span>
  );
}

// ── Trust ladder ────────────────────────────────────────────────
// agent proposes → factory scores → human approves.
// Deterministic bench verdicts cap at the middle rung; LLM-judge labels cap
// at provisional; standings are never self-promoted.
const V2_RUNGS = [
  { key: "provisional", label: "provisional", hint: "agent proposes" },
  { key: "scored",      label: "scored",      hint: "factory scores" },
  { key: "approved",    label: "approved",    hint: "human approves" },
];
const V2_METHOD_CAP = { deterministic: "scored", agent: "provisional", human: "approved", provider: "provisional" };

function TrustChip({ standing, method, small }) {
  const idx = V2_RUNGS.findIndex(r => r.key === standing);
  const cap = method ? V2_METHOD_CAP[method] : null;
  return (
    <span
      className={"trust-chip t-" + standing + (small ? " sm" : "")}
      title={
        V2_RUNGS.map((r, i) => (i <= idx ? "●" : "○") + " " + r.label + " — " + r.hint).join("\n") +
        (cap ? "\ncap for method=" + method + ": " + cap : "")
      }
    >
      <span className="trust-rungs">
        {V2_RUNGS.map((r, i) => <span key={r.key} className={"rung" + (i <= idx ? " on" : "")} />)}
      </span>
      {standing}
    </span>
  );
}

// verifier@version + method — always shown together with standing wherever
// a score appears.
function VerifierTag({ name, ver, method, current, small }) {
  return (
    <span className={"verifier-tag mono" + (small ? " sm" : "")} title={"method: " + method + (current === false ? " · a newer version exists — trust ages with the versions it stood on" : "")}>
      {name}<span className="vt-ver">@{ver}</span>
      {method && <span className={"vt-method m-" + method}>{method}</span>}
      {current === false && <span className="vt-stale">stale</span>}
    </span>
  );
}

// The full ladder, rendered as a rail explainer.
function TrustLadder({ activeStanding }) {
  return (
    <div className="trust-ladder">
      <div className="tl-head">Trust ladder</div>
      {[...V2_RUNGS].reverse().map(r => (
        <div key={r.key} className={"tl-rung" + (activeStanding === r.key ? " here" : "")}>
          <span className="tl-dot" />
          <div className="tl-body">
            <span className="tl-label">{r.label}</span>
            <span className="tl-hint">{r.hint}</span>
          </div>
        </div>
      ))}
      <div className="tl-note">
        Deterministic bench verdicts cap at <b>scored</b>. LLM-judge labels cap at{" "}
        <b>provisional</b>. Standings are never self-promoted.
      </div>
    </div>
  );
}

// ── Honesty stamps ──────────────────────────────────────────────
// sandbox_tier, capsule badge facets, freshness — derived, never
// relabelable upward in the UI. The lock glyph is the promise.
function Stamp({ label, value, tone = "neutral", title }) {
  return (
    <span className={"stamp st-" + tone} title={(title ? title + "\n" : "") + "Derived stamp — never relabelable upward."}>
      <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><rect x="4.5" y="11" width="15" height="9" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/></svg>
      <span className="st-k">{label}</span>
      <span className="st-v mono">{value}</span>
    </span>
  );
}

const V2_TIER_TONE = { none: "warn", jail: "neutral", container: "ok", microvm: "ok" };
function SandboxTier({ tier }) {
  return <Stamp label="sandbox" value={tier} tone={V2_TIER_TONE[tier] || "neutral"} title="Derived from the box's actual isolation." />;
}

// Freshness — trust ages with the versions it stood on.
function FreshnessTag({ band, label }) {
  return <span className={"fresh-tag fr-" + band} title="Freshness is derived and always visible — trust ages with the versions it stood on.">{label}</span>;
}

// ── Atlas row states ────────────────────────────────────────────
// Every hole is a named row state, never an absence.
const V2_ROW_STATES = {
  ok:                     { label: "fresh",                  tone: "ok",   hint: "Bound, run recently, verifier current, red proof on file." },
  unbound:                { label: "unbound",                tone: "hole", hint: "No scenario + verifier bound to this guarantee yet." },
  "stale-run":            { label: "stale-run",              tone: "warn", hint: "The last run is older than the freshness window." },
  "stale-verifier":       { label: "stale-verifier",         tone: "warn", hint: "The verifier has a newer version than the one the verdict stood on." },
  "unrepresentative-world": { label: "unrepresentative-world", tone: "warn", hint: "The pinned app_state recipe has drifted from the world it claims to represent." },
  "no-red-proof":         { label: "no-red-proof",           tone: "red",  hint: "A green exists but the verifier was never shown failing — the green is not yet believed." },
  "surface-drift":        { label: "surface-drift",          tone: "red",  hint: "Touched product surfaces are not mapped to any guarantee row." },
};
function RowStateChip({ state }) {
  const d = V2_ROW_STATES[state] || V2_ROW_STATES.ok;
  return <span className={"rowstate rs-" + d.tone} title={d.hint}>{d.label}</span>;
}

// ── Anti-reward-hacking chips ───────────────────────────────────
const V2_ARH = [
  { key: "blackBox",            label: "black-box",            hint: "The verifier only sees public surfaces — never the implementation." },
  { key: "verifierIndependent", label: "verifier-independent", hint: "The verifier is authored and versioned apart from the code under test." },
  { key: "ledgerAsserted",      label: "ledger-asserted",      hint: "The verdict asserts against the append-only ledger, not screen output." },
  { key: "redProofed",          label: "red-proofed",          hint: "The verifier was shown failing before its green was believed." },
  { key: "rewatchable",         label: "rewatchable",          hint: "A terminal cast of the run exists and can be rewatched. Film is never an input to a verdict." },
];
function ArhChips({ chips, compact }) {
  return (
    <span className={"arh-chips" + (compact ? " compact" : "")}>
      {V2_ARH.map(c => (
        <span key={c.key} className={"arh" + (chips && chips[c.key] ? " on" : "")} title={c.label + " — " + c.hint + (chips && chips[c.key] ? "" : "\n(not held here)")}>
          {compact ? c.label[0].toUpperCase() : c.label}
        </span>
      ))}
    </span>
  );
}

// ── Clearance partition ─────────────────────────────────────────
// One shared predicate, three-way: cleared | not_cleared | unknown.
// unknown is conservatively withheld, never coerced.
function ClearancePartition({ pushed, withheld, unknown }) {
  return (
    <span className="clearance mono" title="One shared predicate at all three egress doors — bucket sync push, dataset publish, capsule share. unknown is conservatively withheld, never coerced.">
      <span className="cl-p">pushed {pushed}</span>
      <span className="cl-w">withheld {withheld}{unknown ? ` (${unknown} unknown)` : ""}</span>
    </span>
  );
}

Object.assign(window, {
  V2_VOCAB, V2Hero, parseAddress, AddressLink, FacetChip, VerdictPill, V2_VERDICT,
  TrustChip, VerifierTag, TrustLadder, V2_RUNGS, Stamp, SandboxTier, FreshnessTag,
  V2_ROW_STATES, RowStateChip, ArhChips, ClearancePartition,
});
