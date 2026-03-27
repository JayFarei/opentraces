import SectionRule from "./SectionRule";

const features = [
  {
    title: "passive capture",
    desc: "Reads existing agent log files from disk. No hooks, no daemons, no runtime overhead.",
  },
  {
    title: "three security tiers",
    desc: "Danger mode for OSS. Automated screening for most. Manual review for sensitive code.",
  },
  {
    title: "training-first schema",
    desc: "Outcome signals, sub-agent hierarchy, per-step tokens. Designed for SFT and RL.",
  },
  {
    title: "huggingface native",
    desc: "Publishes JSONL to HF Hub. Loadable via datasets.load_dataset(). No proprietary lock-in.",
  },
  {
    title: "multi-agent support",
    desc: "Claude Code, Codex, Gemini CLI, Cursor, Cline. One schema, many agents.",
  },
  {
    title: "agent trace attribution",
    desc: "Embeds code attribution at file:line granularity. Bridges trajectory and code output.",
  },
  {
    title: "contributor dashboard",
    desc: "Your personal analytics. Cost, cache efficiency, tool usage, success rates.",
  },
  {
    title: "agent-native cli",
    desc: "Every command outputs structured JSON with next_steps. Built for agents to drive.",
  },
  {
    title: "zero config",
    desc: "One pip install. One command. Auto-discovers sessions, auto-detects agents.",
  },
];

export default function Features() {
  return (
    <section>
      <SectionRule label="features" />
      <div className="feature-grid">
        {features.map((f) => (
          <div key={f.title} className="feature-cell">
            <h4>{f.title}</h4>
            <p>{f.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
