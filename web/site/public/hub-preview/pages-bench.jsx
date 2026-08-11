// pages-bench.jsx — Bench, today's build: scenario + verifier on a
// disposable box. Arena is the family name (bench · capsule · gym) — it is
// never a surface and never a nav noun; this surface is the bench.
// Gym and the parameterizer are horizon — drawn as horizon, no fake data.

function BenchRunRow({ run, onOpenEvidence, onOpenTrace }) {
  const scn = v2Scenario(run.scenario);
  const vf = v2Verifier(run.verifier);
  return (
    <div className="bench-row" role="button" tabIndex={0} onClick={() => onOpenEvidence(run.evidenceId)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpenEvidence(run.evidenceId); } }}>
      <VerdictPill v={run.verdict} detail={run.failNote} />
      <div className="bench-row-main">
        <div className="bench-row-title">{scn ? scn.name : run.scenario}</div>
        <div className="bench-row-meta">
          <span className="mono dim">{run.id}</span>
          {vf && <VerifierTag name={vf.name} ver={vf.ver} method={vf.method} current={vf.current} small />}
          <span className="mono dim" title="content-addressed application state">app_state@{run.appState}</span>
          {(run.skips || []).length > 0 && (
            <span className="ev-skips" title={run.skips.map(s => s.name + " — " + s.reason).join("\n")}>{run.skips.length} named skip{run.skips.length > 1 ? "s" : ""}</span>
          )}
          {!run.redProof && <span className="ev-red none" title="red_proof_ref: null">red_proof_ref: null</span>}
        </div>
      </div>
      <div className="bench-row-side">
        <span className="bench-box mono" title={"box " + run.box}>{run.box}</span>
        <SandboxTier tier={run.tier} />
        {run.joined ? <AddressLink addr={run.joined} onOpen={onOpenTrace} dim /> : <span className="addr-none mono">no join</span>}
        <FacetChip facet={run.facet} small />
        <span className="ev-when mono">{run.when}</span>
      </div>
    </div>
  );
}

function ScenarioRegistry() {
  return (
    <div className="ev-card">
      <div className="ev-card-head">Scenarios <span className="ev-card-hint">the product's proof obligations</span></div>
      {V2_SCENARIOS.map(s => (
        <div key={s.id} className="scn-row">
          <span className="scn-name">{s.name}</span>
          <span className="scn-meta mono">scn@{s.digest} · {s.steps} steps</span>
        </div>
      ))}
    </div>
  );
}

function VerifierRegistry() {
  return (
    <div className="ev-card">
      <div className="ev-card-head">Verifiers <span className="ev-card-hint">versioned apart from the code under test</span></div>
      {V2_VERIFIERS.map(v => (
        <div key={v.name} className="vf-row">
          <VerifierTag name={v.name} ver={v.ver} method={v.method} current={v.current} small />
          <span className="vf-job">{v.job}</span>
          {!v.current && <span className="vf-latest mono" title="Verdicts standing on the old version age accordingly.">{v.latest} available</span>}
        </div>
      ))}
      <div className="tl-note">Deterministic verdicts cap at <b>scored</b>; agent judges cap at <b>provisional</b>.</div>
    </div>
  );
}

// Horizon strip — concept stays visible, maturity stays honest. No fake data.
function BenchHorizon({ onOpenParameterizer }) {
  return (
    <div className="horizon-strip">
      <div className="horizon-label">Horizon — designed, not built. Nothing below shows live data.</div>
      <div className="horizon-cards">
        <div className="horizon-card">
          <div className="hz-name">Gym <span className="mono hz-tech">app_state · tasks · verifier</span></div>
          <div className="hz-desc">Graded attempts against task families — pass-rate tiers over a reproducible environment. Rides the bench runner.</div>
        </div>
        <button className="horizon-card link" onClick={onOpenParameterizer}>
          <div className="hz-name">Parameterizer <span className="mono hz-tech">slices → tasks / scenarios</span></div>
          <div className="hz-desc">Harvests tasks and scenarios from real slices. The classifier loop lives here — explore the working prototype.</div>
          <span className="hz-go">Open prototype <Icon name="arrow-right" size={12} /></span>
        </button>
        <div className="horizon-card">
          <div className="hz-name">Verdict vs current main <span className="mono hz-tech">capsule reenact</span></div>
          <div className="hz-desc">Re-running a sealed replay against today's main — earned, account-gated, on our box. Results attest as arena labels.</div>
        </div>
      </div>
    </div>
  );
}

