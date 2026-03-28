import SectionRule from "./SectionRule";

const agents = ["Claude Code", "Codex CLI", "Cursor", "Gemini CLI", "OpenCode", "Cline"];
const steps = ["init", "capture", "stage", "review", "push"];
const tiers = [
  { name: "danger", label: "tier 1" },
  { name: "automated", label: "tier 2" },
  { name: "manual", label: "tier 3" },
];

export default function InfraDiagram() {
  return (
    <section>
      <SectionRule label="infrastructure" />
      <div className="section-title" style={{ textAlign: "center" }}>Infrastructure</div>
      <p className="section-sub" style={{ textAlign: "center", margin: "8px auto 40px", maxWidth: 520 }}>
        Hook captures sessions automatically. Review locally, push to HF Hub as private or public datasets.
      </p>

      <div className="arch">
        {/* Source agents row */}
        <div className="arch-agents">
          {agents.map((name) => (
            <div key={name} className="arch-agent-box">{name}</div>
          ))}
        </div>

        <div className="arch-line" />
        <div className="arch-label">Session Hook</div>
        <div className="arch-line" />

        {/* Core pipeline */}
        <div className="arch-core">
          <div className="arch-core-brand">
            <span className="brand-open">open</span>
            <span className="brand-traces">traces</span>
          </div>

          <div className="arch-pipeline">
            {steps.map((step, i) => (
              <span key={step}>
                {i > 0 && <span className="arch-arrow">{"\u2192"}</span>}
                <span className="arch-step">{step}</span>
              </span>
            ))}
          </div>

          <div className="arch-tiers">
            {tiers.map((t) => (
              <div key={t.name} className="arch-tier-box">
                <div className="arch-tier-name">{t.name}</div>
                <div className="arch-tier-label">{t.label}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="arch-line" />
        <div className="arch-label">JSONL shards, private or public</div>
        <div className="arch-line" />

        {/* HF Hub destination */}
        <div className="arch-box arch-dest">
          <span style={{ fontSize: 15 }}>{"\uD83E\uDD17"}</span>
          <span>Hugging Face Hub</span>
        </div>
      </div>
    </section>
  );
}
