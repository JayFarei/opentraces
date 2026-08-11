// Intents × Slices — unbundle sessions into content-addressed slices,
// review them at group level, rebundle into task-specific datasets.
//
// The reviewing loop this enables:
//   1. An intent matches SLICES, not whole sessions — the evidence unit
//      is the 8–20 steps that express the pattern, content-addressed.
//   2. Unbundle: group matched slices by task type / project / session to
//      see the shape of the pattern at a glance.
//   3. Rebundle: select slices (or whole groups) → a bundle → push as a
//      task-specific dataset that spans projects.
//
// Provenance uses the same visual taxonomy as pull requests: a ghost
// track for the full session, a lit window for the slice, and the
// content address (sl:xxxxxxx) that seals it.

// ── deterministic synth ─────────────────────────────────────────
function sliceHash(seedStr) {
  let h = 0x811c9dc5;
  for (let i = 0; i < seedStr.length; i++) { h ^= seedStr.charCodeAt(i); h = (h * 0x01000193) | 0; }
  return ("0000000" + (h >>> 0).toString(16)).slice(-7);
}
function seededRng(seedStr) {
  let s = Math.abs(String(seedStr).split("").reduce((a, c) => (a * 33 + c.charCodeAt(0)) | 0, 7)) || 1;
  return () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
}

// Which task types an intent's slices typically land on.
const INTENT_TYPE_POOL = {
  "in-debug":    ["fix", "verify", "explore", "fix"],
  "in-refactor": ["refactor", "polish", "verify", "refactor"],
  "in-scaffold": ["build", "scope", "ship", "build"],
  "in-docs":     ["explore", "polish", "scope", "polish"],
  "in-test":     ["verify", "fix", "verify", "build"],
};
const SLICE_LABEL_POOL = {
  scope:    ["Scope the failing surface", "Pin down the ask", "Frame acceptance criteria"],
  explore:  ["Walk the call graph", "Trace config through loaders", "Map handlers to routes"],
  build:    ["Stand up the endpoint", "Add retry wrapper", "Wire the new module"],
  fix:      ["Repair off-by-one in pager", "Fix stale cache invalidation", "Unbreak flaky auth check"],
  refactor: ["Split the god-module", "Rename to match domain terms", "Hoist shared validation"],
  verify:   ["Run the failing suite green", "Screenshot the regression", "Assert on the boundary case"],
  polish:   ["Tighten empty-state copy", "Align spacing to tokens", "De-noise log output"],
  ship:     ["Land and push the fix", "Commit with test evidence", "Ship behind the flag"],
};
const SLICE_CLASS_KEYS = ["user", "plan", "think", "read", "exec", "write"];
const TYPE_CLASS_BIAS = {
  scope: ["user", "plan", "think"], explore: ["read", "read", "think"],
  build: ["write", "write", "exec"], fix: ["read", "write", "exec"],
  refactor: ["write", "read", "write"], verify: ["exec", "exec", "read"],
  polish: ["write", "read", "write"], ship: ["exec", "write", "exec"],
};

const SLICE_HARNESSES = [
  { id: "claude-code", v: "2.1.111", model: "claude-opus-4-7" },
  { id: "codex", v: "0.44", model: "gpt-6-codex" },
  { id: "aider", v: "0.86", model: "qwen3-coder" },
];

// One intent → its matched slices (built on top of the matched traces).
function buildMatchedSlices(intent, matchedRows) {
  const pool = INTENT_TYPE_POOL[intent.id] || ["build", "explore", "fix", "verify"];
  const out = [];
  (matchedRows || []).forEach((row, r) => {
    const rng = seededRng(intent.id + ":" + (row.traceId || r));
    const nSlices = 1 + Math.round(rng());
    const total = 60 + Math.floor(rng() * 140);
    const harness = SLICE_HARNESSES[Math.floor(rng() * SLICE_HARNESSES.length)];
    for (let k = 0; k < nSlices; k++) {
      const type = pool[Math.floor(rng() * pool.length)];
      const len = 8 + Math.floor(rng() * 16);
      const start = Math.floor(rng() * Math.max(1, total - len));
      const bias = TYPE_CLASS_BIAS[type] || SLICE_CLASS_KEYS;
      const classes = Array.from({ length: Math.min(len, 22) }, (_, i) =>
        i === 0 && start === 0 ? "user" : (rng() < 0.62 ? bias[Math.floor(rng() * bias.length)] : SLICE_CLASS_KEYS[Math.floor(rng() * 6)])
      );
      const labels = SLICE_LABEL_POOL[type];
      out.push({
        id: "sl:" + sliceHash(intent.id + row.traceId + k),
        traceId: row.traceId, session: row.title, repo: row.repo,
        agent: row.agent, when: row.when,
        type, label: labels[Math.floor(rng() * labels.length)],
        start, end: start + len - 1, total, classes,
        slicer: "milestone",
        harness,
        conf: Math.round((0.62 + rng() * 0.36) * 100) / 100,
        tokens: 4000 + Math.floor(rng() * 34000),
        // deterministic mix that guarantees both outcomes appear per intent
        outcome: (r * 2 + k) % 3 === 1 ? "failed" : "reached",
      });
    }
  });
  return out;
}

