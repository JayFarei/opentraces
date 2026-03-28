---
title: "feat: Multi-Persona Trace Quality Assessment Harness"
type: feat
status: active
date: 2026-03-28
---

# Multi-Persona Trace Quality Assessment Harness

## Overview

Add a reusable quality assessment framework with three layers:
1. **Schema completeness audit** -- systematically checks every field in TraceRecord, flags empty/out-of-range values, and classifies each gap as parser bug, schema design issue, or expected session variance
2. **Persona rubrics** -- evaluates traces through four downstream consumer lenses (Training/SFT, RL/RLHF, Analytics, Domain Sourcing)
3. **Preservation comparator** -- compares raw Claude Code sessions against parsed output to detect signal loss and impossible objectives

## Problem Frame

The existing dogfood rubric (`test_e2e_dogfood.py`) answers "is this trace well-formed?" with 28 structural conformance checks. It does not answer:
- **Is this trace useful for training?** ADP's empirical results (Song et al. 2026) show unified trajectory SFT yields ~20% improvement on SWE-Bench at 7B scale. But ADP explicitly lacks security, attribution, hierarchy, outcome signals, and token usage. Our schema fills those gaps, but only if the parsed output actually preserves the signals training pipelines need.
- **Is this trace useful for RL?** Outcome signals (committed, patch, signal_confidence) are the reward proxy. ADP has zero outcome/reward signals, making their trajectories "uniformly valid demonstrations." Our differentiation depends on these fields being reliably populated.
- **Did we lose valuable signals during parsing?** Content truncation (10K observations, 5K snippets), sub-agent inlining, thinking block encryption, tool result correlation. Each transformation is a potential signal loss point.
- **Are we claiming signals we can't deliver?** Schema fields that the raw trace can't populate are empty promises to consumers.

These questions matter because the project's value proposition to HF and the ML community depends on downstream utility, not just structural validity. The persona rubrics make the quality bar concrete and measurable per consumer type.

## Requirements Trace

