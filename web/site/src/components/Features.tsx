import type { CSSProperties } from "react";
import SectionRule from "./SectionRule";

// Ordered to the real pipeline: capture → search → link → context → score →
// project → review → secure → drive. Each cell carries its category hue (the OT
// signature) and the command that runs it.
const features = [
  { color: "var(--c-read)", title: "private trace bucket", cmd: "opentraces bucket status", desc: "Capture-time envelopes, patch history, trail/context companions, blobs, and manifests stay local. Content-hash dedup keeps re-publishing and machine switches safe." },
  { color: "var(--c-user)", title: "trace discovery", cmd: "opentraces trace skills", desc: "query, skills, map, slice, and get expose deterministic packets over a local BM25 + semantic index, without loading full transcripts." },
  { color: "var(--c-git)", title: "trace trails", cmd: "opentraces trail blame commit", desc: "blame commit, blame pr, graph, and track connect each trace patch to the Git history that accepted it, across eight survival states." },
  { color: "var(--c-exec)", title: "context tree", cmd: "opentraces ctx tree", desc: "ctx commands reconstruct what the agent saw at a step and produce resume packets." },
  { color: "var(--c-push)", title: "trace intelligence", cmd: "opentraces trace map --run-intel", desc: "Derive-on-demand signals about a run: context waste, run health, and A/B compare of two traces. No LLM, nothing persisted." },
  { color: "var(--c-plan)", title: "workflow templates", cmd: "opentraces workflow create", desc: "Skill-format packages project raw trace evidence into schema-valid dataset rows for a chosen objective." },
  { color: "var(--c-write)", title: "dataset row review", cmd: "opentraces dataset review", desc: "Approve, reject, reset, schedule, and publish projected rows; raw traces never leave the bucket." },
  { color: "var(--c-error)", title: "security tools", cmd: "opentraces security tools list", desc: "Nine detectors, transformers, and a judge run in one fixed order before egress, all explicit and default off." },
  { color: "var(--c-think)", title: "agent-native cli", cmd: "every command --json", desc: "Structured JSON with next_steps on every command. Built for agents to drive agents." },
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
            <code className="feat-chip">{f.cmd}</code>
          </div>
        ))}
      </div>
    </section>
  );
}
