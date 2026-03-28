---
title: "Community Claude Code JSONL Parsers: Scouting Brief for opentraces Parser & Viewer Improvements"
date: 2026-03-28
mode: ultradeep
sources: 5 repositories + community discussions
status: complete
---

# Community Claude Code JSONL Parsers: What opentraces Can Learn

> Research date: 2026-03-28
> Category: competitive analysis / integration scouting
> Purpose: Identify concrete parsing gaps and viewer improvements from 5 community projects

---

## Executive Summary

Five community projects parse Claude Code JSONL sessions with varying depth. Comparing them against opentraces' `ClaudeCodeParser` and viewer reveals **12 concrete parsing gaps** and **9 viewer improvements** worth adopting. The highest-value findings are: (1) claude-code-log's 16 typed tool models expose fields opentraces silently drops (Bash `description`, Edit `diffs[]`, Read `system_reminder`, WebFetch `duration_ms`); (2) simon willison's `isCompactSummary` and `isMeta` flag extraction enables context-window-management analysis that no other tool captures; (3) ccusage's tiered pricing model with LiteLLM integration would make opentraces' `estimated_cost_usd` far more accurate; (4) the viewer is missing diff rendering, snippet display, metrics dashboard, and system prompt inspection that community tools handle well.

---

## 1. Projects Scouted