- R1. Four persona-specific rubrics scoring traces for downstream utility (training, RL, analytics, domain sourcing)
- R2. Raw-vs-parsed comparison detecting signal loss and impossible objectives
- R3. Reusable module under `src/opentraces/quality/` (not buried in tests)
- R4. Works against real sessions from `~/.claude/projects/`
- R5. Generates a human-readable markdown report with per-persona breakdowns
- R6. Existing structural rubric preserved as the "conformance" persona (backward compatible)
- R7. Persona-appropriate thresholds (not every session scores high on every persona, and that's fine)
- R8. Schema completeness audit: every field in TraceRecord checked for population, empty/out-of-range fields classified as parser bug / schema design issue / expected variance
- R9. Gap classification report explaining WHY each empty field is empty, enabling schema evolution decisions

## Scope Boundaries

- No LLM-based assessment (all checks are deterministic, matching the project's zero-LLM enrichment principle)
- No new CLI subcommand in this iteration (the harness runs via pytest)
- No YAML/config-driven persona definitions (code-defined check functions, extensible by adding new functions)
- No changes to the parser or enrichment pipeline (read-only assessment of their output)
- No ADP export validation (that's a separate feature, `opentraces export --format adp`)

## Context & Research

### What Downstream Consumers Actually Need (Research-Grounded)

**Training/SFT consumers** (grounded in ADP paper findings):
- ADP's core structure is alternating action/observation sequences. 53% APIAction, 24% CodeAction, 23% MessageAction across 1.3M trajectories. Our tool_call/observation pairing must be tight for conversion to this format.
- ADP's quality gate requires >=80% of tool calls paired with reasoning text. This is a concrete threshold we should measure.
- System prompt deduplication matters: multi-agent sessions repeat 20K+ system prompts. ADP doesn't capture system prompts at all, so our dedup is a storage advantage only if it works reliably.
- Cross-task transfer is proven: mixed ADP training outperforms single-domain tuning. Implies our `language_ecosystem` and `dependencies` fields enable meaningful dataset filtering for training mixes.

**RL/RLHF consumers** (grounded in schema rationale):
- ADP has zero outcome/reward signals. NVIDIA's Nemotron dataset has outcome labels but only for synthetic SWE-bench runs. Our `outcome.committed` + `outcome.patch` provide real-world reward proxies that don't exist anywhere else.
- `signal_confidence: "derived"` (behavioral proxy from git state) vs `"annotated"` (human-supplied). RL consumers need to know the confidence level to weight samples.
- Per-step token_usage enables cost-penalized reward functions (cost per successful trace as a training signal).
- Sub-agent hierarchy matters: Claude Code's multi-agent architecture (warm-up -> explore -> plan -> execute) is invisible in flat trajectory formats. ADP explicitly lacks hierarchy.

**Analytics/Observability consumers** (grounded in traces.com competitive analysis):
- traces.com provides only trace-level aggregates (messageTypeCounts, toolTypeCounts). No per-message timestamps, token counts, or cost data. Our per-step token_usage + cache_hit_rate + estimated_cost_usd is the differentiation.
- Kobe Chen's research showed 81% cost savings from cache analysis. `cache_hit_rate` is an "architectural fingerprint" that traces.com cannot provide.
- The Contributor Dashboard (Phase 7) surfaces these to contributors. Analytics persona validates the signals exist for the dashboard.

**Domain Sourcing consumers** (grounded in HF dataset discovery patterns):
- HF Space explorer supports queries like: "all traces where Claude Code debugged a Django migration, outcome: success, files_modified contains migrations/". This requires `language_ecosystem`, `dependencies`, `task.description`, and `outcome.success` to be reliably populated.
- traces.com has zero discoverability beyond title/agent/model. No search, tagging, filtering by language or framework. Our schema richness enables this, but only if the fields are actually filled.

### Key Schema Fields by Persona Value

| Field | Training | RL | Analytics | Domain | Why |
|-------|----------|-----|-----------|--------|-----|
| tool_call ↔ observation pairing | critical | - | - | - | ADP requires alternating action/observation |
| reasoning_content | critical | medium | - | - | ADP: 83.8% of actions include reasoning text |
| step role alternation | critical | - | - | - | SFT data must be clean user/agent turns |
| system_prompts dedup | high | - | - | - | Storage savings, training sample clarity |
| outcome.committed/patch | medium | critical | - | - | Only real-world reward proxy in any dataset |
| outcome.signal_confidence | - | critical | - | - | RL consumers need to weight samples |
| steps[].token_usage | - | high | critical | - | Cost-penalized rewards, analytics dashboard |
| metrics.cache_hit_rate | - | - | critical | - | Kobe Chen: "architectural fingerprint" |
| metrics.estimated_cost_usd | - | high | critical | - | Cost modeling, contributor dashboard |
| metrics.total_duration_s | - | - | high | - | Session length analytics |
| parent_step/call_type | medium | high | medium | - | ADP lacks hierarchy entirely |
| environment.language_ecosystem | - | - | - | critical | HF dataset filtering |
| dependencies | - | - | - | critical | Domain sourcing queries |
| task.description | medium | - | - | high | Training context, HF search |
| attribution | - | medium | - | high | Agent Trace spec bridge |
| snippets with language | medium | - | - | high | Code-specific training data |

### Relevant Code and Patterns

- `tests/test_e2e_dogfood.py:40-457` -- Existing `RubricItem`, `RubricReport`, `score_trace()` to promote
- `packages/opentraces-schema/src/opentraces_schema/models.py` -- All schema models the checks operate on
- `src/opentraces/parsers/claude_code.py` -- Parser whose output we assess; raw JSONL format is the "before"
- `src/opentraces/enrichment/` -- Enrichment modules whose outputs the personas evaluate
- `src/opentraces/security/scanner.py` -- Security scanning for the conformance persona

## Key Technical Decisions

- **Promote, don't duplicate:** Move `RubricItem`, `RubricReport`, and `score_trace()` from test file to `src/opentraces/quality/`. The dogfood test becomes a thin import. This enables reuse from CLI, review UI, or other test files.

- **Personas as flat check registries, not class hierarchies:** Each persona is a `PersonaDef` dataclass with a name, description, and list of `CheckDef` entries. Each `CheckDef` has a `check` callable `(TraceRecord, RawSessionSummary | None) -> CheckResult`. No inheritance, no abstract base classes. New personas = new list of checks.

- **Independent raw reader for before/after comparison:** The comparator reads raw JSONL with `json.loads` per line (no dependency on `ClaudeCodeParser`). This avoids the circular problem of using the parser to validate the parser. Builds a lightweight `RawSessionSummary` with counts of tool_use blocks, tool_result blocks, thinking blocks, usage entries, content chars, timestamps, and sub-agent references.

- **Information preservation as ratios:** For each signal category: `preservation = parsed_count / raw_count`. Categories: messages, tool_calls, tool_results, token_usage_entries, thinking_blocks, timestamps, content_chars. Overall preservation score is weighted average. The comparator also flags "impossible signals" (schema fields with no raw source) and "signal loss" (raw signals that vanished).

- **ADP 80% reasoning threshold as a concrete benchmark:** The training persona adopts ADP's quality gate (>=80% of tool calls paired with reasoning text) as a weighted check. This grounds the rubric in empirical training-pipeline requirements, not opinion.

- **Per-persona thresholds lower than structural:** Structural conformance stays at 70%/80%. Persona thresholds are intentionally lower because session characteristics naturally limit some personas (a session with no commits can't score well on RL, and that's expected). The harness reports which persona scores are N/A vs genuinely low.

- **Encrypted thinking is partial credit, not failure:** Opus 4.6 uses encrypted thinking blocks. The comparator detects thinking blocks in raw with empty/absent content in parsed, scoring as "present but encrypted" (0.5 instead of 0.0 or 1.0). The training persona notes this affects the ADP 80% reasoning threshold.

## Open Questions

### Resolved During Planning

- **Should personas be data or code?** Code (callable check functions). Data-driven configs add indirection without value at this scale. Four personas with ~8-12 checks each is manageable as functions.
- **Should this replace the existing dogfood rubric?** No. The structural rubric becomes the "conformance" persona. The four new personas are additive. `test_e2e_dogfood.py` continues to work, importing from the new location.
- **Where does the raw reader live?** In `src/opentraces/quality/raw_reader.py`. It's a quality assessment tool, not a parser.
- **Should we adopt ADP's quality thresholds directly?** Selectively. The 80% reasoning coverage threshold is adopted for the training persona. ADP's alternating action/observation requirement maps to our tool_call/observation pairing check.

### Deferred to Implementation

- Exact scoring curves for partial-credit items (e.g., snippet count thresholds)
- Whether the report should include per-step signal-loss detail or just per-trace summary
- Whether `RawSessionSummary` should cache parsed results for performance
- Whether content truncation (10K observation cap) should be measured as signal loss or accepted as intentional design

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

```
Raw Session (.jsonl)          Parsed TraceRecord (batch)
       |                              |
       v                              |
  RawSessionSummary                   |
  (independent json.loads)            |
       |                              |
       v                              v
  +---------------------------------------------+
  |              Assessment Engine               |
  |                                              |
  |  Layer 1: Schema Completeness Audit          |
  |    walk every field in TraceRecord           |
  |    compute population rates across batch     |
  |    classify gaps:                            |
  |      parser_bug | enrichment_gap |           |
  |      schema_unrealistic | session_dependent  |
  |                                              |
  |  Layer 2: Persona Rubrics                    |
  |    for persona in personas:                  |
  |      for check in persona.checks:            |
  |        result = check(record, raw)           |
  |                                              |
  |  Layer 3: Preservation Comparator            |
  |    preservation = compare(raw, record)       |
  +---------------------+------------------------+
                        |
                        v
                 BatchAssessment
                 +-- schema_audit:
                 |   environment.os: 0% -> enrichment_gap
                 |   task.source: 0% -> not_yet_implemented
                 |   outcome.committed: 30% -> session_dependent
                 +-- persona_scores:
                 |   conformance: 92%
                 |   training: 85% (ADP reasoning: 78%)
                 |   rl: 45% (no commit, expected)
                 |   analytics: 88%
                 |   domain: 72%
                 +-- preservation: 0.94
                 |   signal_losses: [2 tool_results dropped]
                 +-- recommendations:
                     "Add OS extraction to enrichment"
                     "Wire up task.source in parser"
```

## Implementation Units

- [ ] **Unit 1: Promote rubric types to `src/opentraces/quality/`**

  **Goal:** Move `RubricItem`, `RubricReport`, and `score_trace()` into a reusable module. Keep backward compatibility.

  **Requirements:** R3, R6

  **Dependencies:** None

  **Files:**
  - Create: `src/opentraces/quality/__init__.py`
  - Create: `src/opentraces/quality/types.py` (RubricItem, RubricReport, CheckResult, PersonaDef, CheckDef)
  - Create: `src/opentraces/quality/conformance.py` (existing score_trace checks, refactored as check functions)
  - Modify: `tests/test_e2e_dogfood.py` (import from new location, remove inlined definitions)

  **Approach:**
  - `types.py` defines the core dataclasses. `RubricItem` and `RubricReport` stay as-is for backward compat. Add `CheckResult(passed, score, evidence, note)`, `CheckDef(name, category, weight, check)`, `PersonaDef(name, description, checks)`.
  - `conformance.py` contains one function per existing rubric check (S1-S5, P1-P11, E1-E6, SEC1-SEC3, ST1-ST3), plus a `CONFORMANCE_PERSONA` that assembles them.
  - The existing `score_trace()` function is preserved as a convenience wrapper that runs the conformance persona.

  **Patterns to follow:**
  - Existing `score_trace()` function signature and behavior
  - Project convention: flat modules, no deep nesting

  **Test scenarios:**
  - Importing from `opentraces.quality` produces identical results to the old inline version
  - `score_trace()` backward compatibility (same signature, same scores)

  **Verification:**
  - `pytest tests/test_e2e_dogfood.py` passes unchanged (except import paths)

- [ ] **Unit 2: Build raw session reader**

  **Goal:** Independent JSONL reader that produces `RawSessionSummary` without using `ClaudeCodeParser`.

  **Requirements:** R2

  **Dependencies:** None (parallel with Unit 1)

  **Files:**
  - Create: `src/opentraces/quality/raw_reader.py`
  - Create: `tests/test_raw_reader.py`

  **Approach:**
  - `read_raw_session(path) -> RawSessionSummary` reads JSONL line by line with `json.loads`
  - `RawSessionSummary` fields: `total_lines`, `user_messages`, `assistant_messages`, `tool_use_blocks`, `tool_result_blocks`, `thinking_blocks_total`, `thinking_blocks_with_content`, `usage_entries`, `total_content_chars`, `timestamps`, `subagent_tool_calls` (count of Agent/Task tool_use blocks), `models_seen`, `system_prompt_count`, `corrupted_lines`
  - Counts are extracted from the raw Claude Code JSONL format: `type: "user"|"assistant"`, `message.content[]` blocks with `type: "tool_use"|"tool_result"|"thinking"|"text"`
  - Thinking blocks: count total and separately count those with non-empty `thinking` field (to detect encrypted vs real reasoning)
  - Tolerates corrupted lines (skip and count)

  **Patterns to follow:**
  - `src/opentraces/parsers/claude_code.py` for understanding the raw JSONL format (but NOT importing from it)

  **Test scenarios:**
  - Synthetic 4-line session (matching `test_parser_claude_code.py`'s `_make_minimal_session()` format) produces correct counts
  - Session with thinking blocks (both encrypted/empty and content-bearing) counted correctly
  - Session with sub-agent tool calls (Agent, Task tool names) detected
  - Corrupted lines tolerated and counted
  - Empty file returns zero counts

  **Verification:**
  - Unit tests pass with synthetic data
  - `read_raw_session()` on a real session file produces plausible counts

- [ ] **Unit 3: Schema completeness audit (field walker + gap classifier)**

  **Goal:** Systematically check every field in TraceRecord across a batch of traces. For each field that is empty/None/default/out-of-range in >50% of traces, classify the gap and include the classification in the report.

  **Requirements:** R8, R9

  **Dependencies:** Unit 1 (types)

  **Files:**
  - Create: `src/opentraces/quality/schema_audit.py`
  - Create: `tests/test_schema_audit.py`

  **Approach:**

  **Phase A: Field Walker**

  Walk the TraceRecord Pydantic model recursively, extracting every field path and its value for a given trace. Produce a flat list of `FieldCheck` results:

  ```
  FieldCheck:
    path: str            # e.g. "task.description", "steps[0].reasoning_content", "environment.os"
    field_type: str      # e.g. "str | None", "list[str]", "int"
    populated: bool      # True if non-None, non-empty, non-default
    value_summary: str   # Truncated representation for the report
    expected_range: str   # What this field should contain (from schema description)
    issue: str | None    # "empty", "default_only", "out_of_range", None if fine
  ```

  The walker handles:
  - **Scalar fields** (str, int, float, bool): `None` or default = unpopulated
  - **Optional fields** (`str | None`): `None` = unpopulated
  - **List fields** (`list[Step]`, `list[str]`): empty list = unpopulated
  - **Dict fields** (`dict[str, str]`): empty dict = unpopulated
  - **Nested models** (Task, Agent, Environment, VCS, etc.): recurse into fields
  - **List-of-model fields** (steps, tool_calls, etc.): sample first N items (N=5) and check field population rates across samples
  - **Range checks**: `cache_hit_rate` in [0.0, 1.0], `total_steps > 0` when steps exist, `estimated_cost_usd >= 0`, timestamps in ISO 8601 format

  **Complete field inventory** (every field in the schema, organized by model):

  TraceRecord top-level:
  - `schema_version` -- always populated (hardcoded)
  - `trace_id` -- always populated (generated)
  - `session_id` -- always populated (from raw)
  - `content_hash` -- should be populated after compute_content_hash()
  - `timestamp_start` -- from raw session timestamps
  - `timestamp_end` -- from raw session timestamps
  - `system_prompts` -- dict, may be empty if no system prompts captured
  - `tool_definitions` -- list, may be empty
  - `dependencies` -- list, from manifest files
  - `metadata` -- dict, catch-all

  Task:
  - `task.description` -- from first user message, <=500 chars
  - `task.source` -- "user_prompt", "cli_arg", etc.
  - `task.repository` -- owner/repo format
  - `task.base_commit` -- git SHA

  Agent:
  - `agent.name` -- should always be "claude-code" for v0.1
  - `agent.version` -- CLI version string
  - `agent.model` -- provider/model format

  Environment:
  - `environment.os` -- operating system
  - `environment.shell` -- shell type
  - `environment.vcs.type` -- "git" or "none"
  - `environment.vcs.base_commit` -- HEAD SHA
  - `environment.vcs.branch` -- branch name
  - `environment.vcs.diff` -- unified diff
  - `environment.language_ecosystem` -- list of languages

  Step (sampled across steps):
  - `step.content` -- text content
  - `step.reasoning_content` -- thinking/chain-of-thought
  - `step.model` -- per-step model identifier
  - `step.system_prompt_hash` -- ref into system_prompts map
  - `step.agent_role` -- main/explore/plan
  - `step.parent_step` -- sub-agent hierarchy link
  - `step.call_type` -- main/subagent/warmup
  - `step.subagent_trajectory_ref` -- session ID ref
  - `step.tools_available` -- list of tool names
  - `step.timestamp` -- per-step timestamp

  ToolCall (sampled):
  - `tool_call.tool_call_id` -- unique ID
  - `tool_call.tool_name` -- tool identifier
  - `tool_call.input` -- tool input dict
  - `tool_call.duration_ms` -- execution duration

  Observation (sampled):
  - `observation.source_call_id` -- link to tool call
  - `observation.content` -- result content (<=10K)
  - `observation.output_summary` -- preview (<=200 chars)
  - `observation.error` -- error info

  Snippet (sampled):
  - `snippet.file_path` -- file path
  - `snippet.start_line` -- line number
  - `snippet.end_line` -- line number
  - `snippet.language` -- detected language
  - `snippet.text` -- code content (<=5K)
  - `snippet.source_step` -- step reference

  TokenUsage (sampled across agent steps):
  - `token_usage.input_tokens` -- should be >0 on agent steps
  - `token_usage.output_tokens` -- should be >0 on agent steps
  - `token_usage.cache_read_tokens` -- may be 0
  - `token_usage.cache_write_tokens` -- may be 0
  - `token_usage.prefix_reuse_tokens` -- may be 0

  Outcome:
  - `outcome.success` -- bool or None
  - `outcome.signal_source` -- default "deterministic"
  - `outcome.signal_confidence` -- derived/inferred/annotated
  - `outcome.description` -- text description
  - `outcome.patch` -- unified diff
  - `outcome.committed` -- bool
  - `outcome.commit_sha` -- git SHA

  Attribution (when present):
  - `attribution.version` -- schema version
  - `attribution.experimental` -- bool flag
  - `attribution.files` -- list of attributed files
  - `attribution.files[].path` -- file path
  - `attribution.files[].conversations[].contributor` -- dict
  - `attribution.files[].conversations[].url` -- opentraces:// URL
  - `attribution.files[].conversations[].ranges[].start_line`
  - `attribution.files[].conversations[].ranges[].end_line`
  - `attribution.files[].conversations[].ranges[].content_hash`
  - `attribution.files[].conversations[].ranges[].confidence`

  Metrics:
  - `metrics.total_steps` -- should match len(steps)
  - `metrics.total_input_tokens` -- sum of step tokens
  - `metrics.total_output_tokens` -- sum of step tokens
  - `metrics.total_duration_s` -- wall clock seconds
  - `metrics.cache_hit_rate` -- [0.0, 1.0]
  - `metrics.estimated_cost_usd` -- >= 0

  SecurityMetadata:
  - `security.tier` -- 1, 2, or 3
  - `security.flags_reviewed` -- int
  - `security.redactions_applied` -- int
  - `security.classifier_version` -- version string

  **Phase B: Batch Aggregation**

  `audit_schema_completeness(traces: list[TraceRecord], raw_summaries: list[RawSessionSummary] | None) -> SchemaAuditReport`

  For each field path, compute:
  - `population_rate`: % of traces where the field is populated
  - `issue_category`: computed from population rate + raw session comparison

  **Phase C: Gap Classifier**

  For each field with `population_rate < threshold` (default 80%), classify the gap:

  Classification rules (deterministic first, with raw session cross-reference):

  | Classification | Criteria | Example |
  |---|---|---|
  | `parser_bug` | Raw session contains the signal, parsed trace doesn't | Raw has timestamps on all messages, but `step.timestamp` is None |
  | `enrichment_gap` | Field should be populated by enrichment but isn't | `environment.os` is None because no enrichment module extracts it |
  | `schema_unrealistic` | Field exists in schema but raw sessions fundamentally cannot provide it | A field that requires data not present in Claude Code's JSONL format |
  | `session_dependent` | Field legitimately varies by session | `outcome.committed` is False for exploration sessions, `attribution` is None for sessions with no edits |
  | `not_yet_implemented` | Parser/enrichment code path exists but isn't wired up | Field has a TODO or is documented but not populated |

  The classifier uses these signals:
  - **Raw session comparison**: If `RawSessionSummary` shows the raw data exists but the parsed field is empty -> `parser_bug`
  - **Enrichment module check**: If no enrichment module writes to this field path -> `enrichment_gap`
  - **Session variance**: If some traces have it and some don't, and the ones that don't have a structural reason -> `session_dependent`
  - **Universal emptiness**: If 0% of traces populate the field AND the raw session doesn't contain the data either -> `schema_unrealistic`

  For edge cases where deterministic classification is ambiguous, produce a `needs_review` tag with evidence for human judgment.

  **Report output for each flagged field:**
  ```
  ## Field: environment.os
  Population rate: 0% (0/19 traces)
  Classification: enrichment_gap
  Evidence: No enrichment module writes to environment.os.
    Raw sessions contain OS info in system-reminder blocks,
    but the parser does not extract it.
  Impact: Domain sourcing consumers cannot filter by OS.
    Analytics dashboard cannot show OS distribution.
  Recommendation: Add OS extraction to parser or enrichment pipeline.
  ```

  **Test scenarios:**
  - Synthetic trace with all fields populated -> 100% population, no flags
  - Synthetic trace with known empty fields -> correct classification
  - Batch of traces with mixed population -> correct aggregation rates
  - Known `enrichment_gap` fields (e.g., `environment.os`) classified correctly
  - Known `session_dependent` fields (e.g., `outcome.committed`) classified correctly

  **Verification:**
  - Every field in `models.py` appears in the audit output
  - Classifications are consistent across runs (deterministic)
  - Report section is human-readable with actionable recommendations

- [ ] **Unit 4: Four persona rubrics**

  **Goal:** Define Training/SFT, RL/RLHF, Analytics, and Domain Sourcing personas with their check functions.

  **Requirements:** R1, R7

  **Dependencies:** Unit 1 (types)

  **Files:**
  - Create: `src/opentraces/quality/personas/__init__.py`
  - Create: `src/opentraces/quality/personas/training.py`
  - Create: `src/opentraces/quality/personas/rl.py`
  - Create: `src/opentraces/quality/personas/analytics.py`
  - Create: `src/opentraces/quality/personas/domain.py`
  - Create: `tests/test_persona_rubrics.py`

  **Approach:**

  Each persona module exports a `PERSONA` constant of type `PersonaDef` and individual check functions.

  **Training/SFT checks (~10 items, grounded in ADP requirements):**
  - T1: Alternating user/agent role pattern (weight 1.0) -- ADP requires alternating action/observation
  - T2: Every tool_call has a matching observation (weight 1.0) -- ADP conversion requires paired action/observation
  - T3: No dangling observations (source_call_id links valid) (weight 0.9) -- data integrity for SFT
  - T4: System prompts deduplicated to top-level map (weight 0.7) -- storage efficiency for training datasets
  - T5: Content fields non-empty on agent steps (weight 0.8) -- empty agent turns are noise for SFT
  - T6: Reasoning coverage >= 80% of agent steps with tool calls (weight 0.8) -- ADP quality gate: >=80% of tool calls should be paired with reasoning text
  - T7: Reasoning content present (partial credit 0.5 for encrypted thinking) (weight 0.5) -- Opus 4.6 encrypted thinking is detectable but not usable
  - T8: Task description provides context for training sample (weight 0.6) -- needed for SFT task framing
  - T9: Outcome signals present (committed or success) (weight 0.7) -- distinguishes demonstration quality
  - T10: Warmup steps labeled with call_type (weight 0.5) -- ADP resolution: "filtering loses info, labeling preserves it"

  **RL/RLHF checks (~8 items, grounded in outcome signal value):**
  - RL1: outcome.committed is explicitly set (not default None) (weight 1.0) -- the primary reward proxy, unique to opentraces vs ADP/traces.com
  - RL2: outcome.signal_confidence is "derived" or "annotated" (not default) (weight 1.0) -- RL consumers must know confidence to weight samples
  - RL3: outcome.patch present when committed=True (weight 0.9) -- ground truth diff for reward attribution
  - RL4: Per-step token_usage populated on agent steps (>80%) (weight 0.8) -- enables cost-penalized reward functions
  - RL5: estimated_cost_usd > 0 (weight 0.7) -- cost signal for training sample weighting
  - RL6: Sub-agent hierarchy intact when subagents exist (parent_step links valid) (weight 0.7) -- orchestration research signal, absent in ADP
  - RL7: outcome.success explicitly set (bonus, not required) (weight 0.4) -- annotation-dependent, zero-required-annotation principle
  - RL8: Multiple model tiers visible in steps (model field populated) (weight 0.3) -- multi-model orchestration research

  **Analytics/Observability checks (~8 items, grounded in traces.com competitive gap):**
  - A1: cache_hit_rate computed and in [0.0, 1.0] (weight 1.0) -- "architectural fingerprint", traces.com can't provide this
  - A2: estimated_cost_usd > 0 (weight 0.9) -- contributor dashboard core metric
  - A3: total_duration_s > 0 (weight 0.8) -- session length for cohort analytics
  - A4: Timestamps on individual steps (>80% of steps) (weight 0.7) -- per-step timeline, traces.com has trace-level only
  - A5: Token breakdown per step (input + output + cache_read all populated) (weight 0.8) -- Kobe Chen's 81% cost savings from cache analysis
  - A6: Agent model identified on steps (weight 0.6) -- model distribution analytics
  - A7: total_steps matches actual step count (weight 0.5) -- internal consistency
  - A8: Warmup vs real step distinction via call_type (weight 0.4) -- accurate step count analytics

  **Domain Sourcing checks (~8 items, grounded in HF dataset discovery):**
  - D1: language_ecosystem populated (weight 1.0) -- HF dataset filtering: "all Python traces"
  - D2: dependencies extracted (at least 1 when language_ecosystem is non-empty) (weight 0.9) -- "all Django traces", "all React traces"
  - D3: task.description meaningful (> 10 chars, not just whitespace) (weight 0.8) -- HF search, human browsing
  - D4: VCS info populated (type, branch) (weight 0.7) -- context for domain queries
  - D5: Snippets with language tags (weight 0.6) -- code-specific dataset curation
  - D6: Attribution block present when Edit/Write tool calls exist (weight 0.5) -- Agent Trace spec bridge, experimental
  - D7: Agent name + version populated (weight 0.5) -- "all Claude Code v1.x traces"
  - D8: Environment OS populated (weight 0.3) -- cross-platform analysis

  **Test scenarios:**
  - Synthetic traces with known characteristics score as expected per persona
  - A trace with no commits scores low on RL but high on Analytics (expected divergence)
  - A trace with no dependencies scores low on Domain but high on Training
  - The ADP 80% reasoning threshold check (T6) correctly measures tool-call-to-reasoning ratio
  - All check functions handle edge cases (empty steps, None fields)

  **Verification:**
  - Unit tests with synthetic traces
  - Each persona produces a score between 0-100 for any valid TraceRecord

- [ ] **Unit 5: Preservation comparator**

  **Goal:** Compare raw session against parsed TraceRecord to compute preservation ratios and detect signal loss.

  **Requirements:** R2

  **Dependencies:** Unit 1 (types), Unit 2 (raw_reader)

  **Files:**
  - Create: `src/opentraces/quality/preservation.py`
  - Create: `tests/test_preservation.py`

  **Approach:**
  - `compare_preservation(record: TraceRecord, raw: RawSessionSummary) -> PreservationReport`
  - `PreservationReport` contains:
    - `ratios: dict[str, float]` -- per-category preservation (0.0-1.0)
    - `overall: float` -- weighted average
    - `signal_losses: list[SignalLoss]` -- signals present in raw but absent/reduced in parsed
    - `impossible_signals: list[str]` -- schema fields populated but no raw source
  - Categories and how to compute:
    - `messages`: `parsed_steps / raw_(user+assistant)_messages` (may be >1.0 due to sub-agent inlining, cap at 1.0)
    - `tool_calls`: `parsed_tool_calls / raw_tool_use_blocks`
    - `tool_results`: `parsed_observations / raw_tool_result_blocks`
    - `thinking`: special handling for encrypted thinking -- `(thinking_blocks_with_content_in_parsed + 0.5 * encrypted_blocks) / raw_thinking_blocks_total`
    - `token_usage`: `steps_with_nonzero_tokens / raw_usage_entries`
    - `timestamps`: `steps_with_timestamps / raw_timestamps`
    - `subagents`: `parsed_subagent_steps / raw_subagent_tool_calls` (if any; 1.0 if neither has subagents)
  - Signal loss detection:
    - Raw has N thinking blocks with content but parsed has M < N reasoning_content fields -- signal loss
    - Raw has N tool_results but parsed has M < N observations -- signal loss (with count)
    - Raw has timestamps on all messages but parsed steps lack them -- signal loss
    - Raw has K usage entries but parsed has J < K steps with token_usage -- signal loss
  - Impossible signal detection:
    - `outcome.committed = True` but raw session has no git-related tool calls (Bash with git commands) -- flag as suspicious
    - `attribution` populated but no Edit/Write tool calls in raw -- flag

  **Test scenarios:**
  - Perfect preservation (all raw signals present in parsed) -> ratios at 1.0
  - Known truncation scenario -> measurable content preservation
  - Missing tool results -> tool_results ratio < 1.0
  - Session with encrypted thinking -> thinking ratio between 0.0 and 1.0
  - Session with sub-agents -> subagent ratio computed
  - No raw session provided -> PreservationReport is None (graceful skip)

  **Verification:**
  - Unit tests with synthetic paired data
  - Real session comparison produces ratios in [0.0, 1.0] range

- [ ] **Unit 6: Scoring engine and report generation**

  **Goal:** Engine that runs all personas + preservation against a trace and generates a combined report.

  **Requirements:** R1, R2, R5, R7

  **Dependencies:** Units 1-5

  **Files:**
  - Create: `src/opentraces/quality/engine.py`
  - Modify: `src/opentraces/quality/__init__.py` (public API exports)

  **Approach:**
  - `assess_trace(record, raw_session_path=None, personas=None) -> TraceAssessment`
  - `TraceAssessment` contains:
    - `persona_scores: dict[str, PersonaScore]` (per-persona weighted score + item details)
    - `preservation: PreservationReport | None` (only if raw_session_path provided)
    - `overall_utility: float` -- weighted average across persona scores
  - `assess_batch(traces, raw_session_dir=None) -> BatchAssessment`
    - Runs assessment on multiple traces, aggregates statistics
    - Matches traces to raw sessions via session_id
    - Runs `audit_schema_completeness()` across the full batch to identify systematically empty fields
  - `generate_report(assessment: BatchAssessment) -> str`
    - Markdown report with:
      - Summary table (trace_id, task, conformance%, training%, rl%, analytics%, domain%, preservation%)
      - Per-persona breakdowns with failing items highlighted
      - **Schema completeness audit section**: field-by-field population rates, gap classifications (parser_bug / enrichment_gap / schema_unrealistic / session_dependent / not_yet_implemented), evidence, impact, and recommendations
      - ADP compatibility notes (reasoning coverage %, tool pairing %)
      - Preservation analysis (signal losses with counts, impossible signals)
      - Category averages and distribution
      - Competitive context notes (what traces.com/DataClaw can't provide)
  - Default personas: conformance + training + rl + analytics + domain
  - Custom persona sets via `personas` parameter
  - Each check function wrapped in try/except to prevent one bad check from killing the assessment

  **Patterns to follow:**
  - Existing report generation in `test_e2e_dogfood.py:650-712`

  **Test scenarios:**
  - Engine with all personas produces scores for each
  - Engine without raw session skips preservation gracefully
  - Report markdown is valid and contains all sections
  - Custom persona subset works
  - Exception in one check function doesn't crash the engine

  **Verification:**
  - Engine produces a `TraceAssessment` for any valid TraceRecord
  - Report renders as readable markdown

- [ ] **Unit 7: Integration tests with real sessions**

  **Goal:** Run the full harness against the user's actual sessions, with meaningful thresholds.

  **Requirements:** R4, R7

  **Dependencies:** Unit 5

  **Files:**
  - Modify: `tests/test_e2e_dogfood.py` (add persona assessment tests alongside existing structural tests)
  - Create: `tests/test_harness_integration.py`

  **Approach:**
  - `test_harness_integration.py` uses the same fixture pattern as `test_e2e_dogfood.py` (reads from `~/.claude/projects/`, skips if absent)
  - Runs `assess_batch()` with all personas + real session paths for preservation
  - Thresholds (grounded in expected session characteristics):
    - Conformance: individual >= 70%, average >= 80% (unchanged)
    - Training: individual >= 50%, average >= 65% (encrypted thinking limits reasoning coverage)
    - RL: no minimum individual (session-dependent, a pure exploration session with no commit is valid), average >= 40%
    - Analytics: individual >= 60%, average >= 70% (most signals should be present)
    - Domain: individual >= 45%, average >= 55% (depends on project having manifests)
    - Preservation: overall >= 0.85 (we should preserve 85%+ of raw signals)
  - Generates the combined report to `.gstack/qa-reports/persona-rubric-report.md`
  - `test_e2e_dogfood.py` updates: replace inline rubric definitions with imports, add a `TestPersonaAssessment` class that runs the new engine
  - Tests include a `TestADPReadiness` class that specifically checks the ADP-grounded thresholds:
    - Tool call pairing rate (target: >= 95%)
    - Reasoning coverage (target: >= 60%, acknowledging encrypted thinking)
    - Role alternation integrity

  **Test scenarios:**
  - At least one trace scores above threshold on each persona
  - Preservation ratio is computed for traces where raw sessions are available
  - Report file is written and non-empty
  - No regression on existing dogfood tests (same scores, different import paths)
  - ADP readiness metrics are computed and reported

  **Verification:**
  - `pytest tests/test_e2e_dogfood.py tests/test_harness_integration.py -v` all pass
  - Report file at `.gstack/qa-reports/persona-rubric-report.md` is human-readable
  - Report includes per-persona scores, preservation ratios, and ADP compatibility notes

## System-Wide Impact

- **Interaction graph:** The quality module reads from schema models and raw JSONL only. No writes, no mutations. The engine is pure computation.
- **Error propagation:** Check functions that encounter None fields should return score=0.0 with evidence, not raise exceptions. The engine wraps each check in a try/except to prevent one bad check from killing the entire assessment.
- **API surface parity:** The existing `score_trace()` function remains available at the same import path (re-exported from `src/opentraces/quality/__init__.py`).
- **Integration coverage:** The integration test with real sessions is the primary cross-layer verification. Unit tests use synthetic data for each component.

## Risks & Dependencies

- **Risk: Real sessions may not exercise all persona checks.** Some checks (e.g., RL outcome.committed) depend on session characteristics. Mitigation: thresholds are per-persona and intentionally low for session-dependent items. Report clearly labels which checks are N/A.
- **Risk: Raw JSONL format changes between Claude Code versions.** Mitigation: The raw reader is defensive (skips unrecognized fields, tolerates missing keys). Counts are best-effort.
- **Risk: Encrypted thinking (Opus 4.6) makes training persona reasoning checks unreliable.** Mitigation: Partial credit scoring (0.5 for "present but encrypted"). T6 (ADP 80% reasoning threshold) is weighted at 0.8, not 1.0, and the report explicitly notes encrypted thinking impact.
- **Risk: Preservation ratios may be misleading when sub-agent inlining increases step count.** Mitigation: message preservation is capped at 1.0. Sub-agent inlining is measured separately.

## Sources & References

- Existing rubric: `tests/test_e2e_dogfood.py`
- Schema models: `packages/opentraces-schema/src/opentraces_schema/models.py`
- Parser (raw format reference): `src/opentraces/parsers/claude_code.py`
- Design intent: `kb/resources/intent.md`
- Discussion log (quality decisions Q5, Q7, Q8, Q9): `kb/discussion-log.md`
- ADP paper analysis: `kb/background-research/12-agent-data-protocol-paper.md` -- empirical training thresholds, schema gap analysis
- traces.com competitive analysis: `kb/background-research/04-traces-com.md` -- analytics gap, per-message metadata absence
- Kobe Chen cache research: `kb/background-research/02-hf-community-agent-traces-ecosystem.md`
