import type { CSSProperties } from "react";
import SectionRule from "./SectionRule";

// Each feature gets a category hue from the action palette (the OT signature:
// one hue per category, reused everywhere).
const features = [
  { title: "private trace bucket", color: "var(--c-read)", desc: "Capture-time envelopes, patch history, trail/context companions, blobs, and manifests stay local until you sync them." },
  { title: "trace discovery", color: "var(--c-user)", desc: "trace query, map, slice, and get expose deterministic packets for workflow row builders." },
  { title: "optional security tools", color: "var(--c-error)", desc: "Regex, entropy, TruffleHog, privacy-filter, LLM PII, path anonymization, and classifier are explicit and default off." },
  { title: "trace trails", color: "var(--c-git)", desc: "trail blame commit, trail blame pr, trail graph, and trail track connect trace patches to Git survival." },
  { title: "context tree", color: "var(--c-exec)", desc: "ctx commands reconstruct what the agent saw at a step and produce resume packets." },
  { title: "workflow templates", color: "var(--c-plan)", desc: "Skill-format packages project raw trace evidence into compliant rows for a chosen objective." },
  { title: "dataset row review", color: "var(--c-push)", desc: "Approve, reject, reset, schedule, and publish projected rows without pushing the raw bucket." },
  { title: "content-hash dedup", color: "var(--c-write)", desc: "Reset state, switch machines, re-publish safely. murmur3 hashing blocks duplicates on the remote." },
  { title: "agent-native cli", color: "var(--c-think)", desc: "Every command emits structured JSON with next_steps. Built for agents to drive agents." },
];

export default function Features() {
  return (
    <section>
      <SectionRule label="features" />
      <div className="feature-grid">
        {features.map((f) => (
          <div key={f.title} className="feature-cell" style={{ "--fc": f.color } as CSSProperties}>
            <span className="feat-mark" aria-hidden="true"><span className="feat-dot" /></span>
            <h4>{f.title}</h4>
            <p>{f.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
