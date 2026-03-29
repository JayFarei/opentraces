"use client";

import { useRouter } from "next/navigation";
import { notFound } from "next/navigation";
import { use } from "react";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import SectionRule from "@/components/SectionRule";
import { versions, findVersion, latestVersion, type Field } from "@/lib/schema-versions";

function FieldTable({ fields }: { fields: Field[] }) {
  return (
    <table className="field-table">
      <colgroup className="field-table-colgroup">
        <col style={{ width: "22%" }} />
        <col style={{ width: "16%" }} />
        <col style={{ width: "5%" }} />
        <col style={{ width: "57%" }} />
      </colgroup>
      <thead>
        <tr>
          <th>field</th>
          <th>type</th>
          <th></th>
          <th>description</th>
        </tr>
      </thead>
      <tbody>
        {fields.map((f) => (
          <tr key={f.name}>
            <td style={{ color: "var(--accent)", fontWeight: 500 }}>{f.name}</td>
            <td style={{ color: "var(--cyan)" }}>{f.type}</td>
            <td>{f.required && <span style={{ color: "var(--green)", fontSize: 10 }}>req</span>}</td>
            <td style={{ color: "var(--text-muted)" }}>{f.description}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function SchemaVersionPage({ params }: { params: Promise<{ version: string }> }) {
  const { version } = use(params);
  const router = useRouter();
  const schema = findVersion(version);

  if (!schema) notFound();

  const isLatest = version === "latest" || schema.version === latestVersion;
  const displaySlug = version === "latest" ? "latest" : schema.version;

  return (
    <div className="container">
      <Nav />

      <section style={{ paddingTop: 48, paddingBottom: 32 }}>
        <SectionRule label="schema reference" />

        {/* Version selector bar */}
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          flexWrap: "wrap",
          marginBottom: 8,
        }}>
          <h1 style={{
            fontFamily: "var(--font-display)",
            fontWeight: 400,
            fontSize: "clamp(28px, 4vw, 42px)",
            lineHeight: 1.1,
            letterSpacing: "-0.03em",
            margin: 0,
          }}>
            Schema
          </h1>

          <div style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            background: "var(--bg-alt)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "4px 10px 4px 12px",
          }}>
            <select
              value={displaySlug}
              onChange={(e) => router.push(`/schema/${e.target.value}`)}
              style={{
                background: "transparent",
                color: "var(--accent)",
                border: "none",
                fontSize: 14,
                fontFamily: "var(--font-mono)",
                fontWeight: 500,
                cursor: "pointer",
                outline: "none",
                appearance: "none",
                WebkitAppearance: "none",
                paddingRight: 16,
                backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%239A9895' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")`,
                backgroundRepeat: "no-repeat",
                backgroundPosition: "right 0 center",
              }}
            >
              <option value="latest">latest (v{latestVersion})</option>
              {versions.map((v) => (
                <option key={v.version} value={v.version}>v{v.version}</option>
              ))}
            </select>

            <span style={{
              width: 1,
              height: 16,
              background: "var(--border)",
              flexShrink: 0,
            }} />

            <span style={{ fontSize: 11, color: "var(--text-dim)", whiteSpace: "nowrap" }}>
              {schema.date}
            </span>
          </div>
        </div>

        <p style={{ fontSize: 13, color: "var(--text-muted)", maxWidth: 560, margin: "12px 0 0" }}>
          {schema.summary}
        </p>
      </section>

      <div className="schema-layout">
        {/* Sidebar */}
        <div className="schema-sidebar">
          <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.1em", color: "var(--text-dim)", marginBottom: 12 }}>
            models
          </div>
          {schema.models.map((m) => (
            <a key={m.id} href={`#${m.id}`}>{m.title}</a>
          ))}
          <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.1em", color: "var(--text-dim)", marginTop: 20, marginBottom: 12 }}>
            reference
          </div>
          <a href="#example">Example</a>
        </div>

        {/* Models */}
        <div>
          {schema.models.map((m) => (
            <div key={m.id} id={m.id} style={{ marginBottom: 40 }}>
              <div className="section-title" style={{ fontSize: 20, marginBottom: 4 }}>{m.title}</div>
              <p style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 12 }}>{m.desc}</p>
              <div style={{ border: "1px solid var(--border)", overflowX: "auto", WebkitOverflowScrolling: "touch" }}>
                <FieldTable fields={m.fields} />
              </div>
            </div>
          ))}

          {/* Example */}
          <div id="example" style={{ marginBottom: 48 }}>
            <div className="section-title" style={{ fontSize: 20, marginBottom: 12 }}>Example</div>
            <div className="schema-block">
              <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{`{
  "schema_version": "0.1.0",
  "trace_id": "a4f2b8c1-e2d3-4f5a-b6c7-d8e9f0a1b2c3",
  "session_id": "sess_0x8f2a1b3c",
  "content_hash": "e3b0c44298fc1c14...",
  "timestamp_start": "2026-03-27T14:30:00Z",
  "task": {
    "description": "Add input validation to the signup form",
    "repository": "acme/webapp",
    "base_commit": "a1b2c3d4"
  },
  "agent": {
    "name": "claude-code",
    "version": "1.0.32",
    "model": "anthropic/claude-sonnet-4-20250514"
  },
  "environment": {
    "os": "darwin",
    "shell": "zsh",
    "vcs": { "type": "git", "branch": "main" },
    "language_ecosystem": ["typescript"]
  },
  "system_prompts": {
    "abc123": "You are Claude Code..."
  },
  "steps": [
    {
      "step_index": 0,
      "role": "user",
      "content": "Add Zod validation to the signup form"
    },
    {
      "step_index": 1,
      "role": "agent",
      "content": "I'll add Zod validation...",
      "model": "anthropic/claude-sonnet-4-20250514",
      "system_prompt_hash": "abc123",
      "agent_role": "main",
      "call_type": "main",
      "tool_calls": [{
        "tool_call_id": "tc_001",
        "tool_name": "Edit",
        "input": { "file_path": "src/signup.tsx" },
        "duration_ms": 120
      }],
      "observations": [{
        "source_call_id": "tc_001",
        "output_summary": "Added Zod schema to signup form",
        "content": "File edited successfully"
      }],
      "token_usage": {
        "input_tokens": 4200,
        "output_tokens": 1800,
        "cache_read_tokens": 3800,
        "prefix_reuse_tokens": 3800
      }
    }
  ],
  "outcome": {
    "success": true,
    "signal_source": "deterministic",
    "signal_confidence": "derived",
    "committed": true,
    "commit_sha": "f5e6d7c8"
  },
  "metrics": {
    "total_steps": 2,
    "total_input_tokens": 8400,
    "total_output_tokens": 1800,
    "cache_hit_rate": 0.9,
    "estimated_cost_usd": 0.24
  },
  "security": { "tier": 2, "redactions_applied": 1 }
}`}</pre>
            </div>
          </div>
        </div>
      </div>

      <Footer />
    </div>
  );
}
