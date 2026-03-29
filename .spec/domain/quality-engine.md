---
schema_version: "1.0"
title: Quality Engine
scope: src/opentraces/quality
---

# Quality Engine

## Entities

### Core Types

**CheckResult**: Output of a single quality check. Fields: `passed` (bool), `score` (0.0-1.0), `evidence` (str), `note` (str).

**CheckDef**: Definition of a check. Fields: `name`, `category`, `weight` (0.0-1.0), `check` (callable taking `(TraceRecord, dict) -> CheckResult`).

**PersonaDef**: A named collection of CheckDefs with a description. Fields: `name`, `description`, `checks: list[CheckDef]`.

**RubricItem**: Scored check result with metadata: name, category, weight, passed, score, evidence, note.

**RubricReport**: Full rubric report for one trace. Properties: `total_score` (weighted 0-100), `pass_rate` (0-100%), `category_scores` (dict).

### Assessment Types

**PersonaScore**: Score for one persona on one trace. Includes `total_score` (0-100), `pass_rate`, `items`, `category_scores`, plus optional LLM judge fields (`deterministic_score`, `judge_score`, `judge_result`).

**TraceAssessment**: Full assessment of one trace across all personas. Includes `persona_scores`, optional `preservation`, and `overall_utility` (simple average of persona scores).

**BatchAssessment**: Aggregated assessment across a batch. Includes per-trace assessments, schema audit, persona averages, and preservation average.

**MultiProjectAssessment**: Cross-project assessment with cohorts, cross-project schema audit, and aggregation.

### Judge Types

**JudgeDimension**: One rubric dimension from LLM judge: `name`, `score` (1-5), `rationale`.

**JudgeResult**: Complete judge result: `persona_name`, `dimensions`, `overall_score` (0-100), `model_used`, `skipped`, `skip_reason`.

**PersonaBrief**: Parsed from YAML frontmatter markdown files in `personas/briefs/`. Contains `persona`, `description`, `dimensions` (list of BriefDimension), `prose`.

## Business Rules

### Persona System
Five personas evaluate traces from different consumer perspectives:

1. **Conformance** (built-in, always runs): 27 structural checks across categories: schema (5), parser (11), enrichment (6), security (3), structure (3). Validates the trace conforms to the opentraces schema spec.

2. **Training/SFT** (T1-T10): Evaluates trace utility for supervised fine-tuning. Checks: alternating roles, tool_call/observation pairing, system prompt dedup, reasoning coverage, content presence, outcome signals, warmup labeling.

3. **RL/RLHF** (RL1+): Evaluates for reinforcement learning. Checks: outcome.committed explicitly set, signal_confidence, cost signals, sub-agent hierarchy, model identification.

4. **Analytics** (A1+): Evaluates for observability. Checks: cache_hit_rate, estimated_cost, total_duration, timestamps, token breakdowns, internal consistency.

5. **Domain** (D1+): Evaluates for dataset discovery. Checks: language_ecosystem, dependencies, task descriptions, VCS info, snippets, attribution, agent identity.

### Scoring Formula
For each persona on each trace:
```
total_score = sum(item.score * item.weight for item in items) / sum(item.weight for item in items) * 100
```
Scores are 0-100, rounded to 1 decimal place.

Pass rate: `count(passed) / count(items) * 100`.

Category scores: Same formula but grouped by category.

Overall utility: Simple average of all persona total_scores.

### LLM Judge System (Optional)
Complements deterministic checks with qualitative LLM evaluation. Controlled by `enable_judge` flag.

**Flow**:
1. Summarize the trace for the judge (`summarize_for_judge`): compresses TraceRecord to ~2-4K tokens with representative steps, metrics, and deterministic issues
2. Load persona brief (YAML frontmatter markdown from `personas/briefs/`)
3. Build system prompt with persona description, scoring dimensions, and JSON response format
4. Call Anthropic API (temperature=0, max_tokens=1024)
5. Parse JSON response into JudgeDimension list
6. Compute weighted overall score

**Representative step selection** (3-5 steps):
- First agent step
- Step with most tool calls + reasoning
- Step with highest tool call count
- Step with an error observation
- Last agent step

**Score scaling**: Judge scores are 1-5 per dimension, scaled to 0-100 via `(score - 1) / 4 * 100`.

**Hybrid blending**: When judge runs, final score = `deterministic_weight * det_score + (1 - deterministic_weight) * judge_score`. Default deterministic_weight = 0.6.

**Graceful degradation**: Judge skips with reason when: brief not found, anthropic SDK not installed, API key not set, API call fails, response parse fails. Missing dimensions default to score 3 (neutral).

**Conformance persona is excluded** from judge evaluation (no brief exists).