function BenchPage({ repoId, onOpenEvidence, onOpenTrace, onOpenParameterizer, onOpenBoxes, embedded }) {
  const [verdictFilter, setVerdictFilter] = React.useState("all");
  const runs = V2_RUNS.filter(r => r.kind === "bench").filter(r => verdictFilter === "all" || r.verdict === verdictFilter);
  const all = V2_RUNS.filter(r => r.kind === "bench");
  const count = (v) => all.filter(r => r.verdict === v).length;
  return (
    <div className={embedded ? "v2-embed" : "landing landing-page v2-page"}>
      {!embedded && (
      <V2Hero
        plain="Bench"
        tech="bench.v0 · app_state · scenario · verifier"
        subtitle="This project's bench — one per project: its scenarios and verifiers on disposable boxes. Every run freezes one record, renders as evidence, and returns to the private home as manufactured record — the bench never writes datasets."
        scope={`${repoId || "jayfarei/opentraces"} · ${all.length} runs · ${count("pass")} pass · ${count("fail")} fail · ${count("skip")} skip`}
      />
      )}

      <div className="v2-filter-row">
        {["all", "pass", "fail", "skip"].map(v => (
          <button key={v} className="v2-filter" aria-current={verdictFilter === v} onClick={() => setVerdictFilter(v)}>
            {v === "all" ? "All runs" : v + " · " + count(v)}
          </button>
        ))}
        <span className="v2-filter-note mono">ot bench list --json</span>
      </div>

      <div className="bench-grid">
        <div className="bench-runs">
          {runs.map(r => <BenchRunRow key={r.id} run={r} onOpenEvidence={onOpenEvidence} onOpenTrace={onOpenTrace} />)}
          {runs.length === 0 && <div className="v2-empty">No runs match.</div>}
        </div>
        <div className="bench-side">
          <ScenarioRegistry />
          <VerifierRegistry />
        </div>
      </div>

      <BenchHorizon onOpenParameterizer={onOpenParameterizer} />
    </div>
  );
}

// ── BenchHubPage — one entry, three renderings of the same machinery:
//   Atlas    — the map of the claims
//   Runs     — the ledger of the runs
//   Evidence — the historic ledger of evidence, analysable or handed to an agent
const BHUB_TABS = [
  { id: "atlas",    label: "Atlas",    hint: "map of the claims" },
  { id: "runs",     label: "Runs",     hint: "ledger of the runs" },
  { id: "evidence", label: "Evidence", hint: "historic ledger" },
];

function BenchHubPage({ repoId, tab, onTab, onOpenEvidence, onOpenTrace, onOpenParameterizer, onOpenBoxes, focusGuarantee }) {
  const t = tab || "atlas";
  const unread = (window.V2_EVIDENCE || []).filter(e => e.unread).length;
  const runsN = V2_RUNS.filter(r => r.kind === "bench").length;
  const holes = V2_GUARANTEES.filter(g => g.state !== "ok").length;
  return (
    <div className="landing landing-page v2-page">
      <V2Hero
        plain="Bench"
        tech="atlas · runs · evidence"
        subtitle="This project's bench — one per project. The atlas maps the claims, the runs ledger shows the machinery honouring them, and evidence is the historic ledger every run freezes."
        scope={`${repoId || "jayfarei/opentraces"} · ${V2_GUARANTEES.length} guarantees (${holes} holes) · ${runsN} runs · ${(window.V2_EVIDENCE || []).length} evidence records`}
        actions={
          <div className="ph-action-group">
            <ToolBtn icon="box" label="Boxes" onClick={onOpenBoxes} />
            <ToolBtn icon="play" label="Run" primary />
          </div>
        }
      />
      <div className="bhub-tabs" role="tablist" aria-label="Bench">
        {BHUB_TABS.map(x => (
          <button key={x.id} className="bhub-tab" role="tab" aria-current={t === x.id} onClick={() => onTab(x.id)}>
            {x.label}
            <span className="bhub-hint">{x.hint}</span>
            {x.id === "evidence" && unread > 0 && <span className="inbox-pill" title={unread + " unread"}>{unread}</span>}
          </button>
        ))}
      </div>
      {t === "atlas" && <AtlasPage repoId={repoId} onOpenEvidence={onOpenEvidence} focusGuarantee={focusGuarantee} embedded />}
      {t === "runs" && (
        <BenchPage repoId={repoId} onOpenEvidence={onOpenEvidence} onOpenTrace={onOpenTrace} onOpenParameterizer={onOpenParameterizer} onOpenBoxes={onOpenBoxes} embedded />
      )}
      {t === "evidence" && <EvidencePage repoId={repoId} onOpenEvidence={onOpenEvidence} onOpenTrace={onOpenTrace} embedded />}
    </div>
  );
}

window.BenchPage = BenchPage;
window.BenchHubPage = BenchHubPage;
