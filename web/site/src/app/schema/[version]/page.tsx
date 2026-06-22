import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import SectionRule from "@/components/SectionRule";
import JsonLd from "@/components/JsonLd";
import SchemaVersionSelect from "@/components/SchemaVersionSelect";
import { schemaStructuredData } from "@/lib/structured-data";
import {
  versions,
  versionSlugs,
  findVersion,
  latestVersion,
  type Field,
} from "@/lib/schema-versions";

const SITE = "https://opentraces.ai";

export function generateStaticParams() {
  return versionSlugs().map((version) => ({ version }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ version: string }>;
}): Promise<Metadata> {
  const { version } = await params;
  const schema = findVersion(version);
  if (!schema) return {};
  const label = version === "latest" ? "latest" : `v${schema.version}`;
  const title = `Schema ${label} — opentraces`;
  const canonical = `${SITE}/schema/${version}`;
  return {
    title,
    description: schema.summary,
    alternates: { canonical },
    openGraph: { title, description: schema.summary, url: canonical, type: "article" },
  };
}

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

export default async function SchemaVersionPage({
  params,
}: {
  params: Promise<{ version: string }>;
}) {
  const { version } = await params;
  const schema = findVersion(version);

  if (!schema) notFound();

  const displaySlug = version === "latest" ? "latest" : schema.version;
  const canonical = `${SITE}/schema/${version}`;

  return (
    <div className="container">
      <JsonLd
        data={schemaStructuredData({
          version: schema.version,
          date: schema.date,
          summary: schema.summary,
          canonical,
        })}
      />
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

          <SchemaVersionSelect
            displaySlug={displaySlug}
            latestVersion={latestVersion}
            versions={versions}
            date={schema.date}
          />
        </div>

        <p style={{ fontSize: 13, color: "var(--text-muted)", maxWidth: 560, margin: "12px 0 0" }}>
          {schema.summary}
        </p>
        <p style={{ fontSize: 12, color: "var(--text-dim)", maxWidth: 560, margin: "8px 0 0" }}>
          Read the full{" "}
          <Link href="/docs/schema/overview" style={{ color: "var(--accent)" }}>
            schema documentation
          </Link>{" "}
          for design rationale and usage guides, or see{" "}
          <Link
            href="/docs/contributing/schema-changes"
            style={{ color: "var(--accent)" }}
          >
            contributing to the schema
          </Link>{" "}
          to propose changes.
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
  "schema_version": "${schema.version}",
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
