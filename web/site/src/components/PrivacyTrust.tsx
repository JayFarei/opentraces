"use client";

import { useState, type CSSProperties } from "react";
import SectionRule from "./SectionRule";

type Kind = "detector" | "transformer" | "judge";

interface Redaction {
  before: string;
  after: string;
}

interface Tool {
  name: string;
  kind: Kind;
  color: string;
  catches: string;
  detail: string;
  needs?: string;
  redactions?: Redaction[];
  verdicts?: { risk: string; label: string; flagged: boolean }[];
}

// Each tool carries its OT category hue. Copy is derived directly from the
// implementation in src/opentraces/security/tools/.
const DETECTOR = "var(--c-read)";
const TRANSFORMER = "var(--c-write)";
const JUDGE = "var(--c-plan)";

const TOOLS: Tool[] = [
  {
    name: "regex",
    kind: "detector",
    color: DETECTOR,
    catches: "API keys · tokens · emails · connection strings",
    detail: "Pattern scan with built-in detectors. Deterministic, fast, zero dependencies.",
    redactions: [
      { before: "sk-proj-abc123def456ghi789...", after: "[API_KEY_1]" },
      { before: "jay@company.internal", after: "[EMAIL_1]" },
    ],
  },
  {
    name: "entropy",
    kind: "detector",
    color: DETECTOR,
    catches: "Random-looking secrets no pattern matches",
    detail: "Shannon-entropy scan. Skips tool output and reasoning fields, where false positives run high.",
    redactions: [
      { before: "TOKEN=8Hk2pQ9zXr4mLw7v...", after: "TOKEN=[SECRET_1]" },
      { before: "key=wJalrXUtnFEMI4f3c...", after: "key=[SECRET_2]" },
    ],
  },
  {
    name: "trufflehog",
    kind: "detector",
    color: DETECTOR,
    catches: "800+ credential types, optionally live-verified",
    detail: "Wraps the TruffleHog scanner. With verify_secrets it tests whether a found credential is still active.",
    needs: "needs the trufflehog binary",
    redactions: [
      { before: "AKIAIOSFODNN7EXAMPLE", after: "[AWS_KEY_1]" },
      { before: "ghp_16C7e42F292c6912E7710c8...", after: "[GITHUB_PAT_1]" },
    ],
  },
  {
    name: "privacy_filter",
    kind: "detector",
    color: DETECTOR,
    catches: "People · addresses · emails · phones, in context",
    detail: "OpenAI’s open-weight Privacy Filter, a local token-classification model for context-aware PII no regex can express.",
    needs: "needs transformers + torch",
    redactions: [
      { before: "reviewed by Dr. Sarah Chen", after: "reviewed by [PERSON_1]" },
      { before: "shipping to Boston, MA", after: "shipping to [LOCATION_1]" },
    ],
  },
  {
    name: "llm_pii",
    kind: "detector",
    color: DETECTOR,
    catches: "Context-dependent PII, read in situ",
    detail: "Per-field detection through any OpenAI-compatible endpoint (Ollama, LM Studio, OpenAI). Catches PII only context reveals.",
    needs: "needs an LLM endpoint",
    redactions: [
      { before: "home number 0432 118 994", after: "home number [PHONE_1]" },
      { before: "badge E4471, payroll team", after: "badge [EMPLOYEE_ID_1], payroll team" },
    ],
  },
  {
    name: "business_logic",
    kind: "detector",
    color: DETECTOR,
    catches: "Internal hosts · Jira/Slack URLs · AWS account ids",
    detail: "Promotes internal-infrastructure signals from flags to redactable spans.",
    redactions: [
      { before: "arn:aws:iam::123456789012:role", after: "arn:aws:iam::[AWS_ACCOUNT_1]:role" },
      { before: "cache.prod.internal", after: "[INTERNAL_HOST_1]" },
    ],
  },
  {
    name: "path_anonymizer",
    kind: "transformer",
    color: TRANSFORMER,
    catches: "Local usernames in every path field",
    detail: "Rewrites your username to a stable hash, including path fields detectors never walk.",
    redactions: [
      { before: "/Users/jayfarei/src/app/", after: "/Users/3f9a1c/src/app/" },
      { before: "/home/jayfarei/.aws/creds", after: "/home/3f9a1c/.aws/creds" },
    ],
  },
  {
    name: "capsule_scope",
    kind: "transformer",
    color: TRANSFORMER,
    catches: "Whole prompt-bearing subtrees",
    detail: "Excludes fields wholesale, the only “never leaves” guarantee, independent of what any detector finds.",
    redactions: [
      { before: "context_resume_packet.system_layer", after: "[EXCLUDED]" },
      { before: "slice.steps.3.reasoning_content", after: "[EXCLUDED]" },
    ],
  },
  {
    name: "classifier",
    kind: "judge",
    color: JUDGE,
    catches: "Scores sensitivity — verdict only, no mutation",
    detail: "Flags risky records so a workflow can gate review vs auto-publish. Never rewrites the record.",
    verdicts: [
      { risk: "0.74", label: "2 flags · internal_url, person", flagged: true },
      { risk: "0.02", label: "no classifier flags", flagged: false },
    ],
  },
];

