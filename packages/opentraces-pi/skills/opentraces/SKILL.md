---
name: opentraces
description: Search and use local OpenTraces agent traces, inspect capture status, and build datasets from captured Pi/Claude/Codex sessions.
---

# OpenTraces in Pi

Use OpenTraces when the user asks about prior agent work, captured traces,
bucket search, Trace Trails, Context Tree evidence, capsules, standups, or
workflow-built datasets.

## Tools

- `ot_capture_status` — check package/CLI/project setup. Use this before
  assuming capture is working.
- `ot_search` — search local bucket traces for relevant prior work.
- `ot_trace` — resolve a trace id or `ot://` reference into compact evidence.
- `ot_standup` — query recent traces as standup input (a bounded recent-work query you then summarize).
- `ot_capsule` — capsule helper for previewing or explicitly exporting trace packets.
- `ot_dataset` — list/status/dry-run workflow-built datasets; creation requires an explicit user request.

OpenTraces tool calls are retrieval-first and recursion tagged with
`OPENTRACES_PI_EXTENSION_INTERNAL=1`. Treat mutating actions such as dataset
creation or capsule export as user-approved follow-ups, not background work.

## Setup

Run `/ot-setup` for minimal local capture readiness. It may run
`opentraces init --agent pi` after user approval. Terminal/auth-heavy follow-ups
are reported as `needs_terminal`, for example:

```bash
opentraces setup git
opentraces setup auth
opentraces setup bucket
```

Raw provider bodies are default-off and local/security-gated when explicitly
enabled. Normal Pi work must continue even if OpenTraces capture fails.
