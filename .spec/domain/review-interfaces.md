---
schema_version: "1.0"
title: Review Interfaces
scope: src/opentraces/clients, web/viewer
---

# Review Interfaces

## Entities

### Three Review Modes
The system provides three review interfaces, all operating on the same staging directory and state manager:

1. **CLI Review** (`cli_review.py`): Terminal-based interactive review. Simplest interface for quick Tier 3 reviews.
2. **TUI Review** (`tui_review.py`): Textual-based terminal UI with richer navigation.
3. **Web Review** (`web/app.py`): Flask-based local web server with React viewer frontend.

### Viewer (React SPA)
A separate React + TypeScript application in `viewer/` that provides trace visualization:
- Tree view of steps
- Timeline visualization
- Step detail panel with tool calls and observations
- Redaction preview and security badges
- Session list with stage grouping
- Keyboard navigation

### StateManager Integration
All review interfaces use `StateManager` to persist decisions. Decisions are authoritative for the push step.

## Business Rules

### Review Workflow
1. Traces are loaded from the staging directory (`~/.opentraces/staging/`)
2. Each trace can be: **approved**, **rejected**, or **skipped**
3. Approved traces are marked `TraceStatus.APPROVED` in StateManager
4. Rejected traces are marked `TraceStatus.REJECTED`
5. Only approved traces proceed to upload (`opentraces push`)

### CLI Review Actions
Interactive loop with choices:
- `[a]pprove` - Mark as approved, advance to next
- `[r]eject` - Mark as rejected, advance to next
- `[s]kip` - Skip without decision, advance to next
- `[v]iew` - Show detailed trace view (does not advance, allows decision after viewing)
- `[q]uit` - Exit review loop

### Trace Summary Display
One-line summary shows:
- Trace ID (first 12 chars)
- Agent name and model (last segment after `/`)
- Task description (first 80 chars)
- Step count and tool call count
- Security flag count (if any, shown as `[N flags]`)

### Trace Detail View
Full step-by-step display with:
- Reasoning content (truncated to 200 chars)
- Step content (full)
- Tool calls with name, ID, and duration
- Observations with content preview (200 chars) or error
- Subagent steps indented with `[sub]` prefix
- Security flags section at bottom with severity and reason

### Web Review Interface
Flask app serves:
- Static trace viewer (React SPA from `viewer/`)
- REST API endpoints for trace data and review actions
- Sample trace generation for demo mode when no real staged traces exist
- StateManager-backed persistent decisions

### Review State Precedence
- Existing decisions from StateManager are loaded first
- Already-decided traces are filtered from the pending queue
- Only pending traces are presented for review

## Calculations

None specific to review. Display only.

## State Machines

Review uses the TraceStatus state machine (defined in `state.py`):
```
discovered -> parsed -> staged -> reviewing -> approved -> committed -> uploading -> uploaded
                                            -> rejected
                                  uploading -> failed -> staged (retry)
```

The review interfaces transition traces between `staged/reviewing` and `approved/rejected`.

## Edge Cases

1. **No staged traces**: CLI prints a message directing the user to run `opentraces enrich` first.
2. **KeyboardInterrupt/EOFError**: CLI catches both and exits gracefully with "Review interrupted."
3. **Invalid input**: CLI re-prompts without advancing the index.
4. **JSONL parsing errors**: `_load_staged_traces` catches `json.JSONDecodeError` and `OSError`, skipping corrupted files silently.
5. **Sample data in web**: When no real staged traces exist, the web app generates 12 sample traces with randomized models, agents, tasks, tool calls, and security flags for demo purposes.
6. **Security flags display**: Traces carry `_security_flags` metadata (list of dicts with `severity`, `type`, `reason`) that are shown during review but not persisted to the final JSONL output.