const GROUPS: { kind: Kind; label: string; note: string; color: string }[] = [
  { kind: "detector", label: "Detectors", note: "emit redactable spans", color: DETECTOR },
  { kind: "transformer", label: "Transformers", note: "rewrite the record", color: TRANSFORMER },
  { kind: "judge", label: "Judge", note: "verdict, no mutation", color: JUDGE },
];

// Named bucket-egress policies (opentraces bucket security policy --policy <name>).
// Mirrors the CLI: each preset enables a curated tool set; advanced tools
// (llm_pii, capsule_scope) stay custom-only.
const POLICIES: { id: string; tools: string[] }[] = [
  { id: "off", tools: [] },
  { id: "basic", tools: ["regex", "entropy"] },
  { id: "recommended", tools: ["regex", "entropy", "business_logic", "path_anonymizer", "classifier"] },
  { id: "strict", tools: ["regex", "entropy", "business_logic", "path_anonymizer", "classifier", "trufflehog", "privacy_filter"] },
];

// Example dataset id for the per-dataset `dataset security <ds_id>` surface.
const DEMO_DS = "my-dataset";

// The two load-bearing places the registry runs. Both share one canonical order;
// bucket security guards raw traces before sync (one private bucket), dataset
// security scopes tools to a single dataset's row-producing workflows.
const STAGES = [
  {
    n: "1",
    title: "Bucket entry",
    when: "Every capture, before it lands in your private bucket.",
    cmd: (t: string) => `ot bucket security policy --tool ${t} --enable`,
    effect: "guards raw captured traces before any remote sync",
  },
  {
    n: "2",
    title: "Dataset row",
    when: "When a workflow projects bucket traces into rows, before publish.",
    cmd: (t: string) => `ot dataset security ${DEMO_DS} --tool ${t} --enable`,
    effect: "scoped to one dataset · approved rows only reach the Hub",
  },
];

