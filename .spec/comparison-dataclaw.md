---
schema_version: "1.0"
title: System Comparison — opentraces vs dataclaw
scope: full-system
date: 2026-03-28
---

# System Comparison: opentraces vs dataclaw

Both projects solve the same problem: export AI coding agent conversations to HuggingFace Hub as structured datasets. They share the same author, overlapping code lineage, and nearly identical security pipelines. This document maps where they align, where they diverge, and what each one does that the other doesn't.

---

## At a Glance

| Dimension | **dataclaw** | **opentraces** |
|-----------|-------------|----------------|
| Tagline | "Performance art" counter to Anthropic data policies | Open protocol for crowdsourcing agent traces |
| Version | 0.3.2 | 0.1.0 |
| License | MIT | Apache-2.0 |
| Primary language | Python | Python + TypeScript |
| LOC (source) | 4,111 (6 files) | ~23,285 (5 packages) |
| LOC (with tests) | 7,470 | ~28,947 |
| Agent sources | 7 (Claude, Codex, Gemini, OpenCode, OpenClaw, Kimi, Custom) | 1 (Claude Code) + import bridge |
| Schema | Flat session-level JSONL | Pydantic v2 schema package (TraceRecord with nested Steps/ToolCalls) |
| Security tiers | 1 (always-on + confirm gate) | 3 (open / guarded / strict) |
| Quality evaluation | None | 5-persona rubric engine + optional LLM judge |
| Review interfaces | CLI confirm attestation | CLI + TUI (Textual) + Web (Flask + React viewer) |
| Enrichment | Anonymization + secret redaction only | Git signals, attribution, dependencies, metrics, snippets |
| Upload model | Single `conversations.jsonl` overwrite | Sharded JSONL (append-only, never overwrite) |
| External deps | 1 (`huggingface_hub`) | ~15 (click, flask, pydantic, textual, etc.) |
| Architecture | Single flat package | Monorepo with standalone schema package |

---

## Pipeline Comparison

### dataclaw pipeline

```
discover_projects() — scans 7 agent storage locations
      |
parse_project_sessions() — source-specific parser per agent
      |
anonymizer.path() / anonymizer.text() — username hashing, home dir stripping
      |
redact_session() — 20 regex patterns + custom strings
      |
conversations.jsonl written to disk
      |
[GATE] dataclaw confirm — 3 text attestations, PII scan report
      |
push_to_huggingface() — single file upload, overwrites previous
```

### opentraces pipeline

```
discover_sessions() — Protocol-based, currently Claude Code only
      |
parse_session() — structured TraceRecord with Steps/ToolCalls/Observations
      |
two_pass_scan() — context-aware per-field + serialized bytes catch-all
      |
anonymize_paths() — username SHA-256 hashing
      |
classify_trace_record() — heuristic flagging (tier 2+)
      |
enrich: git_signals -> attribution -> dependencies -> metrics -> snippets
      |
stage to JSONL, StateManager tracks lifecycle
      |
[GATE] review (CLI / TUI / Web) — per-trace approve/reject/redact
      |
commit — bundle approved traces into named group
      |
assess (optional) — 5-persona quality rubric + LLM judge
      |
push — sharded JSONL upload, exponential backoff, dataset card
```

**Key difference**: dataclaw is a single-pass export with a gate at the end. opentraces is a multi-stage pipeline with state management, incremental processing, and quality assessment as a parallel track.

---

## Schema Depth

### dataclaw session object

Flat structure, one object per session:

```
session_id, project, source, model, git_branch,
start_time, end_time,
messages: [{role, content, thinking?, tool_uses?, timestamp}],
stats: {user_messages, assistant_messages, tool_uses, input_tokens, output_tokens}
```

- Messages are conversational turns (user/assistant alternation)
- Tool uses are nested inside assistant messages
- No sub-agent representation beyond inlining
- `thinking` field is optional (suppressible with `--no-thinking`)

### opentraces TraceRecord

Deeply nested, one object per session with rich metadata:

