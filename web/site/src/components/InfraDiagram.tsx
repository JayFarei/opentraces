import Image from "next/image";
import SectionRule from "./SectionRule";

interface Agent {
  name: string;
  ready: boolean;
}

const devTimeAgents: Agent[] = [
  { name: "Claude Code", ready: true },
  { name: "Codex CLI", ready: false },
  { name: "Cursor", ready: false },
  { name: "OpenCode", ready: false },
];

const runTimeAgents: Agent[] = [
  { name: "Claude Code", ready: false },
  { name: "Hermes", ready: true },
  { name: "OpenClaw", ready: false },
  { name: "NemoClaw", ready: false },
];

const pipelineSteps = ["init", "capture", "parse", "enrich", "sanitise"];
const pushModes = [
  { name: "auto", label: "capture, commit, push automatically" },
  { name: "review", label: "human inbox, commit, push (default)" },
];

const useCases = [
  {
    tag: "training / sft",
    title: "Fine-tune on real workflows",
    desc: "Alternating role sequences, tool call/observation pairing, reasoning coverage. Validated against 10 quality checks before upload.",
  },
  {
    tag: "rl / rlhf",
    title: "Reward from outcomes",
    desc: "Committed patches as reward proxies, per-step token costs for cost-penalized reward, sub-agent hierarchy for credit assignment.",
  },
  {
    tag: "analytics",
    title: "Cost and session observability",
    desc: "Cache hit rates, per-step token breakdowns, duration timelines, model distribution. Step-level granularity, not trace-level aggregates.",
  },
  {
    tag: "domain sourcing",
    title: "Filter by ecosystem",
    desc: "Language tags, extracted dependencies, VCS context, code snippets with language annotations. Build domain-specific datasets from HF queries.",
  },
];

function AgentGrid({ label, agents }: { label: string; agents: Agent[] }) {
  return (
    <div className="arch-category">
      <div className="arch-category-label">{label}</div>
      <div className="arch-category-grid">
        {agents.map((a) => (
          <div
            key={a.name}
            className={`arch-agent-box${a.ready ? "" : " arch-agent-soon"}`}
            {...(!a.ready && { title: "Coming soon" })}
          >
            {a.name}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function InfraDiagram() {
  return (
    <section>
      <SectionRule label="how it works" />

      <div className="arch">
        {/* Source agents: two category boxes */}
        <div className="arch-categories">
          <AgentGrid label="dev-time agents" agents={devTimeAgents} />
          <AgentGrid label="run-time agents" agents={runTimeAgents} />
        </div>

        <div className="arch-line" />
        <div className="arch-label">Local traces / Session Hook</div>
        <div className="arch-line" />

        {/* Core pipeline */}
        <div className="arch-core">
          <div className="arch-core-brand">
            <span className="brand-open">open</span>
            <span className="brand-traces">traces</span>
          </div>

          <div className="arch-pipeline">
            {pipelineSteps.map((step, i) => (
              <span key={step}>
                {i > 0 && <span className="arch-arrow">{"\u2192"}</span>}
                <span className="arch-step">{step}</span>
              </span>
            ))}
          </div>

          <div className="arch-line" style={{ marginTop: 12, marginBottom: 4 }} />
          <div className="arch-label">push mode</div>
          <div className="arch-line" style={{ height: 12 }} />

          <div className="arch-fork">
            <div className="arch-fork-rail" />
            {pushModes.map((m) => (
              <div key={m.name} className="arch-fork-branch">
                <div className="arch-fork-stem" />
                <div className="arch-tier-box">
                  <div className="arch-tier-name">{m.name}</div>
                  <div className="arch-tier-label">{m.label}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="arch-line" />
        <div className="arch-label">JSONL shards, private or public</div>
        <div className="arch-line" />

        {/* HF Hub destination */}
        <div className="arch-box arch-dest">
          <Image src="/hf-logo.svg" alt="Hugging Face" width={18} height={18} className="hf-logo hf-logo-light" />
          <Image src="/hf-logo-pirate.svg" alt="Hugging Face" width={18} height={18} className="hf-logo hf-logo-dark" />
          <span>Hugging Face Hub</span>
        </div>
      </div>

      {/* Use cases downstream of pipeline */}
      <div className="use-grid" style={{ marginTop: 48 }}>
        {useCases.map((c) => (
          <div key={c.tag} className="use-card">
            <div className="use-card-tag">{c.tag}</div>
            <h4>{c.title}</h4>
            <p>{c.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