// ── provenance strip (same visual taxonomy as the PR bars) ──────
function SliceProvenance({ slice }) {
  const tone = {
    user: "var(--c-user)", plan: "var(--c-plan)", think: "var(--c-think)",
    read: "var(--c-read)", exec: "var(--c-exec)", write: "var(--c-write)",
  };
  const before = slice.start, len = slice.end - slice.start + 1, after = slice.total - slice.end - 1;
  return (
    <div className="slp" title={`steps ${slice.start}–${slice.end} of ${slice.total} · ${slice.session}`}>
      <span className="slp-ghost" style={{ flexGrow: Math.max(0.5, before) }} />
      <span className="slp-window" style={{ flexGrow: len * 2.2 }}>
        {slice.classes.map((c, i) => <i key={i} style={{ background: tone[c] || "var(--c-plan)" }} />)}
      </span>
      <span className="slp-ghost" style={{ flexGrow: Math.max(0.5, after) }} />
      <span className="slp-span mono">{slice.start}–{slice.end}<em>/{slice.total}</em></span>
    </div>
  );
}

// ── one matched slice ───────────────────────────────────────────
function IntentSliceRow({ slice, selected, onToggle, onOpen }) {
  const t = SLICE_TYPES[slice.type] || { name: slice.type };
  return (
    <div className={"isr" + (selected ? " sel" : "")} data-type={slice.type}>
      <label className="isr-check" onClick={(e) => e.stopPropagation()}>
        <input type="checkbox" checked={selected} onChange={onToggle} aria-label={`Select ${slice.id}`} />
      </label>
      <button className="isr-body" onClick={onOpen} title={`Open ${slice.session} scoped to this slice`}>
        <div className="isr-top">
          <span className="isr-type"><SliceGlyph type={slice.type} size={10} /> {t.name}</span>
          <span className="isr-label">{slice.label}</span>
          <span className={"isr-outcome " + slice.outcome} title={slice.outcome === "reached" ? "outcome: goal reached" : "outcome: interrupted / failed"}>
            {slice.outcome === "reached" ? "✓ reached" : "✕ failed"}
          </span>
          <span className="isr-addr mono">{slice.id}</span>
        </div>
        <SliceProvenance slice={slice} />
        <div className="isr-meta">
          <RepoLabel repo={slice.repo} />
          <span className="dot-sep" />
          <span className="isr-session">{slice.session}</span>
          <span className="dot-sep" />
          <span className="isr-harness mono">{slice.harness.id} {slice.harness.v} · {slice.harness.model}</span>
          <span className="dot-sep" />
          <span className="isr-slicer"><span className="isr-slicer-dot" /> milestone · qwen3-4b</span>
          <span className="dot-sep" />
          <span className="isr-conf mono" title="grader confidence for this intent match">conf {slice.conf.toFixed(2)}</span>
          <span className="isr-when">{slice.when}</span>
        </div>
      </button>
    </div>
  );
}