```
trace_id, session_id,
task: {description, source, repository, base_commit},
agent: {name, version, model},
environment: {os, shell, vcs: {type, branch, base_commit, diff}, language_ecosystem},
system_prompts: {hash -> text},  # deduplicated
steps: [{
    step_number, role, content,
    tool_calls: [{name, input, result, duration}],
    observations: [{type, content}],
    snippets: [{language, content, source_file}],
    token_usage: {input, output, cache_read, cache_creation},
    system_prompt_hash
}],
outcome: {success, signal_confidence, patch, committed, commit_sha},
attribution: {files: [{path, line_ranges, confidence}]},
metrics: {total_input_tokens, total_output_tokens, duration, cache_hit_rate, estimated_cost},
security: {tier, flags_reviewed, redactions_applied},
dependencies: [str],
content_hash
```

- Steps map to individual LLM API calls, not conversational turns
- System prompts deduplicated by hash (large prompts stored once)
- `signal_confidence` distinguishes derived/inferred/annotated outcome signals
- `content_hash` enables deduplication across uploads
- Attribution links code changes to specific conversation steps

**Implication**: dataclaw preserves the raw conversation. opentraces restructures it into a training-oriented schema with enriched metadata. dataclaw is closer to "export what happened"; opentraces is closer to "curate a dataset."

---

## Source Support

| Source | dataclaw | opentraces |
|--------|----------|------------|
| Claude Code | Yes (JSONL parser) | Yes (761 LOC parser with subagent inlining) |
| Codex | Yes (JSONL + archive) | No |
| Gemini CLI | Yes (JSON, SHA-256 path resolution) | No |
| OpenCode | Yes (SQLite3 database) | No |
| OpenClaw | Yes (JSONL with header) | No |
| Kimi CLI | Yes (JSONL, MD5 path hash) | No |
| Custom JSONL | Yes (user-provided) | No (but FormatImporter protocol exists) |

dataclaw's parser advantage is breadth: 7 sources, each with storage-format-specific parsing. opentraces has depth: one source but with subagent recursion (depth limit 10), circular reference detection, quality gates (min 2 steps + 1 tool call), and incremental byte-offset resume.

---

## Security Comparison

### Shared DNA

Both systems share nearly identical approaches:

| Feature | dataclaw | opentraces |
|---------|----------|------------|
| Username hashing | SHA-256, first 8 chars | SHA-256, first 8 chars |
| Home dir stripping | Yes | Yes |
| Regex secret patterns | 20 patterns | 19+ patterns (similar set) |
| JWT detection | Yes | Yes |
| Provider API keys | Anthropic, OpenAI, HF, GitHub, PyPI, NPM, AWS, Slack | Same set + more |
| Shannon entropy | >= 3.5 threshold | >= 4.5 threshold |
| Allowlists | Yes (example.com, private IPs, decorators) | Yes (similar set) |
| Email detection | Yes | Yes |
| Credit card (Luhn) | No | Yes |
| SSN / Phone | No | Yes |

### Where they diverge

**dataclaw**: Single-pass scanning. All fields treated the same. Security is a single layer applied uniformly. The real gate is the `confirm` command with 3 mandatory text attestations (minimum length, content checks for keywords like "manual scan" and a number >= 20).

**opentraces**: Two-pass, context-aware scanning. Fields classified by type (TOOL_INPUT, TOOL_RESULT, REASONING, GENERAL) with different rules per type. Entropy scanning disabled for TOOL_RESULT to reduce false positives. Second pass scans serialized bytes to catch anything introduced during enrichment. Three configurable tiers. Heuristic classifier adds a flag-based layer for internal hostnames, AWS account IDs, DB connection strings.

**Key insight**: dataclaw's security model trusts the human gate (attestations enforce that the user actually reviewed). opentraces' security model trusts the machine (multi-pass scanning reduces what the human needs to catch).

---

## Upload Model

