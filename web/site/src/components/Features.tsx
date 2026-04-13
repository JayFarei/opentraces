import SectionRule from "./SectionRule";

const features = [
  {
    title: "git for traces",
    desc: "init, status, review, push. The workflow you already know, applied to agent sessions.",
  },
  {
    title: "security pipeline",
    desc: "Layered scanning: regex + entropy, optional TruffleHog (800+ detectors), optional LLM PII pass, and optional local LLM session review. Named placeholders like [EMAIL_1] keep traces coherent after redaction.",
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
    desc: "Reset your state, switch machines, re-push safely. murmur3 content hashing prevents duplicates on the remote.",
  },
  {
    title: "commit-anchored",
    desc: "Optional post-commit hook links each trace to the commit(s) it produced. Evidence tiers (tool_emitted, divergence, overlapping, orphan) let you filter by how tightly a session maps to shipped code. Blame any line back to its originating session.",
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
