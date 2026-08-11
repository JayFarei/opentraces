// pages-atlas.jsx — the Atlas: the bench's founder-altitude table. Scoped to
// a project — every project carries its own atlas. One table —
// product guarantees in product language, one row per guarantee. Six named
// row states, two gates (change gate on a PR, release gate on the whole
// atlas). Every hole is a named row state, never an absence.
// SOPs live here too: an SOP is a promise + a verifier; a violation is a label.

function AtlasLastRun({ g, onOpenEvidence }) {
  const run = g.lastRun ? v2Run(g.lastRun) : null;
  const inline = g.lastRunInline;
  if (!run && !inline) return <span className="atlas-never mono">never run</span>;
  const verdict = run ? run.verdict : inline.verdict;
  const when = run ? run.when : inline.when;
  const evId = run ? run.evidenceId : null;
  return (
    <span className="atlas-lastrun">
      <VerdictPill v={verdict} />
      <span className="atlas-when mono">{when}</span>
      {evId ? (
        <button className="atlas-rewatch" onClick={(e) => { e.stopPropagation(); onOpenEvidence(evId); }} title="Open the evidence record — rewatch the run.">
          <Icon name="play" size={10} /> rewatch
        </button>
      ) : (
        <span className="atlas-rewatch off" title="No cast for this run — said honestly.">no cast</span>
      )}
    </span>
  );
}

function AtlasRow({ g, expanded, onToggle, onOpenEvidence }) {
  const vf = g.verifier ? v2Verifier(g.verifier) : null;
  return (
    <>
      <div className="atlas-row" role="button" tabIndex={0} data-state={g.state} data-expanded={expanded} onClick={onToggle} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}>
        <span className="atlas-cell c-state"><RowStateChip state={g.state} /></span>
        <span className="atlas-cell c-guar">
          <span className="atlas-plain">{g.plain}</span>
          <span className="atlas-tech mono">{g.tech}{g.sop ? " · promise (SOP)" : ""}</span>
        </span>
        <span className="atlas-cell c-scn mono">
          {g.scenario ? (v2Scenario(g.scenario) || {}).name : <span className="atlas-hole">no scenario bound</span>}
        </span>
        <span className="atlas-cell c-vf">
          {vf ? <VerifierTag name={vf.name} ver={vf.ver} method={vf.method} current={vf.current} small />
              : <span className="atlas-hole mono">no verifier</span>}
          {g.standing && <TrustChip standing={g.standing} method="agent" small />}
        </span>
        <span className="atlas-cell c-run"><AtlasLastRun g={g} onOpenEvidence={onOpenEvidence} /></span>
        <span className="atlas-cell c-fresh">
          {g.freshness ? <FreshnessTag band={g.freshness.band} label={g.freshness.label} /> : <span className="atlas-hole mono">—</span>}
        </span>
      </div>
      {expanded && (
        <div className="atlas-expand" data-state={g.state}>
          <div className="atlas-ex-row">
            <span className="atlas-ex-k">surfaces</span>
            <span className="atlas-ex-v">{g.surfaces.map(s => <span key={s} className="atlas-surface mono">{s}</span>)}</span>
          </div>
          <div className="atlas-ex-row">
            <span className="atlas-ex-k">against reward hacking</span>
            <span className="atlas-ex-v"><ArhChips chips={g.chips} /></span>
          </div>
          <div className="atlas-ex-row">
            <span className="atlas-ex-k">state</span>
            <span className="atlas-ex-v atlas-ex-hint">{(V2_ROW_STATES[g.state] || {}).hint}
              {g.worldNote ? " — " + g.worldNote : ""}{g.driftNote ? " — " + g.driftNote : ""}
              {g.redProofRef === null ? <span className="mono"> red_proof_ref: null</span> : null}
            </span>
          </div>
          {g.sop && (
            <div className="atlas-ex-row">
              <span className="atlas-ex-k">how it's judged</span>
              <span className="atlas-ex-v atlas-ex-hint">
                The judge is a verifier (<span className="mono">method=agent</span>) and its labels cap at{" "}
                <b>provisional</b> on the trust ladder. Violations are labels on the ledger, not a parallel subsystem.
              </span>
            </div>
          )}
        </div>
      )}
    </>
  );
}

function GatePanel({ onOpenEvidence }) {
  const ch = V2_GATES.change;
  const rel = V2_GATES.release;
  return (
    <div className="gate-grid">
      <div className="gate-card" data-verdict={ch.verdict}>
        <div className="gate-head">
          <span className="gate-name">Change gate</span>
          <span className="gate-scope mono">PR #{ch.pr}</span>
        </div>
        <div className="gate-title">{ch.title}</div>
        <div className="gate-rows">
          {ch.touched.map(id => {
            const g = V2_GUARANTEES.find(x => x.id === id);
            return (
              <div key={id} className="gate-row">
                <RowStateChip state={g.state} />
                <span className="gate-row-name">{g.plain}</span>
              </div>
            );
          })}
          {ch.drift.map((d, i) => (
            <div key={"d" + i} className="gate-row drift">
              <RowStateChip state="surface-drift" />
              <span className="gate-row-name"><span className="mono">{d.surface}</span> — {d.note}</span>
            </div>
          ))}
        </div>
        <div className="gate-foot">The gate attaches an evidence pack to the PR: feed digests, page links, and this atlas delta. Unmapped touched surfaces surface as <span className="mono">surface-drift</span> — never silently.</div>
      </div>
      <div className="gate-card release">
        <div className="gate-head">
          <span className="gate-name">Release gate</span>
          <span className="gate-scope mono">whole atlas</span>
        </div>
        <div className="gate-big">
          <span className="gate-n">{V2_GUARANTEES.length - rel.blocking.length}</span>
          <span className="gate-of">of {V2_GUARANTEES.length} rows fresh &amp; proven</span>
        </div>
        <div className="gate-blockers">
          {rel.blocking.map(id => {
            const g = V2_GUARANTEES.find(x => x.id === id);
            return <span key={id} className="gate-blocker" title={g.plain}><RowStateChip state={g.state} /></span>;
          })}
        </div>
        <div className="gate-foot">{rel.note}</div>
      </div>
    </div>
  );
}