| Aspect | dataclaw | opentraces |
|--------|----------|------------|
| File strategy | Single `conversations.jsonl`, overwritten each push | Sharded `traces_{timestamp}_{uuid}.jsonl`, append-only |
| Additional files | `metadata.json`, `README.md` | Dataset card (preserves user-edited sections) |
| Retry | None | Exponential backoff (3 retries, 1s/2s/4s) |
| Concurrent safety | None (single file overwrite) | Sharded files + StagingLock (fcntl.flock) |
| Incremental | No (full re-export each time) | Yes (byte offset resume, inode/mtime tracking) |
| Repo naming | `{username}/my-personal-codex-data` | `{username}/opentraces` or configured remote |
| Discovery tag | `dataclaw` | `opentraces` + `agent-traces` |

dataclaw's model is simpler: export everything, push the whole file. opentraces' model supports incremental workflows where traces accumulate over time and can be pushed from different machines without conflicts.

---

## Quality & Review

### dataclaw

No quality evaluation system. The review mechanism is the `confirm` command:
- Scans for PII and high-entropy strings in the export file
- Requires 3 text attestations with content validation (must mention specific actions taken)
- Stage-gated: pushing is blocked until confirm completes
- Binary: the whole export is confirmed or not

### opentraces

Multi-layered quality system:

1. **Per-trace review** via 3 interfaces (CLI, TUI, Web viewer with React)
2. **Per-trace actions**: approve, reject, skip, per-step redact
3. **5-persona quality rubric**: conformance (27 checks), training (10), RL (8), analytics (8), domain (8)
4. **Optional LLM judge**: blends 60% deterministic / 40% judge scores
5. **Schema completeness audit**: field population rates, gap classification (parser_bug / enrichment_gap / not_yet_implemented)
6. **Commit groups**: approved traces bundled into named commits before upload

---

## CLI Design Philosophy

Both CLIs are "agent-native", designed for both human and AI agent consumption:

| Feature | dataclaw | opentraces |
|---------|----------|------------|
| JSON sentinel | `---DATACLAW_JSON---` (implied from status output) | `---OPENTRACES_JSON---` |
| Stage workflow | 4 stages: auth -> configure -> review -> confirmed | 7 statuses: discovered -> parsed -> staged -> approved -> committed -> uploading -> uploaded |
| Discoverability | `dataclaw status` shows next steps | `capabilities` and `introspect` commands |
| Git analogy | No | Yes (init/status/review/commit/push mirrors git) |
| Hook integration | No | `SessionEnd` hook in `.claude/settings.json` |
| Default command | `dataclaw` runs export | No default (must specify subcommand) |

dataclaw's CLI is optimized for a single export-and-push workflow. opentraces' CLI mirrors git's staging model for ongoing, incremental trace curation.

---

## Complexity Budget: Where the 10x Actually Lives

At first glance the LOC ratio looks like ~7:1 (dataclaw 4,111 src vs opentraces 28,947 total). But the raw total is misleading. Here's a precise audit of where every line goes.

### Actual LOC (source only, excluding tests)

| Component | dataclaw | opentraces |
|-----------|----------|------------|
| CLI commands | 1,638 | 1,786 |
| Parsing | 2,038 (7 sources) | 915 (1 source) |
| Security (scanning + anonymization) | 522 | 1,372 |
| Config | 54 | 326 |
| Upload | 170 | 381 |
| State management | 0 (stage field in config) | 239 |
| Enrichment pipeline | 0 | 1,301 |
| Quality engine | 0 | 4,117 |
| Review interfaces (CLI/TUI) | 0 (confirm is in cli.py) | 823 |
| Review web (Flask app) | 0 | 911 + 1,780 web assets |
| Exporters | 0 | 242 |
| Schema package | 0 (inline dict) | 288 |
| React viewer | 0 | 5,137 |
| Marketing site | 0 | 3,565 |
| Misc (workflow, init) | 0 | 102 |
| **Source subtotal** | **4,111** | **~23,285** |
| Tests | 3,359 | 7,438 |
| **Grand total** | **7,470** | **~28,947** |

