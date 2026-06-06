"use client";

import { useState } from "react";
import SectionRule from "./SectionRule";

const modes = [
  {
    id: "explicit",
    name: "Explicit",
    label: "--tools",
    color: "var(--accent)",
    desc: "A workflow names the exact tools it wants. Defaults stay off.",
    terminal: [
      { p: "~$", c: "ot security tools list" },
      { gap: true },
      { di: "[off] regex          detector" },
      { di: "[off] business_logic detector" },
      { di: "[off] path_anonymizer transformer" },
      { di: "[off] classifier     judge" },
      { gap: true },
      { p: "~$", c: "ot security sanitize", f: "--tools regex,business_logic" },
      { ok: "\u2713", di: " tools_applied = ", s: "[regex, business_logic]" },
    ],
  },
  {
    id: "config",
    name: "Config",
    label: "--use-config",
    color: "var(--text)",
    desc: "Configured tools run only after the user enables them.",
    terminal: [
      { p: "~$", c: "ot setup trufflehog" },
      { gap: true },
      { ok: "\u2713", di: " enabled ", s: "trufflehog" },
      { gap: true },
      { p: "~$", c: "ot security sanitize", f: "--use-config" },
      { ok: "\u2713", di: " tools_applied = ", s: "[trufflehog]" },
      { gap: true },
      { p: "~$", c: "ot dataset review approve my-set --all" },
    ],
  },
];

const redactionDemo = [
  { label: "API key", original: "sk-proj-abc123def456ghi789...", redacted: "[API_KEY_1]" },
  { label: "email", original: "jay@company.internal", redacted: "[EMAIL_1]" },
  { label: "DB URL", original: "postgresql://admin:pass@db.internal:5432/prod", redacted: "[DB_URL_1]" },
  { label: "AWS account", original: "arn:aws:iam::123456789012:role/deploy", redacted: "arn:aws:iam::[AWS_ACCOUNT_1]:role/deploy" },
  { label: "path", original: "/Users/jayfarei/src/client-project/", redacted: "/Users/user/src/client-project/" },
];

// The full v0.6.0 security registry: nine per-record tools across three
// protocols, run in a fixed canonical order, every one default off.
const toolGroups = [
  {
    kind: "detectors",
    note: "emit redactable spans",
    color: "var(--c-read)",
    tools: ["regex", "entropy", "trufflehog", "privacy_filter", "llm_pii", "business_logic"],
  },
  {
    kind: "transformers",
    note: "rewrite the record",
    color: "var(--c-write)",
    tools: ["path_anonymizer", "capsule_scope"],
  },
  {
    kind: "judge",
    note: "verdict, no mutation",
    color: "var(--c-plan)",
    tools: ["classifier"],
  },
];

interface TermLine {
  p?: string;
  c?: string;
  f?: string;
  s?: string;
  di?: string;
  di2?: string;
  diEnd?: string;
  n?: string;
  w?: string;
  w2?: string;
  ok?: string;
  gap?: boolean;
}

function TerminalLine({ line }: { line: TermLine }) {
  if (line.gap) return <span className="terminal-line terminal-line-gap" />;
  return (
    <span className="terminal-line">
      {line.p && <span className="p">{line.p} </span>}
      {line.c && <span className="c">{line.c}</span>}
      {line.f && <span className="f"> {line.f}</span>}
      {line.s && <span className="s"> {line.s}</span>}
      {line.di && <span className="di">{line.p ? "" : ""}{line.di}</span>}
      {line.n && <span className="n">{line.n}</span>}
      {line.di2 && <span className="di">{line.di2}</span>}
      {line.w && <span className="w">{line.w}</span>}
      {line.w2 && <span className="w">{line.w2}</span>}
      {line.diEnd && <span className="di">{line.diEnd}</span>}
      {line.ok && <span className="ok">{line.ok}</span>}
    </span>
  );
}