// ── composition strip: the intent's shape in task types ─────────
function SliceComposition({ slices, typeFilter, onFilter }) {
  const counts = {};
  slices.forEach((s) => { counts[s.type] = (counts[s.type] || 0) + 1; });
  const order = Object.keys(SLICE_TYPES).filter((k) => counts[k]);
  if (!order.length) return null;
  return (
    <div className="slc-comp">
      <div className="slc-comp-bar">
        {order.map((k) => (
          <button
            key={k}
            className={"slc-comp-seg" + (typeFilter === k ? " on" : "") + (typeFilter && typeFilter !== k ? " dim" : "")}
            style={{ flexGrow: counts[k] }}
            title={`${SLICE_TYPES[k].name} · ${counts[k]} slices — ${SLICE_TYPES[k].desc}`}
            onClick={() => onFilter(typeFilter === k ? null : k)}
          >
            <SliceGlyph type={k} size={10} />
            <span className="scs-n mono">{counts[k]}</span>
            <span className="scs-name">{SLICE_TYPES[k].name}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── heatmap: intents × process taxonomy — the vantage point ─────
function IntentHeatmap({ matched, sel, onSel }) {
  const types = Object.keys(SLICE_TYPES);
  const rows = INTENTS.map((it) => {
    const slices = buildMatchedSlices(it, matched[it.id] || []);
    const byType = {};
    let failed = 0;
    slices.forEach((s) => { byType[s.type] = (byType[s.type] || 0) + 1; if (s.outcome === "failed") failed++; });
    return { it, slices, byType, failed };
  });
  const emerging = (it, t) => seededRng("em:" + it.id + t)() > 0.82;
  const est = (row, t) => Math.round(row.it.count * ((row.byType[t] || 0) / Math.max(1, row.slices.length)));
  const hintFor = (row) => {
    const failShare = row.failed / Math.max(1, row.slices.length);
    if (failShare > 0.34) return { cls: "warn", lbl: "failing — draft eval" };
    if (types.some((t) => row.byType[t] && emerging(row.it, t))) return { cls: "new", lbl: "emerging — inspect" };
    return { cls: "ok", lbl: "stable — curate dataset" };
  };

  return (
    <section className="intent-heatmap-card" data-screen-label="intent-heatmap">
      <header className="ihm-head">
        <div>
          <div className="icc-kicker">Task families × process</div>
          <div className="icc-sub">Columns are the system's invariant process stages. Rows are task families — detected automatically, reviewable, and yours to add to. Cell heat: how much of a family's volume carries that stage.</div>
        </div>
        <div className="ihm-attr">
          <span className="ilc-grader-dot" />
          labels: grader <span className="mono">qwen3-8b</span> <span className="ilc-grader-rt">local</span> · slices: milestone · <span className="mono">qwen3-4b</span>
          <span className="ihm-legend"><i className="lg-low" /> low <i className="lg-hi" /> high <i className="lg-em" /> emerging</span>
        </div>
      </header>

      <div className="ihm-grid" style={{ "--cols": types.length }}>
        <div className="ihm-corner" />
        {types.map((t) => (
          <div key={t} className="ihm-col-h" title={SLICE_TYPES[t].desc}>
            <SliceGlyph type={t} size={11} />
            <span>{SLICE_TYPES[t].name}</span>
          </div>
        ))}
        <div className="ihm-col-h act">Next action</div>

        {rows.map((row) => {
          const hint = hintFor(row);
          const isRow = sel.id === row.it.id;
          return (
            <React.Fragment key={row.it.id}>
              <button className={"ihm-row-h" + (isRow ? " on" : "")} onClick={() => onSel({ id: row.it.id, type: null })}>
                <span className="ilc-dot" style={{ background: row.it.color }} />
                <span className="ihm-row-name">{row.it.name}</span>
                <span className={"ihm-row-src " + statusOf(row.it)}>{statusOf(row.it)}</span>
                <span className="ihm-row-n mono">{row.it.count}</span>
              </button>
              {types.map((t) => {
                const n = row.byType[t] || 0;
                const share = n / Math.max(1, row.slices.length);
                const isOn = isRow && sel.type === t;
                const em = n > 0 && emerging(row.it, t);
                return (
                  <button
                    key={t}
                    className={"ihm-cell" + (isOn ? " on" : "") + (em ? " em" : "") + (n === 0 ? " zero" : "")}
                    style={{ "--heat": Math.min(1, share * 2.2) }}
                    disabled={n === 0}
                    title={n === 0 ? undefined : `${row.it.name} × ${SLICE_TYPES[t].name} — ~${est(row, t)} slices${em ? " · emerging" : ""} · click to inspect`}
                    onClick={() => onSel({ id: row.it.id, type: t })}
                  >
                    {n > 0 && <span className="ihm-cell-n mono">{est(row, t)}</span>}
                  </button>
                );
              })}
              <button className={"ihm-hint " + hint.cls} onClick={() => onSel({ id: row.it.id, type: null })}>{hint.lbl}</button>
            </React.Fragment>
          );
        })}
      </div>

      <IntentOpportunities rows={rows} onSel={onSel} />
    </section>
  );
}

// ── opportunities: where inspection pays off — the system's suggestions ─
function IntentOpportunities({ rows, onSel }) {
  const opps = [];
  rows.forEach((row) => {
    const n = Math.max(1, row.slices.length);
    const medTok = Math.round(row.slices.reduce((a, s) => a + (s.tokens || 0), 0) / n / 1000);
    const failShare = row.failed / n;
    const projects = new Set(row.slices.map((s) => s.repo)).size;
    const verifyShare = (row.byType.verify || 0) / n;
    if (medTok >= 18 && row.it.count >= 150) opps.push({ id: row.it.id, kind: "skill", score: medTok * row.it.count, msg: <><b>{row.it.name}</b> burns ~{medTok}k tok/slice across {row.it.count} runs — distill a skill to shortcut it.</> });
    if (failShare > 0.34) opps.push({ id: row.it.id, kind: "eval", score: failShare * row.it.count * 40, msg: <><b>{row.it.name}</b> fails {Math.round(failShare * 100)}% of the time — its failed slices are ready-made eval cases.</> });
    if (verifyShare >= 0.3 && failShare <= 0.34) opps.push({ id: row.it.id, kind: "arena", score: verifyShare * row.it.count * 30, msg: <><b>{row.it.name}</b> verifies reproducibly — package the environment as an arena to train or benchmark against.</> });
    if (projects >= 4) opps.push({ id: row.it.id, kind: "dataset", score: projects * 90, msg: <><b>{row.it.name}</b> recurs across {projects} projects — bundle a cross-project dataset.</> });
  });
  const top = opps.sort((a, b) => b.score - a.score).slice(0, 3);
  if (!top.length) return null;
  const icon = { skill: "tool", eval: "check", arena: "grid", dataset: "datasets", capsule: "capsule" };
  return (
    <div className="ihm-opps">
      <span className="ihm-opps-k">Opportunities</span>
      {top.map((o, i) => (
        <button key={i} className={"ihm-opp " + o.kind} onClick={() => onSel({ id: o.id, type: null })}>
          <span className="ihm-opp-kind"><Icon name={icon[o.kind] || "tool"} size={10} /> {o.kind}</span>
          <span className="ihm-opp-msg">{o.msg}</span>
        </button>
      ))}
    </div>
  );
}

// cluster lifecycle: auto-detected → confirmed by a human → promoted
function statusOf(it) {
  const r = seededRng("st:" + it.id)();
  return r < 0.25 ? "promoted" : r < 0.6 ? "confirmed" : "auto";
}

// ── refine queue: teach the classifier ─────────────────────────
function RefineQueue({ slices, onOpen, bare }) {
  const [handled, setHandled] = React.useState({});
  const low = slices.filter((s) => s.conf < 0.72);
  const queue = low.filter((s) => !handled[s.id]).slice(0, 3);
  const nDone = Object.keys(handled).length;
  if (!low.length) return null;
  const act = (id, verdict) => setHandled((h) => ({ ...h, [id]: verdict }));
  const body = (
    <>
      {bare && <div className="rfq-sub">{low.length} low-confidence matches · your labels feed the next training pass{nDone > 0 ? ` · ${nDone} labeled this session` : ""}</div>}
      {queue.length === 0 ? (
        <div className="rfq-done"><Icon name="check" size={13} /> Queue clear — labels queued for the next regrade pass.</div>
      ) : (
        <div className="rfq-list">
          {queue.map((s) => (
            <div key={s.id} className="rfq-row">
              <button className="rfq-main" onClick={() => onOpen && onOpen(s.traceId)} title="Open the session scoped to this slice">
                <div className="rfq-top">
                  <span className="isr-type"><SliceGlyph type={s.type} size={10} /> {SLICE_TYPES[s.type].name}</span>
                  <span className="isr-label">{s.label}</span>
                  <span className="rfq-guess">grader says <span className="mono">@{s.intentName}</span> · conf {s.conf.toFixed(2)}</span>
                </div>
                <SliceProvenance slice={s} />
              </button>
              <div className="rfq-actions">
                <button className="rfq-btn ok" onClick={() => act(s.id, "confirm")} title="Confirm the grader's label"><Icon name="check" size={11} /> Confirm</button>
                <select className="rfq-reassign" defaultValue="" onChange={(e) => e.target.value && act(s.id, "reassign:" + e.target.value)} aria-label="Reassign to another cluster">
                  <option value="" disabled>Reassign…</option>
                  {INTENTS.filter((i) => i.name !== s.intentName).map((i) => <option key={i.id} value={i.id}>@{i.name}</option>)}
                </select>
                <button className="rfq-btn" onClick={() => act(s.id, "skip")}>Skip</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
  if (bare) return <div className="rfq-bare">{body}</div>;
  return (
    <section className="refine-card" data-screen-label="refine-queue">
      <header className="ihm-head">
        <div>
          <div className="icc-kicker">Refine the classifier</div>
          <div className="icc-sub">{low.length} low-confidence matches · your labels feed the next regrade{nDone > 0 ? ` · ${nDone} labeled this session` : ""}</div>
        </div>
        <span className="ihm-attr"><span className="ilc-grader-dot" /> grader <span className="mono">qwen3-8b</span> <span className="ilc-grader-rt">local</span></span>
      </header>
      {body}
    </section>
  );
}

// ── diagnostics: how this intent plays out (before curating it) ──
function IntentDiagnostics({ intent, slices }) {
  const rng = seededRng("diag:" + intent.id);
  const reached = (slices || []).filter((s) => s.outcome === "reached").length;
  const success = slices && slices.length ? Math.round((reached / slices.length) * 100) : 0;
  const matchesN = intent.id === "all" ? (slices || []).length : intent.count;
  const medTok = slices && slices.length ? Math.round(slices.reduce((a, s) => a + (s.tokens || 0), 0) / slices.length / 1000) : 0;
  const medLen = 9 + Math.floor(rng() * 10);
  const waste = 4 + Math.floor(rng() * 14);
  const projects = new Set((slices || []).map((s) => s.repo)).size || 1;
  // assets already derived from this intent — the loop's output, visible
  const derived = {
    "in-debug":    [{ k: "dataset", label: "debug-fix-verify · 214 rows" }, { k: "eval", label: "debug-investigation · 87% pass" }],
    "in-refactor": [{ k: "skill", label: "safe-rename-sweep · v2" }],
    "in-test":     [{ k: "eval", label: "coverage-gaps · 74% pass" }],
  }[intent.id] || [];
  return (
    <div className="int-diag">
      <div className="int-diag-stats">
        <span className="ids-item"><b className="mono">{matchesN}</b> {intent.id === "all" ? "slices" : "matches"}</span>
        <span className="ids-item"><b className="mono">{success}%</b> reached goal</span>
        <span className="ids-item">median slice <b className="mono">{medLen}</b> steps</span>
        <span className="ids-item"><b className="mono">~{medTok}k</b> tok/slice</span>
        <span className="ids-item"><b className="mono">{waste}%</b> wasted steps</span>
        <span className="ids-item"><b className="mono">{projects}</b> projects</span>
      </div>
      {derived.length > 0 && (
        <div className="int-derived">
          <span className="int-derived-k">Derived</span>
          {derived.map((d, i) => (
            <span key={i} className={"idv-chip " + d.k}>
              <Icon name={d.k === "dataset" ? "datasets" : d.k === "eval" ? "check" : "tool"} size={10} />
              <span className="idv-kind">{d.k}</span>
              <span className="mono">{d.label}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── derive tray: turn selected slices into an asset ──────────────
function BundleTray({ slices, selectedIds, onClear, intent }) {
  const sel = slices.filter((s) => selectedIds.has(s.id));
  const [done, setDone] = React.useState(null);
  React.useEffect(() => { setDone(null); }, [selectedIds.size]);
  if (!sel.length) return null;
  const repos = [...new Set(sel.map((s) => s.repo))];
  const types = [...new Set(sel.map((s) => s.type))];
  const slug = (intent.name || "intent").toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const shape = [...new Set(sel.map((s) => s.type))].map((t) => SLICE_TYPES[t] ? SLICE_TYPES[t].name : t).join(" → ");
  const derive = (kind) => {
    const one = sel.length === 1;
    if (kind === "dataset") setDone({ msg: <>Bundled <b>{sel.length} {one ? "slice" : "slices"}</b> from {repos.length} project{repos.length > 1 ? "s" : ""} into dataset <span className="mono">{slug}-{types.join("-")}</span> — rows stay content-addressed to their source slices.</> });
    else if (kind === "eval") setDone({ msg: <>Drafted eval suite <span className="mono">{slug}</span> — <b>{sel.length} {one ? "case" : "cases"}</b>, expected outcomes taken from each slice's Verify/Ship signals. Review it under Evals.</> });
    else if (kind === "arena") setDone({ msg: <>Drafted arena <span className="mono">{slug}-arena</span> from <b>{sel.length} {one ? "slice" : "slices"}</b> — repo pins + verify commands become the reproducible environment. Review under Arena.</> });
    else if (kind === "capsule") setDone({ msg: <>Sealed <b>{sel.length} {one ? "capsule" : "capsules"}</b> — each slice packaged with its repo pin and redaction pass, shareable by content address.</> });
    else setDone({ msg: <>Distilled a skill draft from <b>{sel.length} {one ? "slice" : "slices"}</b> — common trajectory <span className="mono">{shape}</span>. Review the playbook before publishing.</> });
  };
  return (
    <div className="bundle-tray" role="region" aria-label="Derive from selected slices">
      {done ? (
        <div className="bt-done">
          <Icon name="check" size={13} />
          <span>{done.msg}</span>
          <button className="bt-clear" onClick={onClear}>Done</button>
        </div>
      ) : (
        <>
          <div className="bt-l">
            <span className="bt-count mono">{sel.length}</span>
            <span className="bt-lbl">slices selected</span>
            <span className="bt-chips">
              {repos.slice(0, 3).map((r) => <span key={r} className="bt-chip mono">{r.split("/").pop()}</span>)}
              {repos.length > 3 && <span className="bt-chip">+{repos.length - 3}</span>}
              <span className="bt-chip types">{types.map((t) => <SliceGlyph key={t} type={t} size={9} />)}</span>
            </span>
          </div>
          <div className="bt-r">
            <span className="bt-derive-k">Derive</span>
            <button className="bt-derive" onClick={() => derive("dataset")} title="Task-specific dataset — rows content-addressed to source slices">
              <Icon name="datasets" size={12} /> Dataset
            </button>
            <button className="bt-derive" onClick={() => derive("eval")} title="Eval suite — each slice becomes a graded case with its expected outcome">
              <Icon name="check" size={12} /> Eval suite
            </button>
            <button className="bt-derive" onClick={() => derive("skill")} title="Skill — distill the recurring trajectory into a reusable playbook">
              <Icon name="tool" size={12} /> Skill
            </button>
            <button className="bt-derive" onClick={() => derive("arena")} title="Arena — package repo pins + verify commands as a reproducible environment">
              <Icon name="grid" size={12} /> Arena
            </button>
            <button className="bt-derive" onClick={() => derive("capsule")} title="Capsule — seal slices with repo pin + redaction, shareable by content address">
              <Icon name="capsule" size={12} /> Capsule
            </button>
            <button className="bt-clear" onClick={onClear} title="Clear selection">Clear</button>
          </div>
        </>
      )}
    </div>
  );
}

// ── grouped list with unbundling controls ───────────────────────
function groupSlices(slices, mode) {
  const key = mode === "project" ? (s) => s.repo
    : mode === "session" ? (s) => s.session
    : mode === "cluster" ? (s) => s.intentName || "unclustered"
    : (s) => s.type;
  const groups = new Map();
  slices.forEach((s) => {
    const k = key(s);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(s);
  });
  return [...groups.entries()];
}

function IntentSlicesPanel({ intent, matchedRows, slicesOverride, onSelectTrace, presetType, allMode }) {
  const slices = React.useMemo(
    () => slicesOverride || buildMatchedSlices(intent, matchedRows),
    [intent, matchedRows, slicesOverride]
  );
  const [groupBy, setGroupBy] = React.useState("type");
  const [typeFilter, setTypeFilter] = React.useState(null);
  const [harnessFilter, setHarnessFilter] = React.useState(null);
  const [outcomeFilter, setOutcomeFilter] = React.useState(null);
  const [selected, setSelected] = React.useState(() => new Set());
  React.useEffect(() => {
    setSelected(new Set()); setHarnessFilter(null); setOutcomeFilter(null);
    setTypeFilter(presetType || null);
  }, [intent.id, presetType]);

  const visible = slices.filter((s) =>
    (!typeFilter || s.type === typeFilter) &&
    (!harnessFilter || s.harness.id === harnessFilter) &&
    (!outcomeFilter || s.outcome === outcomeFilter)
  );
  const groups = groupSlices(visible, groupBy);
  const toggle = (id) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });
  const toggleGroup = (items) => setSelected((prev) => {
    const next = new Set(prev);
    const allIn = items.every((s) => next.has(s.id));
    items.forEach((s) => allIn ? next.delete(s.id) : next.add(s.id));
    return next;
  });

  return (
    <div className="intent-slices" data-screen-label="intent-slices">
      <SliceComposition slices={slices} typeFilter={typeFilter} onFilter={setTypeFilter} />
      <div className="isl-toolbar">
        <span className="isl-k">Group by</span>
        <div className="isl-groupby" role="tablist">
          {[["type", "Task type"], ["project", "Project"], ["session", "Session"]].concat(allMode ? [["cluster", "Cluster"]] : []).map(([id, lbl]) => (
            <button key={id} className={"isl-gb" + (groupBy === id ? " on" : "")} role="tab" aria-selected={groupBy === id} onClick={() => setGroupBy(id)}>{lbl}</button>
          ))}
        </div>
        <span className="isl-note">{visible.length} slices · {new Set(visible.map((s) => s.repo)).size} projects · content-addressed</span>
      </div>
      <div className="isl-facets">
        <span className="isl-k">Harness</span>
        {[null, "claude-code", "codex", "aider"].map((h) => (
          <button key={h || "all"} className={"isl-facet" + (harnessFilter === h ? " on" : "")} onClick={() => setHarnessFilter(h)}>{h || "All"}</button>
        ))}
        <span className="isl-k out">Outcome</span>
        {[[null, "All"], ["reached", "✓ reached"], ["failed", "✕ failed"]].map(([v, lbl]) => (
          <button key={lbl} className={"isl-facet" + (outcomeFilter === v ? " on" : "")} onClick={() => setOutcomeFilter(v)}>{lbl}</button>
        ))}
        {outcomeFilter === "failed" && <span className="isl-hint">failures make hard eval cases — select and derive</span>}
      </div>

      {groups.length === 0 && (
        <div className="isl-empty">
          No slices match the current filters
          {outcomeFilter ? ` (outcome: ${outcomeFilter})` : ""}{harnessFilter ? ` · harness: ${harnessFilter}` : ""}.
          <button className="isl-empty-clear" onClick={() => { setTypeFilter(null); setHarnessFilter(null); setOutcomeFilter(null); }}>Clear filters</button>
        </div>
      )}

      {groups.map(([k, items]) => (
        <section key={k} className="isl-group">
          <header className="isl-group-h">
            {groupBy === "type" ? (
              <span className="isl-gh-type"><SliceGlyph type={k} size={11} /> {SLICE_TYPES[k] ? SLICE_TYPES[k].name : k}</span>
            ) : groupBy === "project" ? (
              <RepoLabel repo={k} />
            ) : groupBy === "cluster" ? (
              <span className="isl-gh-session"><span className="mono">@{k}</span></span>
            ) : (
              <span className="isl-gh-session">{k}</span>
            )}
            <span className="isl-gh-n mono">{items.length}</span>
            <button className="isl-gh-sel" onClick={() => toggleGroup(items)}>
              {items.every((s) => selected.has(s.id)) ? "Unselect group" : "Select group"}
            </button>
          </header>
          {items.map((s) => (
            <IntentSliceRow
              key={s.id}
              slice={s}
              selected={selected.has(s.id)}
              onToggle={() => toggle(s.id)}
              onOpen={() => onSelectTrace && onSelectTrace(s.traceId)}
            />
          ))}
        </section>
      ))}

      <BundleTray slices={slices} selectedIds={selected} onClear={() => setSelected(new Set())} intent={intent} />
    </div>
  );
}

Object.assign(window, { IntentSlicesPanel, IntentDiagnostics, IntentHeatmap, RefineQueue, buildMatchedSlices, SliceProvenance });
