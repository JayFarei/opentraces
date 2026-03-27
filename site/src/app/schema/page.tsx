import ThemeToggle from "@/components/ThemeToggle";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import SectionRule from "@/components/SectionRule";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Schema - opentraces.ai",
  description: "Complete schema documentation for the opentraces.ai trace format.",
};

interface Field {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

function FieldTable({ fields }: { fields: Field[] }) {
  return (
    <table className="field-table">
      <thead>
        <tr>
          <th>field</th>
          <th>type</th>
          <th>required</th>
          <th>description</th>
        </tr>
      </thead>
      <tbody>
        {fields.map((f) => (
          <tr key={f.name}>
            <td style={{ color: "var(--accent)", fontWeight: 500 }}>{f.name}</td>
            <td style={{ color: "var(--cyan)" }}>{f.type}</td>
            <td>{f.required ? <span style={{ color: "var(--green)" }}>yes</span> : <span style={{ color: "var(--text-dim)" }}>no</span>}</td>
            <td style={{ color: "var(--text-muted)" }}>{f.description}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const traceRecordFields: Field[] = [
  { name: "trace_id", type: "string", required: true, description: "Unique identifier for the trace (UUID v4)" },
  { name: "session_id", type: "string", required: true, description: "Reference to the original agent session" },
  { name: "agent", type: "string", required: true, description: "Agent identifier (claude-code, codex, cursor, etc.)" },
  { name: "model", type: "string", required: false, description: "Primary model used in the session" },
  { name: "steps", type: "Step[]", required: true, description: "Array of conversation steps/turns" },
  { name: "outcome", type: "Outcome", required: false, description: "Session outcome with success/failure signal" },
  { name: "tokens", type: "TokenUsage", required: false, description: "Aggregate token usage for the session" },
  { name: "cost_usd", type: "number", required: false, description: "Total cost in USD" },
  { name: "security_tier", type: "string", required: true, description: "Security tier applied: danger, automated, manual" },
  { name: "dependencies", type: "string[]", required: false, description: "Detected project dependencies" },
  { name: "contributor", type: "string", required: true, description: "HF username of the contributor" },
  { name: "timestamp", type: "string", required: true, description: "ISO 8601 timestamp of session start" },
];

const stepFields: Field[] = [
  { name: "step_id", type: "string", required: true, description: "Unique step identifier" },
  { name: "role", type: "string", required: true, description: "Message role: user, assistant, system, tool" },
  { name: "content", type: "string", required: false, description: "Text content of the message" },
  { name: "tool_calls", type: "ToolCall[]", required: false, description: "Tool invocations made by the assistant" },
  { name: "tool_result", type: "string", required: false, description: "Result returned from a tool execution" },
  { name: "parent_step", type: "string", required: false, description: "Parent step ID for sub-agent hierarchy" },
  { name: "tokens", type: "TokenUsage", required: false, description: "Token usage for this specific step" },
  { name: "duration_ms", type: "number", required: false, description: "Wall-clock time for this step" },
];

const toolCallFields: Field[] = [
  { name: "tool_name", type: "string", required: true, description: "Name of the tool invoked" },
  { name: "tool_input", type: "object", required: true, description: "Input parameters passed to the tool" },
  { name: "tool_output", type: "string", required: false, description: "Output returned by the tool" },
  { name: "files_modified", type: "string[]", required: false, description: "Files changed by this tool call" },
];

const outcomeFields: Field[] = [
  { name: "success", type: "boolean", required: true, description: "Whether the session achieved its goal" },
  { name: "error", type: "string", required: false, description: "Error message if failed" },
  { name: "metrics", type: "object", required: false, description: "Custom metrics (tests_passed, lint_clean, etc.)" },
];

const tokenFields: Field[] = [
  { name: "input", type: "number", required: true, description: "Input/prompt tokens" },
  { name: "output", type: "number", required: true, description: "Output/completion tokens" },
  { name: "cache_read", type: "number", required: false, description: "Tokens served from cache" },
  { name: "cache_write", type: "number", required: false, description: "Tokens written to cache" },
];

const models = [
  { id: "trace-record", title: "TraceRecord", desc: "The root record. One per session, one JSONL line.", fields: traceRecordFields },
  { id: "step", title: "Step", desc: "A single conversation turn within a trace.", fields: stepFields },
  { id: "tool-call", title: "ToolCall", desc: "A tool invocation within an assistant step.", fields: toolCallFields },
  { id: "outcome", title: "Outcome", desc: "Session outcome signal for training reward.", fields: outcomeFields },
  { id: "token-usage", title: "TokenUsage", desc: "Token counts for cost and efficiency tracking.", fields: tokenFields },
];

export default function SchemaPage() {
  return (
    <>
      <ThemeToggle />
      <div className="container">
        <Nav />

        <section style={{ paddingTop: 48 }}>
          <SectionRule label="schema reference" />
          <h1 style={{
            fontFamily: "var(--font-display)",
            fontWeight: 400,
            fontSize: "clamp(28px, 4vw, 42px)",
            lineHeight: 1.1,
            letterSpacing: "-0.03em",
            marginBottom: 12,
          }}>
            Schema Documentation
          </h1>
          <p className="section-sub">
            Complete reference for the opentraces.ai trace format. Every field documented with types and descriptions.
          </p>
        </section>

        <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: 48, paddingBottom: 64 }}>
          {/* Sidebar */}
          <div className="schema-sidebar">
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.1em", color: "var(--text-dim)", marginBottom: 12 }}>
              models
            </div>
            {models.map((m) => (
              <a key={m.id} href={`#${m.id}`}>{m.title}</a>
            ))}
          </div>

          {/* Content */}
          <div>
            {models.map((m) => (
              <div key={m.id} id={m.id} style={{ marginBottom: 48 }}>
                <div className="section-title" style={{ fontSize: 22 }}>{m.title}</div>
                <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 16 }}>{m.desc}</p>
                <div style={{ border: "1px solid var(--border)", overflow: "hidden" }}>
                  <FieldTable fields={m.fields} />
                </div>
              </div>
            ))}

            {/* Example JSON */}
            <div style={{ marginBottom: 48 }}>
              <div className="section-title" style={{ fontSize: 22, marginBottom: 16 }}>Example</div>
              <div className="schema-block">
                <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{`{
  "trace_id": "a4f2b8c1-e2d3-4f5a-b6c7-d8e9f0a1b2c3",
  "session_id": "sess_0x8f2a1b3c",
  "agent": "claude-code",
  "model": "claude-sonnet-4",
  "steps": [
    {
      "step_id": "step_001",
      "role": "user",
      "content": "Add input validation to the signup form"
    },
    {
      "step_id": "step_002",
      "role": "assistant",
      "content": "I'll add Zod validation...",
      "tool_calls": [
        {
          "tool_name": "edit",
          "tool_input": { "file": "src/signup.tsx", "changes": "..." },
          "files_modified": ["src/signup.tsx"]
        }
      ],
      "tokens": { "input": 4200, "output": 1800 }
    }
  ],
  "outcome": { "success": true, "metrics": { "tests_passed": 12 } },
  "tokens": { "input": 12400, "output": 3200, "cache_read": 8100 },
  "cost_usd": 2.40,
  "security_tier": "automated",
  "dependencies": ["react", "zod", "typescript"],
  "contributor": "jayfarei",
  "timestamp": "2026-03-27T14:30:00Z"
}`}</pre>
              </div>
            </div>
          </div>
        </div>

        <Footer />
      </div>
    </>
  );
}