### The three buckets

**Bucket 1 — Features that don't exist in dataclaw (~16,400 LOC, 66% of the gap)**

| Feature | LOC | What it does |
|---------|-----|-------------|
| Quality engine | 4,117 | 5-persona rubric, 60+ checks, LLM judge, schema audit |
| React viewer | 5,137 | Trace visualization SPA with virtualized trees |
| Marketing site | 3,565 | Next.js landing page, docs, schema explorer |
| Enrichment pipeline | 1,301 | Git signals, attribution, dependencies, metrics, snippets |
| Review interfaces | 1,734 | TUI (Textual) + Flask web review |
| Web review assets | 1,780 | HTML templates, JS, CSS for Flask app |
| State management | 239 | Lifecycle tracking, byte-offset resume, file locks |
| Exporters | 242 | ATIF format export |
| Schema package | 288 | Standalone Pydantic models |

These are entire subsystems opentraces has that dataclaw simply doesn't. The quality engine alone (4,117 LOC) is larger than dataclaw's entire source code.

**Bucket 2 — Same features, deeper implementation (~1,600 LOC, 6% of the gap)**

| Feature | dataclaw | opentraces | Delta | What the extra buys |
|---------|----------|------------|-------|---------------------|
| Security | 522 | 1,372 | +850 | Context-aware field typing, two-pass scan, heuristic classifier, 3 tiers |
| Config | 54 | 326 | +272 | Per-project overrides, migration, atomic writes, tier config |
| Upload | 170 | 381 | +211 | Sharding, exponential retry, concurrent locks, card preservation |
| CLI | 1,638 | 1,786 | +148 | Git-analogy commands, hook integration, capabilities/introspect |

**Bucket 3 — Tests proportional to source (~4,079 extra LOC, 16% of the gap)**

Both projects maintain a similar test-to-source ratio (~0.8x), so the test gap is proportional to the source gap.

**Bucket 4 — dataclaw is actually bigger (+1,123 LOC)**

dataclaw's parser.py (2,038 LOC) is **2.2x larger** than opentraces' entire parsers/ directory (915 LOC). This is the one area where dataclaw invests more, supporting 7 agent formats vs 1. The Codex event-stream state machine alone is 320 LOC, and Gemini's SHA-256 path resolution adds another 200+.

### The honest ratio

Strip away everything that only exists in one project (viewer, mktg, quality engine, enrichment, extra parsers) and compare just the overlapping CLI capabilities:

| | dataclaw | opentraces |
|---|---|---|
| Parse + scan + upload + config + CLI | ~4,100 | ~5,400 |
| **Ratio** | | **1.3x** |

For equivalent features, opentraces is only **1.3x** larger, not 10x. Each of those extra lines buys something concrete (context-aware scanning, sharded uploads, incremental resume, atomic config writes).

The apparent 10x is really:
- **3 entire applications** that dataclaw doesn't have (viewer, mktg, Flask review)
- **2 subsystems** that dataclaw doesn't have (quality engine, enrichment)
- **Tests** for all of the above
- A modest deepening of shared features

### Per-module detail

**dataclaw cli.py (1,638 LOC):**
- Export flow + guards: ~360 LOC
- Confirm gate + attestation validators: ~305 LOC
- Status/prep/config: ~230 LOC
- Dataset card template: ~120 LOC
- PII/entropy scanners: ~145 LOC
- Argparse setup: ~113 LOC
- Helpers/formatting: ~365 LOC

**dataclaw parser.py (2,038 LOC):**
- Codex (event-stream state machine): ~320 LOC
- Gemini (JSON, SHA-256 path resolution): ~204 LOC
- OpenClaw: ~206 LOC
- Kimi: ~95 LOC
- OpenCode (SQLite): ~92 LOC
- Claude (main + subagent): ~117 LOC
- Custom (passthrough): ~49 LOC
- Discovery + indexing: ~506 LOC
- Shared helpers: ~449 LOC