export default function PrivacyTrust() {
  const [selected, setSelected] = useState("privacy_filter");
  const [policy, setPolicy] = useState("recommended");
  const tool = TOOLS.find((t) => t.name === selected)!;
  const enabled = POLICIES.find((p) => p.id === policy)!.tools;
  const inPolicy = enabled.includes(tool.name);

  return (
    <section>
      <SectionRule label="privacy & trust" />
      <div className="section-title">Security tools to sanitize your traces.</div>

      <p className="section-sub" style={{ marginBottom: 18, maxWidth: 720 }}>
        Your traces are training data, so cleaning them can&rsquo;t cost you the signal. Point detectors,
        transformers, and a judge at exactly the fields that matter, on capture into your bucket and
        again as you build a training dataset, and decide what runs where.
      </p>

      {/* Policy presets — a bucket-egress decision, rendered as pills */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 22 }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--text-dim)" }}>
          bucket policy
        </span>
        <div className="sec-presets" role="group" aria-label="bucket security policy">
          {POLICIES.map((p) => (
            <button
              key={p.id}
              className="sec-preset"
              aria-pressed={policy === p.id}
              onClick={() => setPolicy(p.id)}
            >
              {p.id}
            </button>
          ))}
        </div>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-dim)" }}>
          {enabled.length === 0 ? "no tools enabled" : `${enabled.length} tools on`} {"·"} canonical order {"·"} SECURITY_VERSION 0.6.0
        </span>
      </div>

      <div className="privacy-grid">
        {/* LEFT — the catalogue: one hairline panel, grouped, click to inspect */}
        <div className="sec-panel">
          {GROUPS.map((g) => (
            <div key={g.kind}>
              <div className="sec-ghead">
                <span className="sec-dot" style={{ "--sc": g.color } as CSSProperties} />
                <span className="sec-glabel">{g.label}</span>
                <span className="sec-gnote">{g.note}</span>
              </div>
              {TOOLS.filter((t) => t.kind === g.kind).map((t) => {
                const on = enabled.includes(t.name);
                return (
                  <button
                    key={t.name}
                    className="sec-tool"
                    aria-pressed={t.name === selected}
                    onClick={() => setSelected(t.name)}
                    style={{ "--sc": t.color } as CSSProperties}
                  >
                    <span className="sec-tool-name">
                      <span className="sec-dot" />
                      <span>{t.name}</span>
                      {t.needs && <span className="sec-need" title={t.needs}>{"○"}</span>}
                    </span>
                    <span className={on ? "sec-state on" : "sec-state"} style={{ "--sc": t.color } as CSSProperties}>
                      {on ? "on" : "off"}
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>

        {/* RIGHT — inspector: what the selected tool produces + where it runs */}
        <div className="sec-inspector" style={{ "--sc": tool.color } as CSSProperties}>
          <div className="sec-isec sec-ihead">
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 6 }}>
              <span className="sec-iname">{tool.name}</span>
              <span className="sec-kind">{tool.kind}</span>
              {inPolicy && <span className="sec-inpol">in {policy}</span>}
              {tool.needs && (
                <span style={{ fontSize: 9, color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>{"○"} {tool.needs}</span>
              )}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4 }}>{tool.catches}</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.6, minHeight: 35 }}>{tool.detail}</div>
          </div>

          <div className="sec-isec">
            <div className="sec-eyebrow">
              {tool.kind === "judge" ? "verdict" : tool.kind === "transformer" ? "rewrite" : "redaction"}
            </div>
            {tool.redactions && (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {tool.redactions.map((r, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", minWidth: 0 }}>
                    <span className="sec-before">{r.before}</span>
                    <span className="sec-arrow">{"→"}</span>
                    <span className="sec-after">{r.after}</span>
                  </div>
                ))}
              </div>
            )}
            {tool.verdicts && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {tool.verdicts.map((v, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    <span
                      className="sec-verpill"
                      style={{
                        border: `1px solid ${v.flagged ? "var(--red)" : "var(--green-dim)"}`,
                        color: v.flagged ? "var(--red)" : "var(--green)",
                      }}
                    >
                      {v.flagged ? "flagged" : "clean"}
                    </span>
                    <span style={{ color: "var(--text-muted)" }}>{v.label}</span>
                    <span style={{ color: "var(--text-dim)", marginLeft: "auto" }}>risk {v.risk}</span>
                  </div>
                ))}
                <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 2, lineHeight: 1.5 }}>
                  The record is never mutated — only scored, so a workflow can route it to review or auto-publish.
                </div>
              </div>
            )}
          </div>

          <div className="sec-isec" style={{ marginTop: "auto" }}>
            <div className="sec-eyebrow">available at both stages — you decide where</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
              {STAGES.map((s) => (
                <div key={s.n} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <span className="sec-snum">{s.n}</span>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                      <span style={{ fontSize: 12, color: "var(--text)", fontWeight: 500 }}>{s.title}</span>
                      <span style={{ fontSize: 10, color: "var(--text-dim)" }}>{s.when}</span>
                    </div>
                    <div className="sec-cmd" style={{ marginTop: 6 }}>
                      <span className="pr">~$ </span>{s.cmd(tool.name)}
                    </div>
                    <div style={{ marginTop: 5, fontSize: 10, color: "var(--text-dim)", lineHeight: 1.5 }}>{s.effect}</div>
                  </div>
                </div>
              ))}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.6, borderTop: "1px solid var(--border)", paddingTop: 11, marginTop: 13 }}>
              One registry, one canonical order. <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>bucket security</span> guards raw traces in the one private bucket before sync; <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>dataset security {DEMO_DS}</span> scopes the same tools to a single dataset&rsquo;s row workflows. Inline JSON: <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>ot security sanitize --tools {tool.name}</span>.
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
