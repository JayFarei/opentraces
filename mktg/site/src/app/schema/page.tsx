"use client";

import { useState } from "react";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import SectionRule from "@/components/SectionRule";

interface Field {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

function FieldTable({ fields }: { fields: Field[] }) {
  return (
    <table className="field-table">
      <colgroup>
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

/* ── Schema versions ── */

interface SchemaVersion {
  version: string;
  date: string;
  summary: string;
  highlights: string[];
  rationaleUrl: string;
  models: { id: string; title: string; desc: string; fields: Field[] }[];
}

const v010: SchemaVersion = {
  version: "0.1.0",
  date: "2026-03-27",
  summary: "Initial release. 15 models covering trajectory, attribution, outcome signals, and security.",
  highlights: [
    "TraceRecord: one JSONL line per complete agent session",
    "Step: TAO-loop oriented (one LLM API call, not conversational turns)",
    "Outcome: RL-ready signals with derived/inferred/annotated confidence",
    "Attribution: embedded Agent Trace-compatible block (experimental)",
    "Sub-agent hierarchy via parent_step, agent_role, call_type",
    "System prompt deduplication by hash",
    "SecurityMetadata: auto/review mode classification",
    "Content hashing (SHA-256) for cross-upload deduplication",
    "TokenUsage with prefix reuse and cache breakdown fields",
  ],
  rationaleUrl: "https://github.com/opentraces/opentraces/blob/main/packages/opentraces-schema/RATIONALE-0.1.0.md",
  models: [
    {
      id: "trace-record", title: "TraceRecord",
      desc: "Root record. One per session, one JSONL line.",
      fields: [
        { name: "schema_version", type: "string", required: true, description: "e.g. \"0.1.0\"" },
        { name: "trace_id", type: "string", required: true, description: "UUID for this trace" },
        { name: "session_id", type: "string", required: true, description: "Agent's native session ID" },
        { name: "content_hash", type: "string", required: false, description: "SHA-256 for deduplication" },
        { name: "timestamp_start", type: "string", required: false, description: "ISO 8601 start" },
        { name: "timestamp_end", type: "string", required: false, description: "ISO 8601 end" },
        { name: "task", type: "Task", required: false, description: "Task metadata" },
        { name: "agent", type: "Agent", required: true, description: "Agent identity" },
        { name: "environment", type: "Environment", required: false, description: "OS, shell, VCS, languages" },
        { name: "system_prompts", type: "dict", required: false, description: "Deduplicated prompts keyed by hash" },
        { name: "tool_definitions", type: "dict[]", required: false, description: "Available tool schemas" },
        { name: "steps", type: "Step[]", required: false, description: "TAO-loop steps" },
        { name: "outcome", type: "Outcome", required: false, description: "Session outcome" },
        { name: "dependencies", type: "string[]", required: false, description: "Project dependencies" },
        { name: "metrics", type: "Metrics", required: false, description: "Aggregated metrics" },
        { name: "security", type: "SecurityMetadata", required: false, description: "Security tier and redactions" },
        { name: "attribution", type: "Attribution", required: false, description: "Code attribution (experimental)" },
        { name: "metadata", type: "dict", required: false, description: "Extensible key-value pairs" },
      ],
    },
    {
      id: "task", title: "Task",
      desc: "Task metadata for filtering and grouping.",
      fields: [
        { name: "description", type: "string", required: false, description: "What the task is" },
        { name: "source", type: "string", required: false, description: "user_prompt, cli_arg, skill, etc." },
        { name: "repository", type: "string", required: false, description: "owner/repo format" },
        { name: "base_commit", type: "string", required: false, description: "Starting commit SHA" },
      ],
    },
    {
      id: "agent", title: "Agent",
      desc: "Agent identity.",
      fields: [
        { name: "name", type: "string", required: true, description: "claude-code, cursor, codex, etc." },
        { name: "version", type: "string", required: false, description: "Agent version" },
        { name: "model", type: "string", required: false, description: "provider/model-name" },
      ],
    },
    {
      id: "environment", title: "Environment",
      desc: "Runtime context.",
      fields: [
        { name: "os", type: "string", required: false, description: "darwin, linux, etc." },
        { name: "shell", type: "string", required: false, description: "zsh, bash, etc." },
        { name: "vcs", type: "VCS", required: false, description: "type, base_commit, branch, diff" },
        { name: "language_ecosystem", type: "string[]", required: false, description: "python, typescript, etc." },
      ],
    },
    {
      id: "step", title: "Step",
      desc: "One LLM API call in the TAO loop.",
      fields: [
        { name: "step_index", type: "int", required: true, description: "Sequential index" },
        { name: "role", type: "string", required: true, description: "system | user | agent" },
        { name: "content", type: "string", required: false, description: "Message content" },
        { name: "reasoning_content", type: "string", required: false, description: "Chain-of-thought" },
        { name: "model", type: "string", required: false, description: "Model for this step" },
        { name: "system_prompt_hash", type: "string", required: false, description: "Key into system_prompts" },
        { name: "agent_role", type: "string", required: false, description: "main, explore, plan, etc." },
        { name: "parent_step", type: "int", required: false, description: "Parent step index" },
        { name: "call_type", type: "string", required: false, description: "main | subagent | warmup" },
        { name: "subagent_trajectory_ref", type: "string", required: false, description: "Sub-agent session ID" },
        { name: "tools_available", type: "string[]", required: false, description: "Available tool names" },
        { name: "tool_calls", type: "ToolCall[]", required: false, description: "Tool invocations" },
        { name: "observations", type: "Observation[]", required: false, description: "Tool results" },
        { name: "snippets", type: "Snippet[]", required: false, description: "Extracted code blocks" },
        { name: "token_usage", type: "TokenUsage", required: false, description: "Token breakdown" },
        { name: "timestamp", type: "string", required: false, description: "ISO 8601" },
      ],
    },
    {
      id: "tool-call", title: "ToolCall",
      desc: "A tool invocation within a step.",
      fields: [
        { name: "tool_call_id", type: "string", required: true, description: "ID for linking to observations" },
        { name: "tool_name", type: "string", required: true, description: "Tool name" },
        { name: "input", type: "dict", required: false, description: "Input parameters" },
        { name: "duration_ms", type: "int", required: false, description: "Wall-clock time" },
      ],
    },
    {
      id: "observation", title: "Observation",
      desc: "Tool result linked to its ToolCall.",
      fields: [
        { name: "source_call_id", type: "string", required: true, description: "Links to ToolCall" },
        { name: "content", type: "string", required: false, description: "Full output" },
        { name: "output_summary", type: "string", required: false, description: "Lightweight preview" },
        { name: "error", type: "string", required: false, description: "Error info if failed" },
      ],
    },
    {
      id: "token-usage", title: "TokenUsage",
      desc: "Per-step token breakdown.",
      fields: [
        { name: "input_tokens", type: "int", required: false, description: "Input tokens" },
        { name: "output_tokens", type: "int", required: false, description: "Output tokens" },
        { name: "cache_read_tokens", type: "int", required: false, description: "From cache" },
        { name: "cache_write_tokens", type: "int", required: false, description: "Written to cache" },
        { name: "prefix_reuse_tokens", type: "int", required: false, description: "Via prefix caching" },
      ],
    },
    {
      id: "outcome", title: "Outcome",
      desc: "Session outcome for reward modeling.",
      fields: [
        { name: "success", type: "boolean", required: false, description: "Goal achieved" },
        { name: "signal_source", type: "string", required: false, description: "Default: \"deterministic\"" },
        { name: "signal_confidence", type: "string", required: false, description: "derived | inferred | annotated" },
        { name: "description", type: "string", required: false, description: "Outcome description" },
        { name: "patch", type: "string", required: false, description: "Unified diff" },
        { name: "committed", type: "boolean", required: false, description: "Changes committed to git" },
        { name: "commit_sha", type: "string", required: false, description: "Commit SHA" },
      ],
    },
    {
      id: "attribution", title: "Attribution",
      desc: "Code attribution (experimental).",
      fields: [
        { name: "version", type: "string", required: false, description: "Tracks schema_version" },
        { name: "experimental", type: "boolean", required: false, description: "Always true in v0.1.0" },
        { name: "files", type: "AttributionFile[]", required: false, description: "Per-file line ranges" },
      ],
    },
    {
      id: "metrics", title: "Metrics",
      desc: "Session-level aggregates.",
      fields: [
        { name: "total_steps", type: "int", required: false, description: "Step count" },
        { name: "total_input_tokens", type: "int", required: false, description: "Sum of input tokens" },
        { name: "total_output_tokens", type: "int", required: false, description: "Sum of output tokens" },
        { name: "total_duration_s", type: "float", required: false, description: "Wall-clock seconds" },
        { name: "cache_hit_rate", type: "float", required: false, description: "0.0 to 1.0" },
        { name: "estimated_cost_usd", type: "float", required: false, description: "Estimated cost" },
      ],
    },
    {
      id: "security", title: "SecurityMetadata",
      desc: "Security mode and redaction record.",
      fields: [
        { name: "tier", type: "int", required: false, description: "1 (auto), 2 (review), 3 (review, legacy)" },
        { name: "flags_reviewed", type: "int", required: false, description: "Flags reviewed" },
        { name: "redactions_applied", type: "int", required: false, description: "Redactions applied" },
        { name: "classifier_version", type: "string", required: false, description: "Classifier version" },
      ],
    },
  ],
};

/* All versions, newest first. Add new versions here. */
const versions: SchemaVersion[] = [v010];

export default function SchemaPage() {
  const [selectedVersion, setSelectedVersion] = useState(versions[0].version);
  const schema = versions.find((v) => v.version === selectedVersion) ?? versions[0];

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

          <select
            value={selectedVersion}
            onChange={(e) => setSelectedVersion(e.target.value)}
            style={{
              background: "var(--bg-alt)",
              color: "var(--accent)",
              border: "1px solid var(--border)",
              borderRadius: 4,
              padding: "4px 8px",
              fontSize: 14,
              fontFamily: "var(--font-mono)",
              cursor: "pointer",
            }}
          >
            {versions.map((v) => (
              <option key={v.version} value={v.version}>v{v.version}</option>
            ))}
          </select>

          <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
            {schema.date}
          </span>
        </div>

        <p style={{ fontSize: 13, color: "var(--text-muted)", maxWidth: 560, margin: "12px 0 0" }}>
          {schema.summary}
        </p>

        {/* Links row */}
        <div style={{
          display: "flex",
          gap: 20,
          marginTop: 16,
          fontSize: 12,
        }}>
          <a href={schema.rationaleUrl} style={{ color: "var(--cyan)" }}>
            Rationale
          </a>
          <a href="https://github.com/opentraces/opentraces/blob/main/packages/opentraces-schema/CHANGELOG.md" style={{ color: "var(--cyan)" }}>
            Full changelog
          </a>
          <a href="https://github.com/opentraces/opentraces/issues" style={{ color: "var(--cyan)" }}>
            Feedback / change requests
          </a>
        </div>
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 40, paddingBottom: 64 }}>
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
              <div style={{ border: "1px solid var(--border)", overflow: "hidden" }}>
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
