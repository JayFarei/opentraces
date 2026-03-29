import SectionRule from "./SectionRule";

const features = [
  {
    title: "git for traces",
    desc: "init, status, review, push. The workflow you already know, applied to agent sessions.",
  },
  {
    title: "private first",
    desc: "Traces push to private HF datasets by default. Publish when ready, like open-sourcing a repo.",
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
    title: "lazytraces",
    desc: "Full TUI for reviewing traces. Like lazygit, but for your agent sessions. Approve, reject, redact.",
  },
  {
    title: "automatic collection",
    desc: "Claude Code hook captures sessions after every conversation. No manual parsing.",
  },
  {
    title: "agent-native cli",
    desc: "Every command outputs structured JSON. Built for agents to drive agents.",
  },
  {
    title: "contributor dashboard",
    desc: "Your personal analytics. Cost, cache efficiency, tool usage, success rates.",
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
