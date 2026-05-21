import SectionRule from "./SectionRule";

const features = [
  {
    title: "private trace bucket",
    desc: "Capture-time envelopes, patch history, trail/context companions, blobs, and manifests stay local until you sync them.",
  },
  {
    title: "trace discovery",
    desc: "trace query, map, slice, and get expose deterministic packets for workflow row builders.",
  },
  {
    title: "optional security tools",
    desc: "Regex, entropy, TruffleHog, privacy-filter, LLM PII, path anonymization, and classifier are explicit and default off.",
  },
  {
    title: "trace trails",
    desc: "trail blame commit, trail blame pr, trail graph, and trail track connect trace patches to Git survival.",
  },
  {
    title: "context tree",
    desc: "ctx commands reconstruct what the agent saw at a step and produce resume packets.",
  },
  {
    title: "workflow templates",
    desc: "Skill-format packages project raw trace evidence into compliant rows for a chosen objective.",
  },
  {
    title: "dataset row review",
    desc: "Approve, reject, reset, schedule, and publish projected rows without pushing the raw bucket.",
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