| Project | Author | Stars | Stack | Depth |
|---------|--------|-------|-------|-------|
| [claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) | Simon Willison | ~2K | Python/Jinja2 | Medium (HTML export) |
| [claude-code-log](https://github.com/daaain/claude-code-log) | Daniel Demmel | ~500 | Python/Textual/Jinja2 | Deep (87 test files, 16 tool types) |
| [claude-JSONL-browser](https://github.com/withLinda/claude-JSONL-browser) | withLinda | ~200 | Next.js/React | Shallow (markdown converter) |
| [ccusage](https://github.com/ryoppippi/ccusage) | ryoppippi | 12K | TypeScript/Valibot | Deep (pricing/analytics) |
| claude-devtools | Unknown | N/A | Unknown | Could not verify existence |

---

## 2. Parsing Gaps: What opentraces Misses

### 2.1 JSONL Line Types opentraces Ignores

opentraces' `ClaudeCodeParser._extract_metadata()` only processes `user` and `assistant` lines. Community projects reveal additional line types:

| Line Type | Who Handles It | What It Contains | Recommended Action |
|-----------|---------------|------------------|--------------------|
| `summary` | simonw, claude-code-log | Claude-generated session summary, `leafUuid` for cross-session linking | Capture as `task.description` fallback, store `leafUuid` in metadata |
| `queue-operation` | claude-code-log | `enqueue`/`dequeue`/`remove`/`popAll` ops, inner JSON with model info | Extract model from inner JSON (opentraces partially does this), capture `remove` ops as user steering signals |
| `system` | claude-code-log | System instructions | Already handled via `system_prompt_raw` extraction |
| `file-history-snapshot` | claude-code-log (skips) | Unknown | Skip (no value) |
| `progress` | claude-code-log (skips) | Unknown | Skip (no value) |

**Priority**: HIGH for `summary` (improves `task.description` quality), MEDIUM for `queue-operation.remove` (captures user cancellation/steering).

### 2.2 Per-Message Fields opentraces Drops

opentraces extracts `timestamp`, `model`, and `content` from each message but drops several fields that community parsers capture:

| Field | Who Captures | Value for opentraces |
|-------|-------------|---------------------|
| `isCompactSummary` | simonw | Identifies context-window compaction events. Critical for understanding when Claude's context was compressed. Could power a `call_type="compaction"` step type. |
| `isMeta` | simonw, claude-code-log | Marks system-injected meta messages. Useful for filtering noise from training data. |
| `uuid` / `leafUuid` | simonw, claude-code-log | Message-level unique IDs. Enables cross-session linking and deduplication. |
| `parentUuid` / `isSidechain` | claude-code-log | Tree structure of message hierarchy. opentraces uses sequential ordering. |
| `userType` | claude-code-log | Distinguishes user types. |
| `costUSD` | ccusage | Pre-calculated cost per API response. More accurate than opentraces' post-hoc `estimated_cost_usd`. |
| `requestId` | ccusage | Deduplication key. |
| `speed` | ccusage | `standard` vs `fast` mode flag. Affects pricing. |
| `version` | simonw, ccusage | Claude Code version. Already in opentraces as `agent.version`. |
| `gitBranch` | all | Per-message branch. opentraces captures once at session level. |
| `cwd` | simonw, claude-code-log | Per-message working directory. Could change mid-session. |

**Priority**: HIGH for `costUSD` (direct cost improvement), `isCompactSummary` (training data quality signal), `speed` (pricing accuracy). MEDIUM for `uuid`/`leafUuid` (dedup).

### 2.3 Tool-Specific Fields opentraces Drops

opentraces stores tool call inputs as opaque `dict` via `ToolCall(input=block.get("input", {}))`. claude-code-log parses 16 tool types into typed models, revealing fields that matter for downstream consumers:

| Tool | Field | Value |
|------|-------|-------|
| **Bash** | `description` | Human-readable intent behind the command. Valuable for training data (action-intent pairs). |
| **Bash** | `timeout`, `run_in_background`, `dangerouslyDisableSandbox` | Execution context. `dangerouslyDisableSandbox` is a security signal. |
| **Read** | `system_reminder` (in output) | System reminders embedded in file reads. Should be stripped for training, preserved for analysis. |
| **Edit** | `diffs[]` (in output) | Structured diff with `start_line` and before/after. Currently lost in opaque observation content. |
| **WebFetch** | `duration_ms` (in output) | Network latency per fetch. Useful for analytics persona. |
| **WebSearch** | `links[]` (in output) | Structured search results with titles and URLs. Currently flattened to text. |
| **Task** | `subagent_type`, `model`, `run_in_background` | Agent orchestration decisions. `model` captures sonnet/opus/haiku routing. |
| **ExitPlanMode** | `launchSwarm`, `teammateCount` | Multi-agent coordination signals. |

**Priority**: HIGH for Bash `description` (training value), Edit `diffs[]` (viewer diff rendering). MEDIUM for the rest.

### 2.4 User Message Sub-Types

claude-code-log identifies 8 distinct user message sub-types that opentraces treats as undifferentiated `role="user"` steps:

1. **Slash commands** (`<command-name>` XML tags) - user invoked a skill
2. **Local command output** (`<local-command-stdout>`) - user ran `! command`
3. **Bash input/output** (`<bash-input>`, `<bash-stdout>`, `<bash-stderr>`) - terminal interaction
4. **Compacted summaries** ("This session is being continued...") - context window management
5. **User memory** (`<user-memory-input>`) - CLAUDE.md / memory injection
6. **IDE notifications** (`<ide_opened_file>`, `<ide_selection>`, `<ide_diagnostics>`) - VS Code/JetBrains context
7. **Queue steering** (remove operation) - user cancelled an action
8. **Regular text** - actual user prompt

**Recommendation**: Add a `user_message_type` field to `Step` or use `call_type` variants. At minimum, detect compaction summaries (training persona needs to know when context was compressed) and slash commands (affects task description extraction).

### 2.5 Content Format Duality

simonw documents that Claude Code v2.0.76+ changed `message.content` from a string to an array of typed blocks. opentraces' parser handles the array format but should add a fallback for the old string format for historical sessions:

```python
# Old: {"content": "Hello"}
# New: {"content": [{"type": "text", "text": "Hello"}]}
```

opentraces' parser at `claude_code.py:330` iterates `msg_content` as a list. If `msg_content` is a string, this would fail silently or crash.

**Priority**: MEDIUM (only affects pre-2.0.76 sessions, but matters for importing historical data).

### 2.6 Sub-Agent File Resolution

claude-code-log supports both the legacy flat layout (`agent-{id}.jsonl` in session dir) and the new nested layout (`session/subagents/agent-{id}.jsonl`). opentraces' `_load_subagent()` at line 491 looks for `<session-dir>/subagents/*.meta.json`, which is the newer pattern. It should also check the legacy flat pattern for compatibility.

**Priority**: LOW (legacy layout is rare in 2026).

### 2.7 Sidechain Deduplication

claude-code-log removes duplicate messages at sidechain boundaries: the first user message in a sidechain (which duplicates the Task input) and the last assistant message (if it matches Task output) are automatically stripped. This is the same duplication that would appear in opentraces' inlined sub-agent steps.

**Priority**: MEDIUM (affects training data quality, duplicated content inflates datasets).

### 2.8 Image Content Handling

opentraces' parser at line 358 silently ignores `"image"` blocks. claude-code-log:
- Validates base64 encoding
- Allowlists media types (png, jpeg, gif, webp, excludes SVG for XSS)
- Supports three export modes (embedded, referenced file, placeholder)

For a HuggingFace dataset, images should at minimum be counted (for metrics) and optionally preserved (for multimodal training).

**Priority**: LOW for v0.1 (text-first), MEDIUM for future multimodal support.

---

## 3. Cost & Analytics Improvements from ccusage

ccusage (12K stars) is the de facto standard for Claude Code usage analytics. Key improvements for opentraces' `_compute_metrics()`:

### 3.1 Use Pre-Calculated `costUSD`

Newer Claude Code versions write a `costUSD` field per API response in the JSONL. opentraces' `estimated_cost_usd` in `_compute_metrics()` should prefer this field when available, falling back to calculation only when absent.

### 3.2 Tiered Pricing for 1M Context

ccusage implements tiered pricing where tokens above 200K are charged at a higher rate:
```
cost = (min(tokens, 200K) * basePrice) + (max(0, tokens - 200K) * tieredPrice)
```
opentraces' flat-rate calculation underestimates cost for long sessions.

### 3.3 Fast Mode Multiplier

When `speed === 'fast'`, pricing multiplies by the provider's fast-mode factor (e.g., 6x for Opus). opentraces does not capture the `speed` field at all.

### 3.4 LiteLLM Pricing Database

ccusage fetches pricing from LiteLLM's maintained model pricing database rather than hardcoding rates. This stays current as Anthropic adjusts pricing.

### 3.5 Thinking Token Accounting

Neither ccusage nor opentraces separately track thinking tokens. Both count them within `output_tokens`. This is a shared gap. For the analytics and RL personas, a `thinking_tokens` field (even if estimated by subtracting visible output from total output) would be valuable.

---

## 4. Viewer Improvements

Comparing opentraces' viewer against community rendering approaches reveals these gaps:

### 4.1 Diff Rendering (HIGH PRIORITY)

**Gap**: `Outcome.patch` and `VCS.diff` are loaded but never rendered. Edit tool results with `diffs[]` are shown as raw JSON.

**What community does**: claude-code-log renders inline diffs with red/green backgrounds and character-level diff within lines. simonw renders Edit tool calls with `-`/`+` styled sections.

**Recommendation**: Add a `DiffViewer` component that renders:
- Edit tool call inputs as side-by-side old/new with syntax highlighting
- `Outcome.patch` as a unified diff view in the detail panel
- `VCS.diff` as a baseline comparison view

### 4.2 Snippet Display (HIGH PRIORITY)

**Gap**: `Step.snippets[]` (file_path, start_line, end_line, language, text) are parsed and stored but never rendered in `StepDetail`.

**Recommendation**: Add a `SnippetList` component in `StepDetail` showing syntax-highlighted code blocks with file path headers and line numbers.

### 4.3 Metrics Dashboard (HIGH PRIORITY)

**Gap**: `TraceRecord.metrics` (total_tokens, cost, duration, cache_hit_rate) exists but is never displayed. `formatCost()` is defined in `format.ts` but never called.

**Recommendation**: Add a `MetricsSummary` bar (either in `Header` or as a panel) showing: total tokens, estimated cost, duration, cache hit rate, step count, tool call breakdown by type.

### 4.4 System Prompt Inspection (MEDIUM)

**Gap**: `TraceRecord.system_prompts` (SHA-256-keyed deduped prompts) is loaded but never shown.

**Recommendation**: Add a "System Prompts" tab or expandable section. Training and domain personas care about this. Show the SHA key, character count, and expandable content.

### 4.5 Tool Input Formatting (MEDIUM)

**Gap**: Tool call inputs are shown as collapsible raw JSON in `ToolCallDetail`.

**What community does**: All three web renderers format tool inputs by type: Bash shows `$ command`, Edit shows file path + old/new blocks, Read shows file path + range.

**Recommendation**: Add tool-specific renderers in `ToolCallDetail` for at least Bash, Edit, Read, Write, and Grep. Use the patterns from claude-JSONL-browser's `formatToolInput()` as reference.

### 4.6 ANSI Color Support in Bash Output (MEDIUM)

**Gap**: Bash tool results show raw ANSI escape sequences.

**What community does**: claude-code-log has a full ANSI-to-HTML converter handling standard colors, bright colors, RGB, bold/dim/italic/underline.

**Recommendation**: Add ANSI-to-HTML conversion for Bash observation content. Libraries like `ansi-to-html` exist for TypeScript.

### 4.7 Context Compaction Visualization (LOW)

**Gap**: No indication of when context window compaction occurred during a session.

**What community does**: simonw collapses `isCompactSummary` messages in `<details>` elements. claude-code-log detects "continued from previous conversation" messages.

**Recommendation**: If the parser captures `isCompactSummary`, the viewer should render these steps distinctly (e.g., dashed border, "context compacted" label) to show where Claude lost context.

### 4.8 Search Functionality (LOW)

**Gap**: The search tab in `TracePanel` is a stub showing only the text "search".

**What community does**: simonw has full-text search with a modal and keyboard shortcut. claude-JSONL-browser has cross-file search with match counts. claude-code-log has regex search with match navigation.

**Recommendation**: Implement at minimum a text search that filters the trace tree to matching steps/tool calls.

### 4.9 Timeline Fix (BUG)

**Gap**: `TimelineStrip` never renders due to prop name mismatch (`App.tsx` passes `timelineStrip` but `AppLayout` expects `contextPanel`).

**Fix**: Rename the prop in `App.tsx` from `timelineStrip` to `contextPanel`, or update `AppLayout` to accept both.

---

## 5. Viewer Bugs Found During Research

These are pre-existing bugs in the viewer unrelated to community projects, but surfaced during the architectural review:

1. **TimelineStrip prop mismatch** (App.tsx:39 vs AppLayout.tsx:14) - strip never renders
2. **`p` key undocumented** - shown in KeyboardHelp but no handler in useKeyboardNav
3. **`r` key undocumented** - same issue
4. **RedactionPreview orphaned** - component built but never rendered from DetailPanel
5. **Panel focus has no visual indicator** - focusedPanel state drives nav but no highlight
6. **Virtualized tree does not scroll to selected** - j/k nav can move selection off-screen
7. **Header push button has no onClick** - enabled when committed sessions exist but does nothing
8. **Onboarding sample mode is hollow** - setSampleMode(true) but no sample data loaded
9. **`approveSession` mutation has no UI binding** - returned from useReviewActions but no button

---

## 6. Priority Matrix

### Parsing (src/opentraces/parsers/claude_code.py)

| # | Improvement | Source | Effort | Impact | Priority |
|---|-----------|--------|--------|--------|----------|
| P1 | Capture `costUSD` per API response | ccusage | Quick | High (accurate cost) | **P0** |
| P2 | Capture `isCompactSummary` flag | simonw | Quick | High (training quality) | **P0** |
| P3 | Capture Bash `description` field | claude-code-log | Quick | High (training value) | **P0** |
| P4 | Capture `speed` (fast mode) flag | ccusage | Quick | Medium (pricing) | **P1** |
| P5 | Detect user message sub-types | claude-code-log | Medium | Medium (training) | **P1** |
| P6 | Parse Edit output `diffs[]` | claude-code-log | Medium | Medium (viewer) | **P1** |
| P7 | Capture `summary` line type | simonw | Quick | Medium (task desc) | **P1** |
| P8 | Handle string content format | simonw | Quick | Low (historical) | **P2** |
| P9 | Sidechain dedup for sub-agents | claude-code-log | Medium | Low (data quality) | **P2** |
| P10 | Image block counting/preservation | claude-code-log | Medium | Low (future) | **P2** |
| P11 | Tiered pricing calculation | ccusage | Medium | Medium (accuracy) | **P1** |
| P12 | LiteLLM pricing database | ccusage | Medium | Medium (maintainability) | **P2** |

### Viewer (viewer/src/)

| # | Improvement | Source | Effort | Impact | Priority |
|---|-----------|--------|--------|--------|----------|
| V1 | Diff rendering for Edit + patch | claude-code-log, simonw | Medium | High | **P0** |
| V2 | Snippet display in StepDetail | (internal gap) | Quick | High | **P0** |
| V3 | Metrics dashboard | ccusage patterns | Quick | High | **P0** |
| V4 | Fix TimelineStrip prop mismatch | (bug) | Quick | Medium | **P0** |
| V5 | Tool-specific input formatting | claude-JSONL-browser | Medium | Medium | **P1** |
| V6 | System prompt viewer | (internal gap) | Quick | Medium | **P1** |
| V7 | ANSI color in Bash output | claude-code-log | Quick | Medium | **P1** |
| V8 | Context compaction visualization | simonw | Quick | Low | **P2** |
| V9 | Search in trace tree | all community tools | Medium | Low | **P2** |

---

## 7. Key Takeaways

1. **claude-code-log is the gold standard for parsing depth.** Its 16 typed tool models, 8 user message sub-types, sidechain deduplication, and IDE notification parsing represent the most thorough understanding of Claude Code's JSONL format. opentraces should treat it as a reference implementation.

2. **ccusage owns the cost/analytics space.** Its tiered pricing, LiteLLM integration, and fast-mode handling are battle-tested at 12K stars. opentraces should adopt its cost calculation approach rather than reinventing.

3. **simonw captures session-level metadata others miss.** `isCompactSummary` and `isMeta` flags are unique to his parser and critical for training data quality, since they tell you when Claude's context was compressed.

4. **The viewer's biggest gap is rendering what the parser already captures.** Diffs, snippets, metrics, system prompts, and attribution are all parsed and stored in `TraceRecord` but never displayed. This is low-hanging fruit.

5. **No community project handles thinking blocks well.** All tools hit the same wall: Claude 4 JSONL files have empty thinking fields. opentraces' redacted marker approach (`[redacted: model produced reasoning but content was withheld by provider]`) is the most honest handling among all projects surveyed.

6. **claude-JSONL-browser is the weakest reference.** It is a markdown converter, not a browser. Its only useful contribution is the `formatToolInput()` function with ~45 file extension mappings for syntax highlighting.

---

## Sources

- [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) - Apache-2.0, v0.6
- [daaain/claude-code-log](https://github.com/daaain/claude-code-log) - MIT, v1.1.1
- [withLinda/claude-JSONL-browser](https://github.com/withLinda/claude-JSONL-browser) - MIT
- [ryoppippi/ccusage](https://github.com/ryoppippi/ccusage) - MIT, 12K stars
- [Simon Willison blog: "A new way to extract detailed transcripts"](https://simonwillison.net/2025/Dec/25/claude-code-transcripts/)
- [DeepWiki: claude-code-log documentation](https://deepwiki.com/daaain/claude-code-log)
