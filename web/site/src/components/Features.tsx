import SectionRule from "./SectionRule";

const features = [
  {
    title: "git for traces",
    desc: "init, status, review, push. The workflow you already know, applied to agent sessions.",
  },
  {
    title: "security pipeline",
    desc: "Three-tier scanning: regex redaction, heuristic classification, full local review. Nothing leaves your machine unscanned.",
  },
  {
    title: "auto or review",
    desc: "Set per-project policy. Auto-push to a private dataset, or gate every session through the local inbox first.",
  },
  {
    title: "traces inbox",
    desc: "TUI and web interface to inspect redacted sessions. Approve, reject, or redact individual steps before pushing.",
  },
  {
    title: "schema depth",
    desc: "Steps, tool calls, reasoning, sub-agent hierarchy, token usage, attribution, outcome, and security metadata in one record.",
  },
  {
    title: "huggingface native",
    desc: "Sharded JSONL on HF Hub. Load via datasets.load_dataset(), or mount large datasets as a virtual filesystem. No lock-in, take your data wherever you want.",
  },
  {
    title: "quality scoring",
    desc: "Five persona rubrics score every trace. Upload gates enforce minimums. Re-score remotely with opentraces assess.",
  },
  {
    title: "content-hash dedup",
    desc: "Reset your state, switch machines, re-push safely. Content hashing prevents duplicates on the remote.",
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