### Schema Completeness Audit
Walks every field in the TraceRecord model across a batch:
- Computes population rates for each field
- Classifies gaps into: `parser_bug`, `enrichment_gap`, `schema_unrealistic`, `session_dependent`, `not_yet_implemented`, `needs_review`, `ok`

**Field inventory**: 60+ fields with metadata: path, description, source (parser/enrichment:X/security/generated), expected_when (always/git_repo/has_edits/optional/...), persona_impact list.

**Gap classification logic**:
- Population rate >= 80% -> `ok`
- Field in `_NOT_YET_IMPLEMENTED` set -> `not_yet_implemented` (task.repository, task.base_commit, security.classifier_version)
- Field in `_SESSION_DEPENDENT` set with conditional expected_when -> `session_dependent`
- Raw session has signal but parsed doesn't -> `parser_bug`
- Enrichment source field below 50% when expected_when=always -> `enrichment_gap`
- Parser field expected always but below 50% -> depends on raw signal availability

### Conformance Checks Detail

**Schema checks** (S1-S5): schema_version matches constant, trace_id is UUID format (>=32 chars with hyphens), content_hash is 64-char hex, agent.name = "claude-code", timestamps present.

**Parser checks** (P1-P11): step count >= 2, roles are user/agent (not human/assistant), step_index unique and monotonic, tool_calls have IDs, observations linked, call_type assigned (>80%), subagent parent_step linked (>80%), token_usage populated (>50%), snippets extracted (>0), task.description present, reasoning_content captured.

**Security checks** (SEC1-SEC3): security.tier in (1,2,3), no real secrets in serialized output (spot checks 5 patterns), paths anonymized (no raw `/Users/<username>/`).

**Structure checks** (ST1-ST3): JSONL is valid single-line JSON, content_hash is deterministic (same input = same hash), system_prompts deduplicated with valid step references.

### Training Persona Checks Detail

**T1 (weight 1.0)**: Alternating user/agent roles. Filters out system and subagent steps. Pass threshold: >=90% alternation ratio.

**T2 (weight 1.0)**: Every tool_call has a matching observation. Pass threshold: >=95% pairing ratio.

**T3 (weight 0.9)**: No dangling observations (source_call_id references valid tool_call). Pass threshold: >=95%.

**T4 (weight 0.7)**: system_prompts dict non-empty (deduplication occurred).

**T5 (weight 0.8)**: Agent steps have content or tool_calls (not both empty). Pass threshold: >=90%.

**T6 (weight 0.8)**: Reasoning coverage >=80% of agent steps with tool calls. ADP quality gate. Readable reasoning = 1.0 credit, redacted = 0.5 credit (model reasoned but content unavailable for SFT).

**T7 (weight 0.5)**: Any reasoning_content present. Encrypted thinking gets 0.5 partial credit.

**T8 (weight 0.6)**: task.description present and non-empty.

**T9 (weight 0.7)**: Outcome signals present (committed=True or success is not None).

**T10 (weight 0.5)**: Warmup steps labeled and positioned before main/subagent steps. If call_type labeling is active but no warmup detected, partial credit 0.5.

### Multi-Project Assessment
- Discovers Claude Code projects in `~/.claude/projects/`
- Samples sessions per project (most recent by mtime, capped by `max_per_project` and `max_total`)
- Runs parser-only enrichment (metrics, ecosystem, dependencies, attribution, commit detection from steps)
- Assesses each batch per project with full persona suite
- Computes cross-project schema audit and averages

## Calculations

- **Weighted score**: `sum(score * weight) / sum(weight) * 100`
- **Judge scale**: `(dim_score - 1) / 4 * 100` (maps 1-5 to 0-100)
- **Hybrid blend**: `det_weight * det_score + (1 - det_weight) * judge_score`
- **Population rate**: `populated_count / total_count` (field-level) or sampled across array items

## State Machines

None. Quality assessment is a pure function: TraceRecord -> Assessment.

## Edge Cases

1. **Check exceptions**: If a check function raises, the engine catches it and records a RubricItem with `passed=False, score=0.0, evidence="ERROR: {e}"`.
2. **Zero-weight division guard**: `max(total_weight, 0.001)` prevents division by zero in category scoring.
3. **Empty batch**: `audit_schema_completeness([])` returns a report with 0 traces and the full field spec count.
4. **Raw data serialization failure**: `json.loads(record.to_jsonl_line())` is wrapped in try/except. If it fails, `raw_data=None` is passed to checks.
5. **Judge missing dimensions**: Any dimension not returned by the judge defaults to score 3 (neutral) to avoid penalizing for API parsing issues.
6. **Conformance check SEC2**: Only spot-checks 5 specific secret patterns (not the full 21-pattern library). This is a quick sanity check, not a replacement for the full security pipeline.
