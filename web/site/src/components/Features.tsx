import SectionRule from "./SectionRule";

const features = [
  {
    title: "git for traces",
    desc: "init, status, dataset new, dataset publish. The workflow you know, applied to agent sessions.",
  },
  {
    title: "security pipeline",
    desc: "Regex + entropy + optional TruffleHog and LLM review. Stable placeholders like [EMAIL_1] keep traces coherent.",
  },
  {
    title: "auto or review",
    desc: "Per-dataset policy: auto-approve safe rows, or gate every row through the TUI or browser reviewer.",
  },
  {
    title: "trail blame and graph",
    desc: "Every shipped line back to the prompt behind it. trail blame and trail graph map commits to their agent sessions.",
  },
  {
    title: "schema depth",
    desc: "Steps, tool calls, reasoning, sub-agents, tokens, attribution, outcome in one record.",
  },
  {
    title: "huggingface native",
    desc: "Sharded JSONL on HF Hub. Load via datasets.load_dataset() or mount it as a virtual filesystem. No lock-in.",
  },
  {
    title: "quality scoring",
    desc: "Five persona rubrics score every trace. Publish gates enforce minimums via dataset publish.",
  },
  {
    title: "content-hash dedup",
    desc: "Reset state, switch machines, re-publish safely. murmur3 hashing blocks duplicates on the remote.",
  },
  {
    title: "agent-native cli",
    desc: "Every command emits structured JSON with next_steps. Built for agents to drive agents.",
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
