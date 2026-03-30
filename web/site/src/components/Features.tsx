import SectionRule from "./SectionRule";

const features = [
  {
    title: "git for traces",
    desc: "init, status, review, push. The workflow you already know, applied to agent sessions.",
  },
  {
    title: "private first",
    desc: "Every trace is scanned for secrets, API keys, and PII before it leaves your machine. Push to private HF datasets by default. Share only what you choose.",
  },
  {
    title: "auto or review",
    desc: "Push traces automatically to a private dataset, or review each session before committing to the remote.",
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
    title: "traces inbox",
    desc: "TUI and web interface to review redacted sessions before committing them to your dataset. Commit, reject, redact.",
  },
  {
    title: "automatic collection",
    desc: "Claude Code hook captures sessions after every conversation. No manual parsing.",
  },
  {
    title: "automatic dedup",
    desc: "Content-hash dedup on push. Reset your state, switch machines, re-push safely. No duplicates on the remote.",
  },
  {
    title: "agent-native cli",
    desc: "Every command outputs structured JSON. Built for agents to drive agents.",
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
