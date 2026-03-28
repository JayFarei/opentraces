"use client";

import { useState } from "react";
import SectionRule from "./SectionRule";

const tiers = [
  {
    id: "open",
    name: "Open",
    label: "tier 1",
    color: "var(--red)",
    desc: "Regex auto-redact, no human review. For open-source and benchmarks.",
    terminal: [
      { p: "~$", c: "opentraces init", f: "--tier", s: "1" },
      { gap: true },
      { ok: "\u2713", di: " tier 1 (open), sessions auto-stage" },
      { ok: "\u2713", di: " installed Claude Code hook" },
      { gap: true },
      { p: "~$", c: "opentraces push" },
      { di: "auto-redacted ", n: "4", diEnd: " secrets (JWT, API key, email, DB URL)" },
      { ok: "\u2713", di: " pushed 12 sessions \u2192 ", s: "jayfarei/opentraces-my-project" },
    ],
  },
  {
    id: "guarded",
    name: "Guarded",
    label: "tier 2 (default)",
    color: "var(--accent)",
    desc: "Heuristic classifier flags internal hostnames, proprietary patterns, de-anonymization risks. Flagged traces need your approval.",
    terminal: [
      { p: "~$", c: "opentraces init" },
      { gap: true },
      { ok: "\u2713", di: " tier 2 (guarded), default" },
      { ok: "\u2713", di: " installed Claude Code hook" },
      { gap: true },
      { p: "~$", c: "opentraces push" },
      { di: "auto-redacted ", n: "3", diEnd: " secrets" },
      { w: "\u26A0", di: " flagged ", w2: "2", diEnd: " sessions, review required" },
      { ok: "\u2713", di: " pushed ", n: "21", diEnd: " clean sessions" },
    ],
  },
  {
    id: "strict",
    name: "Strict",
    label: "tier 3",
    color: "var(--text)",
    desc: "Nothing leaves your machine without explicit approval. Full human-in-the-loop review via TUI or local web UI.",
    terminal: [
      { p: "~$", c: "opentraces init", f: "--tier", s: "3" },
      { gap: true },
      { ok: "\u2713", di: " tier 3 (strict), review everything" },
      { ok: "\u2713", di: " installed Claude Code hook" },
      { gap: true },
      { p: "~$", c: "opentraces review" },
      { di: "8 sessions pending review" },
      { di: "  approve (a) / redact (r) / skip (s) / reject (x)? ", s: "a" },
      { gap: true },
      { p: "~$", c: "opentraces push" },
      { ok: "\u2713", di: " pushed 6 approved sessions" },
    ],
  },
];

const redactionDemo = [
  { label: "API key", original: "sk-proj-abc123def456ghi789...", redacted: "[REDACTED_API_KEY]" },
  { label: "email", original: "jay@company.internal", redacted: "[REDACTED_EMAIL]" },
  { label: "DB URL", original: "postgresql://admin:pass@db.internal:5432/prod", redacted: "[REDACTED_DB_URL]" },
  { label: "path", original: "/Users/jayfarei/src/client-project/", redacted: "/Users/[REDACTED]/src/[REDACTED]/" },
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
  const [activeTier, setActiveTier] = useState("guarded");
  const active = tiers.find((t) => t.id === activeTier)!;

  return (
    <section>
      <SectionRule label="privacy & trust" />
      <div className="section-title">Every trace scrubbed before it leaves your machine.</div>

      {/* Redaction demo as the hero visual */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 32, marginBottom: 48, alignItems: "start" }}>
        <div>
          <p className="section-sub" style={{ marginBottom: 20 }}>
            19 regex patterns, Shannon entropy analysis, context-aware scanning. API keys, emails, database credentials, filesystem paths, all auto-redacted.
          </p>
          <div style={{ border: "1px solid var(--border)", background: "var(--bg-alt)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
            <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--border)", background: "var(--surface)", fontSize: 10, color: "var(--text-dim)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
              redaction preview
            </div>
            <div style={{ padding: 16 }}>
              {redactionDemo.map((r) => (
                <div key={r.label} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 9, color: "var(--text-dim)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 3 }}>{r.label}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                    <span style={{ color: "var(--red)", textDecoration: "line-through", opacity: 0.5, fontSize: 11 }}>{r.original}</span>
                    <span style={{ color: "var(--text-dim)", fontSize: 10 }}>{"\u2192"}</span>
                    <span style={{ color: "var(--green)", background: "var(--green-bg)", padding: "1px 6px", border: "1px solid var(--green-dim)", fontSize: 11 }}>{r.redacted}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Tier selector as companion, not hero */}
        <div>
          <div style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.7, marginBottom: 16 }}>
            Three security tiers, configured per-project. The sensitivity of a side project differs from a client codebase.
          </div>
          <div style={{ display: "flex", gap: 0, marginBottom: 16, border: "1px solid var(--border)" }}>
            {tiers.map((t) => (
              <button
                key={t.id}
                onClick={() => setActiveTier(t.id)}
                style={{
                  flex: 1,
                  padding: "10px 12px",
                  borderRight: t.id !== "strict" ? "1px solid var(--border)" : "none",
                  border: "none",
                  borderBottom: activeTier === t.id ? `2px solid ${t.color}` : "2px solid transparent",
                  background: activeTier === t.id ? "var(--surface)" : "transparent",
                  cursor: "pointer",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  textAlign: "center",
                }}
              >
                <div style={{ fontWeight: 500, color: activeTier === t.id ? t.color : "var(--text-muted)" }}>
                  {t.name}
                </div>
                <div style={{ fontSize: 9, color: "var(--text-dim)", marginTop: 2 }}>{t.label}</div>
              </button>
            ))}
          </div>

          {/* Terminal preview per tier */}
          <div className="terminal" style={{ minHeight: 200 }}>
            <div className="terminal-bar">
              <span>{active.name.toLowerCase()} mode</span>
            </div>
            <div className="terminal-body" style={{ minHeight: 160 }}>
              {active.terminal.map((line, i) => (
                <TerminalLine key={i} line={line} />
              ))}
            </div>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 8, fontFamily: "var(--font-mono)" }}>
            {active.desc}
          </div>
        </div>
      </div>
    </section>
  );
}
