// pages-pr-gate.jsx — the PR page's future spine: the bench change gate.
// An evidence pack (feed digests + page links + atlas delta) attached to
// the PR; unmapped touched surfaces shown as surface-drift. The report
// renders as Evidence — never bespoke HTML.

function ChangeGatePanel({ onOpenEvidence, onOpenChecks }) {
  const ch = V2_GATES.change;
  const packRuns = ["run-d5521", "run-b7d21", "run-a9910"];
  return (
    <div className="prgate" data-screen-label="pr-change-gate">
      <div className="prgate-head">
        <span className="prgate-name"><Icon name="shield" size={14} /> Change gate</span>
        <span className="prgate-verdict" data-v={ch.verdict}>{ch.verdict === "holds-with-drift" ? "holds · 1 surface-drift" : ch.verdict}</span>
        <button className="prgate-atlas" onClick={() => onOpenChecks && onOpenChecks()}>Atlas delta <Icon name="arrow-right" size={11} /></button>
      </div>
      <div className="prgate-body">
        <div className="prgate-col">
          <div className="prgate-k">evidence pack</div>
          {packRuns.map(id => {
            const run = v2Run(id);
            const scn = v2Scenario(run.scenario);
            return (
              <button key={id} className="prgate-run" onClick={() => onOpenEvidence && onOpenEvidence(run.evidenceId)}>
                <VerdictPill v={run.verdict} />
                <span className="prgate-run-name">{scn.name}</span>
                <span className="mono dim">{id}</span>
              </button>
            );
          })}
        </div>
        <div className="prgate-col">
          <div className="prgate-k">touched guarantees</div>
          {ch.touched.map(id => {
            const g = V2_GUARANTEES.find(x => x.id === id);
            return (
              <div key={id} className="prgate-guar">
                <RowStateChip state={g.state} />
                <span>{g.plain}</span>
              </div>
            );
          })}
          {ch.drift.map((d, i) => (
            <div key={i} className="prgate-guar drift">
              <RowStateChip state="surface-drift" />
              <span><span className="mono">{d.surface}</span> — {d.note}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="prgate-foot">
        Verdicts here are the deterministic workflow consumer's — the gate formats the frozen records, it never recomputes them.
        <span className="mono"> ot bench gate change --pr {ch.pr} --json</span>
      </div>
    </div>
  );
}

// v2 wrapper around the existing pulls surface: list untouched; the open PR
// gets the change gate rendered as its spine, above the report.
function RepoPullsPageV2({ repoId, pullId, onOpenPull, onClosePull, onOpenEvidence, onOpenChecks }) {
  if (pullId) {
    return (
      <div className="pullsv2">
        <div className="pullsv2-gate"><ChangeGatePanel onOpenEvidence={onOpenEvidence} onOpenChecks={onOpenChecks} /></div>
        <RepoPullDetail repoId={repoId} pullId={pullId} onBack={onClosePull} />
      </div>
    );
  }
  return <RepoPullsList repoId={repoId} onOpenPull={onOpenPull} />;
}

window.ChangeGatePanel = ChangeGatePanel;
window.RepoPullsPageV2 = RepoPullsPageV2;