export default function PrivacyTrust() {
  const [activeMode, setActiveMode] = useState("explicit");
  const active = modes.find((t) => t.id === activeMode)!;

  return (
    <section>
      <SectionRule label="privacy & trust" />
      <div className="section-title">Security tools are explicit, optional, and inspectable.</div>

      {/* Redaction demo as the hero visual */}
      <div className="privacy-grid">
        <div style={{ display: "flex", flexDirection: "column" }}>
          <p className="section-sub" style={{ marginBottom: 16 }}>
            Every per-record tool defaults off. A workflow opts into exactly the tools it needs, and they run in one fixed order: detectors that emit redactable spans, transformers that rewrite the record, then a judge that scores sensitivity without mutating anything. Pi raw provider bodies are default-off too, so retained blobs stay local and security-gated before any remote sync.
          </p>

          <div style={{ fontSize: 10, color: "var(--text-dim)", fontFamily: "var(--font-mono)", letterSpacing: "0.04em", marginBottom: 12 }}>
            9 tools {"\u00b7"} canonical order {"\u00b7"} default off {"\u00b7"} SECURITY_VERSION 0.6.0
          </div>

          {/* Tool registry: nine tools grouped by protocol, all default off */}
          <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 20 }}>
            {toolGroups.map((g) => (
              <div key={g.kind} style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
                <div style={{ flex: "0 0 92px", fontFamily: "var(--font-mono)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-secondary)" }}>
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: g.color, flexShrink: 0 }} />
                    {g.kind}
                  </div>
                  <div style={{ fontSize: 9, color: "var(--text-dim)" }}>{g.note}</div>
                </div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", flex: "1 1 0", minWidth: 0 }}>
                  {g.tools.map((t) => (
                    <span
                      key={t}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        border: "1px solid var(--border)",
                        borderLeft: `2px solid ${g.color}`,
                        borderRadius: "var(--radius-sm)",
                        background: `color-mix(in oklch, ${g.color} 8%, var(--surface))`,
                        padding: "4px 8px",
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: "var(--text)",
                      }}
                    >
                      {t}
                      <span style={{ fontSize: 8, color: "var(--text-dim)", border: "1px solid var(--border)", borderRadius: 2, padding: "0 4px", textTransform: "uppercase", letterSpacing: "0.06em" }}>off</span>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div style={{ border: "1px solid var(--border)", background: "var(--bg-alt)", fontFamily: "var(--font-mono)", fontSize: 12, marginTop: "auto" }}>
            <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--border)", background: "var(--surface)", fontSize: 10, color: "var(--text-dim)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
              redaction preview
            </div>
            <div style={{ padding: 16 }}>
              {redactionDemo.map((r) => (
                <div key={r.label} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 9, color: "var(--text-dim)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 3 }}>{r.label}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", minWidth: 0 }}>
                    <span style={{ color: "var(--red)", textDecoration: "line-through", opacity: 0.5, fontSize: 11, wordBreak: "break-all", minWidth: 0 }}>{r.original}</span>
                    <span style={{ color: "var(--text-dim)", fontSize: 10 }}>{"\u2192"}</span>
                    <span style={{ color: "var(--green)", background: "var(--green-bg)", padding: "1px 6px", border: "1px solid var(--green-dim)", fontSize: 11, wordBreak: "break-all" }}>{r.redacted}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Mode selector as companion, not hero */}
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.7, marginBottom: 16 }}>
            Two ways to run tools in a bucket flow or dataset workflow.
          </div>
          <div style={{ display: "flex", gap: 0, marginBottom: 12, border: "1px solid var(--border)" }}>
            {modes.map((t) => (
              <button
                key={t.id}
                onClick={() => setActiveMode(t.id)}
                style={{
                  flex: 1,
                  padding: "10px 12px",
                  borderRight: t.id === "explicit" ? "1px solid var(--border)" : "none",
                  border: "none",
                  borderBottom: activeMode === t.id ? `2px solid ${t.color}` : "2px solid transparent",
                  background: activeMode === t.id ? "var(--surface)" : "transparent",
                  cursor: "pointer",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  textAlign: "center",
                }}
              >
                <div style={{ fontWeight: 500, color: activeMode === t.id ? t.color : "var(--text-muted)" }}>
                  {t.name}
                </div>
                <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 2 }}>{t.label}</div>
              </button>
            ))}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 12, fontFamily: "var(--font-mono)", lineHeight: 1.6 }}>
            {active.desc}
          </div>

          {/* Terminal preview per mode */}
          <div className="terminal" style={{ marginTop: "auto" }}>
            <div className="terminal-bar">
              <span>{active.name.toLowerCase()} tools</span>
            </div>
            <div className="terminal-body" style={{ minHeight: 320, overflowY: "auto" }}>
              {active.terminal.map((line, i) => (
                <TerminalLine key={i} line={line} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