**opentraces quality/ (4,117 LOC) — the single biggest subsystem:**
- engine.py: 862 (orchestration, scoring, report generation)
- schema_audit.py: 589 (field population tracking, gap classification)
- judge.py: 546 (LLM evaluation, hybrid blending)
- conformance.py: 457 (27 structural checks)
- personas/training.py: 315 (SFT fitness checks)
- preservation.py: 276 (raw-vs-parsed signal loss detection)
- personas/domain.py: 247
- personas/rl.py: 214
- raw_reader.py: 189
- personas/analytics.py: 160
- gates.py + types.py + inits: 262

---

## Architecture Complexity

### dataclaw: Monolith

6 files, 4,111 LOC source. Everything in one flat package. No internal package boundaries, no protocol abstractions, no state machine. The entire pipeline runs in a single function call chain within `export()`.

**Strengths**: Easy to understand, easy to contribute, minimal dependencies (1 external dep), fast to install.
**Tradeoffs**: No incremental processing, no quality evaluation, limited extensibility, single-file upload limits dataset size.

### opentraces: Modular pipeline

5 packages, ~23,285 LOC source. Standalone schema package, protocol-based adapters, stateful pipeline with lifecycle tracking, parallel quality assessment, 3 review interfaces.

**Strengths**: Incremental processing, rich metadata, quality evaluation, multiple review modes, concurrent-safe uploads, extensible via protocols.
**Tradeoffs**: Higher complexity, ~15 dependencies, harder to onboard, requires understanding the pipeline model.

---

## Relationship Between the Two

dataclaw appears to be the **v1 prototype**: ship fast, support many sources, prove the concept. opentraces is the **v2 redesign**: one source done deeply with a schema-first approach, quality evaluation, and a git-like workflow model.

Evidence of lineage:
- Same author
- Same HuggingFace Hub upload pattern
- Nearly identical secret detection regex sets
- Same username hashing approach (SHA-256, 8 chars)
- Same anonymization strategy (home dir stripping, path replacement)
- opentraces' `dataclaw_import.py` in parsers suggests a migration path

The projects represent different points on the breadth-vs-depth spectrum:

```
dataclaw                                    opentraces
  |                                              |
  7 sources, flat schema,                   1 source, rich schema,
  simple pipeline,                          multi-stage pipeline,
  human attestation gate                    machine scanning + human review,
  4,111 LOC (6 files)                      23,285 LOC (5 packages)
  |                                              |
  BREADTH ◄──────────────────────────────► DEPTH
```

For overlapping capabilities, the ratio is 1.3x. The rest is entirely new surface area.

---

## What Each Has That the Other Doesn't

### dataclaw has, opentraces doesn't:
- 6 additional agent source parsers (Codex, Gemini, OpenCode, OpenClaw, Kimi, Custom)
- Text attestation-based confirm gate (forces human acknowledgment of specific review actions)
- `--no-thinking` flag to suppress reasoning traces
- Skill update mechanism (`dataclaw update-skill claude`)
- Project exclusion by display name
- Gemini SHA-256 path resolution and OpenCode SQLite parsing

### opentraces has, dataclaw doesn't:
- Standalone schema package (reusable across tools)
- Protocol-based adapter contracts (structural typing)
- Incremental processing with byte-offset resume
- Three-tier configurable security model
- Context-aware, two-pass security scanning
- Git signal enrichment (branch, base_commit, commit detection)
- Code attribution (per-file line ranges linked to conversation steps)
- Dependency extraction from imports and tool calls
- Token/cost metrics with configurable pricing
- 5-persona quality rubric engine with 60+ checks
- Optional LLM judge with hybrid scoring
- Schema completeness audit
- 3 review interfaces (CLI, TUI, Web with React viewer)
- Commit groups (git-like staging model)
- Sharded, append-only uploads
- Content hash deduplication
- File-based locking for concurrent safety
- `SessionEnd` hook for automatic capture
- System prompt deduplication by hash
- Dataset card generation with section preservation
- Structured exit codes
- Machine-discoverable capabilities