// The real admission contract is the arena-ready app — not a wizard.
function ArenaReadyCard() {
  const items = [
    { k: "capability manifest", ok: true,  note: "machine-readable · 15 verbs declared" },
    { k: "endpoint-override seams", ok: false, note: "14 of 15 verbs — `ot bench show` missing its seam" },
    { k: "scriptable public surfaces", ok: true, note: "all declared surfaces drive headless" },
  ];
  return (
    <div className="ev-card arena-ready">
      <div className="ev-card-head">Arena-ready <span className="ev-card-hint">the admission contract is the app itself</span></div>
      {items.map(it => (
        <div key={it.k} className="ar-row">
          <span className={"ar-dot" + (it.ok ? " ok" : "")} />
          <span className="ar-k">{it.k}</span>
          <span className="ar-note mono">{it.note}</span>
        </div>
      ))}
    </div>
  );
}

function AtlasPage({ repoId, onOpenEvidence, focusGuarantee, embedded }) {
  const [expanded, setExpanded] = React.useState(focusGuarantee || null);
  const [stateFilter, setStateFilter] = React.useState("all");
  React.useEffect(() => { if (focusGuarantee) setExpanded(focusGuarantee); }, [focusGuarantee]);

  const holes = V2_GUARANTEES.filter(g => g.state !== "ok");
  const list = V2_GUARANTEES.filter(g => stateFilter === "all" || g.state === stateFilter);
  const presentStates = [...new Set(V2_GUARANTEES.map(g => g.state))];

  return (
    <div className={embedded ? "v2-embed" : "landing landing-page v2-page"}>
      {!embedded && (
      <V2Hero
        plain="Atlas"
        tech="guarantee + scenario + verifier + label"
        subtitle="Did this product's promises hold? One row per guarantee, in product language. Every hole is a named row state, never an absence."
        scope={`${repoId || "jayfarei/opentraces"} · ${V2_GUARANTEES.length} guarantees · ${V2_GUARANTEES.length - holes.length} fresh · ${holes.length} named holes`}
      />
      )}

      <GatePanel onOpenEvidence={onOpenEvidence} />

      <div className="v2-filter-row">
        <button className="v2-filter" aria-current={stateFilter === "all"} onClick={() => setStateFilter("all")}>All rows</button>
        {presentStates.filter(s => s !== "ok").map(s => (
          <button key={s} className="v2-filter" aria-current={stateFilter === s} onClick={() => setStateFilter(s)}>
            {(V2_ROW_STATES[s] || {}).label} · {V2_GUARANTEES.filter(g => g.state === s).length}
          </button>
        ))}
      </div>

      <div className="atlas-table" role="table" aria-label="Atlas">
        <div className="atlas-head" role="row">
          <span className="atlas-cell c-state">state</span>
          <span className="atlas-cell c-guar">guarantee</span>
          <span className="atlas-cell c-scn">scenario</span>
          <span className="atlas-cell c-vf">verifier</span>
          <span className="atlas-cell c-run">last run</span>
          <span className="atlas-cell c-fresh">freshness</span>
        </div>
        {list.map(g => (
          <AtlasRow
            key={g.id} g={g}
            expanded={expanded === g.id}
            onToggle={() => setExpanded(expanded === g.id ? null : g.id)}
            onOpenEvidence={onOpenEvidence}
          />
        ))}
      </div>

      <ArenaReadyCard />

      <div className="atlas-world">
        <div className="atlas-world-head">
          <span className="gate-name">World signals</span>
          <span className="ev-card-hint">what reality said later — the delayed second label. It never rewrites a verdict; the disagreement pair is the finding.</span>
        </div>
        <div className="dp-grid">
          {V2_DISAGREEMENTS.map(dp => (
            <DisagreementCard key={dp.id} dp={dp} onOpenEvidence={onOpenEvidence} onOpenChecks={(gid) => setExpanded(gid)} />
          ))}
        </div>
        <div className="ev-card wf-feed">
          <div className="ev-card-head">Survivorship feed <span className="ev-card-hint">subject · observation · observed_at · source</span></div>
          {V2_WORLD.filter(w => !w.horizon).map(w => (
            <div key={w.id} className="wf-row">
              <span className="wf-kind mono">{w.subject.kind}</span>
              <span className="wf-ref mono">{w.subject.ref}</span>
              <SurvivorshipChip obs={w.observation} />
              <span className="wf-note">{w.note || ""}</span>
              <span className="wf-at mono">{w.at}</span>
              <span className="wf-src">{w.source}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

window.AtlasPage = AtlasPage;
window.GatePanel = GatePanel;
