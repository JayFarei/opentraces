// pages-world.jsx — World signals: the delayed second label. Git
// survivorship today; production metrics on the horizon. The marquee is
// the disagreement pair — check said green, world said reverted — the
// most valuable thing the system mines.

const WF_TONE = { alive: "ok", reverted: "red", transformed: "warn", lost: "red", moved: "warn", "—": "dim" };

function SurvivorshipChip({ obs }) {
  return <span className={"surv-chip sv-" + (WF_TONE[obs] || "dim")}>{obs}</span>;
}

function DisagreementCard({ dp, onOpenEvidence, onOpenChecks }) {
  const g = V2_GUARANTEES.find(x => x.id === dp.guarantee);
  const run = v2Run(dp.check.run);
  const vf = v2Verifier(dp.check.verifier);
  const wf = V2_WORLD.find(w => w.id === dp.world.fact);
  return (
    <div className="dp-card">
      <div className="dp-head">
        <span className="dp-badge">disagreement pair</span>
        <button className="dp-guar" onClick={() => onOpenChecks(dp.guarantee)} title="The guarantee both labels speak about.">
          <Icon name="shield" size={12} /> {g.plain}
        </button>
      </div>
      <div className="dp-pair">
        <div className="dp-side check">
          <div className="dp-side-k">the check said</div>
          <div className="dp-side-v">
            <VerdictPill v={dp.check.verdict} />
            <span className="mono dim">{dp.check.when}</span>
          </div>
          <div className="dp-side-meta">
            {vf && <VerifierTag name={vf.name} ver={vf.ver} method={vf.method} small />}
            {run && run.evidenceId
              ? <button className="atlas-rewatch" onClick={() => onOpenEvidence(run.evidenceId)}><Icon name="play" size={10} /> rewatch</button>
              : <span className="mono dim">{dp.check.run}</span>}
          </div>
        </div>
        <div className="dp-vs">≠</div>
        <div className="dp-side world">
          <div className="dp-side-k">the world said</div>
          <div className="dp-side-v">
            <SurvivorshipChip obs={dp.world.said} />
            {wf && <span className="mono dim">{wf.at}</span>}
          </div>
          <div className="dp-side-meta">{dp.world.detail}</div>
        </div>
      </div>
      <div className="dp-foot">
        <span className="dp-status mono">{dp.status}</span>
        <span className="dp-note">Neither label overwrites the other — the pair itself is the finding.</span>
      </div>
    </div>
  );
}

function WorldPage({ onOpenEvidence, onOpenChecks, onOpenTrace }) {
  const live = V2_WORLD.filter(w => !w.horizon);
  return (
    <div className="landing landing-page v2-page">
      <V2Hero
        plain="World signals"
        tech="world_fact"
        subtitle="What did reality say later? World facts arrive late and land as the delayed second label — they never rewrite a check's verdict."
        scope={`${live.length} facts · git survivorship · ${V2_DISAGREEMENTS.length} open disagreement pairs`}
      />

      <div className="dp-grid">
        {V2_DISAGREEMENTS.map(dp => (
          <DisagreementCard key={dp.id} dp={dp} onOpenEvidence={onOpenEvidence} onOpenChecks={onOpenChecks} />
        ))}
      </div>

      <div className="ev-card wf-feed">
        <div className="ev-card-head">Feed <span className="ev-card-hint">subject · observation · observed_at · source</span></div>
        {live.map(w => (
          <div key={w.id} className="wf-row">
            <span className="wf-kind mono">{w.subject.kind}</span>
            {w.subject.kind === "trace:step"
              ? <AddressLink addr={w.subject.ref} onOpen={onOpenTrace} />
              : <span className="wf-ref mono">{w.subject.ref}</span>}
            <SurvivorshipChip obs={w.observation} />
            <span className="wf-note">{w.note || ""}</span>
            <span className="wf-at mono">{w.at}</span>
            <span className="wf-src">{w.source}</span>
          </div>
        ))}
      </div>

      <div className="horizon-strip">
        <div className="horizon-label">Horizon</div>
        <div className="horizon-cards">
          <div className="horizon-card">
            <div className="hz-name">Production metrics <span className="mono hz-tech">world_fact · metric</span></div>
            <div className="hz-desc">Latency, error rates, and usage arriving as late labels on the same subjects — the second opinion at scale.</div>
          </div>
        </div>
      </div>
    </div>
  );
}

window.WorldPage = WorldPage;
window.DisagreementCard = DisagreementCard;
window.SurvivorshipChip = SurvivorshipChip;
window.WF_TONE = WF_TONE;
