---
title: "feat: Hybrid deterministic + LLM persona rubrics"
type: feat
status: active
date: 2026-03-28
origin: kb/plans/2026-03-28-001-feat-persona-rubric-harness-plan.md
---

# Hybrid Deterministic + LLM Persona Rubrics

## Overview

Evolve the quality assessment system from purely deterministic Python checks to a hybrid model: keep structural checks in code (fast, reproducible, CI-safe), add markdown persona briefs that define qualitative rubric criteria, and introduce an LLM judge pass that evaluates the subjective dimensions code cannot measure. This lets us track both structural correctness and real-world utility over time as the parser and enrichment pipeline evolve.

## Problem Frame

The current four personas (training, rl, analytics, domain) only measure structural properties: "is this field populated?", "do IDs link correctly?", "is the count above a threshold?" They cannot answer the questions a real downstream consumer would ask:

- **SFT engineer:** "Would this trace actually teach a model something useful about coding, or is it noise?"
- **RL researcher:** "Can I trust this outcome signal enough to use it as a reward, and can I trace the causal chain from actions to outcome?"
- **Analytics builder:** "Does this data tell a coherent, internally consistent story I can visualize?"
- **Dataset curator:** "Would someone searching for Django migration traces actually find this one, and would the metadata be accurate?"

These are judgment calls that require reading the actual content, not just checking field presence. The hybrid approach closes this gap.

## Requirements Trace

- R1. Each persona has a markdown brief describing the downstream application, the ideal trace, and scored rubric dimensions
- R2. Deterministic checks remain as-is (fast, CI-safe, reproducible) with targeted fixes for known bugs
- R3. An LLM judge evaluator reads the persona brief + a serialized trace and returns structured scores per rubric dimension
- R4. Combined scores (deterministic + LLM) produce a single persona utility score
- R5. Reports go to `.gstack/qa/` with timestamped filenames (already done)
- R6. The system works without an LLM available (graceful degradation to deterministic-only)
- R7. Rubric dimensions are grounded in the background research (ADP paper, academic surveys, downstream application requirements)

## Scope Boundaries

- Not building an LLM-as-judge evaluation framework from scratch, just a thin adapter that calls Claude and parses structured output
- Not changing the schema or parser, only the quality measurement layer
- Not adding new personas, only enriching the existing four
- Not automating rubric evolution (the briefs are human-curated documents)
- Not adding Parquet/columnar output for scores (JSON in reports is sufficient for v0.1)

## Context & Research

### Relevant Code and Patterns

- `src/opentraces/quality/types.py` -- `CheckDef`, `CheckResult`, `PersonaDef` are the extension points
- `src/opentraces/quality/engine.py` -- `_run_persona()`, `assess_trace()`, `assess_batch()` orchestrate scoring
- `src/opentraces/quality/personas/{training,rl,analytics,domain}.py` -- current deterministic checks
- `src/opentraces/quality/gates.py` -- threshold enforcement
- `tests/test_persona_rubrics.py` -- fixture traces and expected scores

### Background Research Grounding

**SFT (ADP paper, ICLR 2026):**
- >=80% reasoning coverage threshold is empirically validated
- Cross-task transfer from diverse datasets outperforms single-domain (10x in some cases)
- Complete TAO loops without truncation are non-negotiable
- 1.3M unified trajectories produced ~20% SFT improvement

**RL (AgentPRM, STeCa, Agent-R1, LightningRL):**
- Step-level reward signals (not just final outcome) are critical for process reward models
- Tool return status (`Observation.error`) serves as intermediate reward signal
- Cost-penalized reward functions need per-step token usage
- Batch diversity (mix of success/failure) is essential, all-success datasets are useless for RL

**Analytics (Kobe Chen trace analysis, Langfuse, ICSE 2026):**
- Cache hit rate is an "architectural fingerprint" enabling 81% cost savings
- Failed trajectories are consistently longer with higher variance (cheap quality signal)
- Three-surface model: Operational + Cognitive + Contextual
- 39.9-59.7% of trajectory tokens are waste (AgentDiet)

**Domain (HF ecosystem, CASS, community datasets):**
- Query pattern: "all traces where Claude Code debugged a Django migration, outcome: success"
- Semantic search over task descriptions is a primary discovery mechanism
- 32K+ community traces exist but lack standardized metadata for filtering
- Attribution blocks enable domain-specific code change pattern analysis

### Known Bugs to Fix Alongside

1. **Gate bug in `gates.py`:** `if avg > 0 and avg < threshold.min_average` lets 0.0 averages pass silently
2. **A5 docstring vs implementation mismatch:** claims to check `cache_read_tokens` but doesn't
3. **D8 always fails:** checks `environment.os` which is `_NOT_YET_IMPLEMENTED`
4. **ST3 is a no-op:** always returns `passed=True, score=1.0`
5. **T10 is trivially true:** passes if any single step has `call_type` set, doesn't verify warmup identification

## Key Technical Decisions

- **Markdown briefs live in `src/opentraces/quality/personas/briefs/`:** Co-located with the Python checks they complement. Each brief is `{persona_name}.md` with structured YAML frontmatter defining the rubric dimensions, weights, and scoring criteria. The Python code reads these at runtime.

- **LLM judge is a separate scoring layer, not mixed into existing checks:** Deterministic checks return `CheckResult` as before. LLM judge returns a parallel `JudgeResult` with per-dimension scores. The engine combines them with configurable weights (default: 60% deterministic, 40% LLM). This keeps CI fast (deterministic-only) and full assessment rich (hybrid).

- **Structured output via system prompt + JSON schema:** The LLM judge prompt includes the persona brief, a serialized trace summary (not the full trace, which would blow context), and asks for a JSON response with scores per dimension. No function calling needed, just constrained JSON output.

- **Trace summary for LLM context:** Full traces can be 100K+ tokens. The judge gets a summary: task description, first/last user messages, a sample of 3-5 representative agent steps (with reasoning + tool calls), outcome, metrics, and any notable structural issues flagged by deterministic checks. A `summarize_for_judge()` function builds this.

- **Graceful degradation:** If no API key is available or the LLM call fails, the system falls back to deterministic-only scoring with a flag in the report noting the LLM pass was skipped. No hard dependency on LLM availability.

## Open Questions

### Resolved During Planning

- **Which LLM to use for judging?** Claude (via the Anthropic SDK already in the project's environment). Haiku for cost efficiency on routine runs, Sonnet for detailed assessment. Configurable via `--judge-model` flag.
- **How much of the trace to show the judge?** A structured summary, not the raw JSONL. The summary includes enough content to make qualitative judgments without exceeding ~4K tokens per trace.
- **Where do briefs live?** In `src/opentraces/quality/personas/briefs/` as markdown files, co-located with the Python check modules.

### Deferred to Implementation

- Exact prompt engineering for the judge system prompt (will iterate based on output quality)
- Whether `summarize_for_judge()` needs per-persona variants or if a single summary works for all four
- Calibration of the 60/40 deterministic/LLM weight split (may need adjustment after seeing real scores)

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
Current flow:
  TraceRecord -> _run_persona(checks) -> PersonaScore

New flow:
  TraceRecord -> _run_persona(checks) -> DeterministicScore
                                      |
  TraceRecord -> summarize_for_judge() -> brief + summary -> LLM -> JudgeScore
                                      |
  DeterministicScore + JudgeScore -> HybridPersonaScore
```

Each persona brief defines rubric dimensions like:

```yaml
---
persona: training
dimensions:
  - name: reasoning_quality
    weight: 0.3
    description: "Does the reasoning show genuine problem-solving or is it filler?"
    scoring: "1=noise/repetitive, 3=adequate but generic, 5=insightful chain-of-thought"
  - name: demonstration_value
    weight: 0.25
    description: "Would an SFT pipeline learn useful coding behavior from this trace?"
    ...
---

# Training/SFT Consumer Persona Brief

You are evaluating traces as if you are building a supervised fine-tuning
pipeline for a coding agent...
```

## Implementation Units

- [ ] **Unit 1: Persona brief markdown files**

**Goal:** Create the four persona briefs with research-grounded rubric dimensions.

**Requirements:** R1, R7

**Dependencies:** None

**Files:**
- Create: `src/opentraces/quality/personas/briefs/training.md`
- Create: `src/opentraces/quality/personas/briefs/rl.md`
- Create: `src/opentraces/quality/personas/briefs/analytics.md`
- Create: `src/opentraces/quality/personas/briefs/domain.md`

**Approach:**

Each brief has YAML frontmatter with structured rubric dimensions (name, weight, description, scoring guide) and a prose section that puts the evaluator in the consumer's shoes. Dimensions are grounded in specific research findings:

**Training brief dimensions:**
- `reasoning_quality` (0.3) -- Does reasoning show genuine problem-solving? (ADP: 80% reasoning coverage matters because quality matters, not just presence)
- `demonstration_value` (0.25) -- Would SFT learn useful coding behavior? (CoderForge: fine-tuning boosted SWE-Bench from 23% to 59.4% with quality traces)
- `task_clarity` (0.2) -- Is the task description a usable training prompt? (ADP: task framing drives cross-task transfer)
- `conversation_naturalness` (0.15) -- Clean turn structure without artifacts? (Bouzenia & Pradel: TAO normalization works without info loss when source is clean)
- `tool_use_coherence` (0.1) -- Are tool calls purposeful with meaningful arguments? (AgentDiet: 40-60% of tokens are waste)

**RL brief dimensions:**
- `outcome_trustworthiness` (0.3) -- Can you trust the reward signal? Is committed=True genuine? (unique to opentraces, no academic schema has this)
- `causal_traceability` (0.25) -- Can you trace from outcome back to the actions that caused it? (TRACE/WWW'26: "high-score illusion" from flawed processes)
- `step_level_signals` (0.2) -- Are there meaningful intermediate signals (errors, partial results)? (AgentPRM: step-level promise + progress for PRMs)
- `cost_reward_feasibility` (0.15) -- Does token/cost data support cost-penalized rewards? (LightningRL: AIR module uses tool return status)
- `diversity_signal` (0.1) -- Does this trace add diversity to an RL batch? (ADP: mixed datasets outperform single-domain 10x)

**Analytics brief dimensions:**
- `timeline_coherence` (0.3) -- Do timestamps, durations, and step ordering tell a consistent story? (Langfuse: Gantt chart visualization requires monotonic, reasonable timestamps)
- `cost_model_credibility` (0.25) -- Are the cost/token numbers internally consistent and plausible? (Kobe Chen: 81% cost savings require accurate cache data)
- `operational_completeness` (0.2) -- Can you reconstruct what happened during the session? (AgentSight: three-surface observability model)
- `anomaly_detectability` (0.15) -- Would unusual patterns (high cost, many retries, long duration) be visible? (ICSE 2026: failed traces are longer with higher variance)
- `aggregation_readiness` (0.1) -- Can this trace be meaningfully aggregated with others? (Consistent field semantics, no NaN-generating edge cases)

**Domain brief dimensions:**
- `discoverability` (0.3) -- Would someone searching for this type of trace find it? (HF ecosystem: metadata-first query -> download matching subset)
- `metadata_accuracy` (0.25) -- Do language/framework/dependency tags accurately describe the work? (CASS: 19 connectors show diversity of metadata needs)
- `task_specificity` (0.2) -- Is the task description specific enough for semantic search? (Community traces: 32K+ exist but hard to find relevant ones)
- `attribution_utility` (0.15) -- Does the attribution block accurately reflect code changes? (Agent Trace spec: line-level attribution for domain analysis)
- `reproducibility_context` (0.1) -- Is there enough context to understand the environment? (VCS info, dependencies, OS/shell)

**Patterns to follow:**
- YAML frontmatter convention similar to the plan files in this project
- Prose style should be second-person ("You are evaluating...") to prime the LLM judge

**Test scenarios:**
- Each brief parses correctly as YAML frontmatter + markdown body
- Dimension weights sum to 1.0 per persona
- All dimension names are unique within a persona

**Verification:**
- Briefs exist and are syntactically valid
- A human reviewer confirms the dimensions match the research grounding

---

- [ ] **Unit 2: Trace summarizer for LLM context**

**Goal:** Build a `summarize_for_judge()` function that compresses a TraceRecord into a structured summary suitable for LLM evaluation (~2-4K tokens).

**Requirements:** R3, R6

**Dependencies:** Unit 1 (needs to know what dimensions the judge will evaluate)

**Files:**
- Create: `src/opentraces/quality/judge.py`
- Test: `tests/test_judge.py`

**Approach:**
- Extract: task description, first user message, outcome block, metrics block, security tier
- Sample 3-5 "representative" agent steps: first agent step, one with tool calls + reasoning, one with the highest tool call count, last agent step, one with an error observation (if any)
- For each sampled step: include content (truncated to 500 chars), reasoning_content (truncated to 300 chars), tool call names + truncated inputs, observation summaries
- Include deterministic check failures as context ("The structural checks flagged: T6 reasoning coverage at 65%, A5 missing cache_read_tokens")
- Return a structured dict that can be serialized to the judge prompt

**Patterns to follow:**
- Similar truncation approach to `Observation.output_summary` in the schema
- Step sampling heuristic inspired by the multi-project eval's session sampling (most recent, most interesting)

**Test scenarios:**
- Rich trace produces summary under 4K tokens
- Minimal trace produces valid (if sparse) summary
- Encrypted reasoning is noted as encrypted, not shown as content
- Empty/None fields are omitted, not shown as "None"
- Steps with no content are skipped in sampling

**Verification:**
- `summarize_for_judge()` returns a dict with consistent keys
- Token count estimate stays under 4K for typical traces

---

- [ ] **Unit 3: LLM judge evaluator**

**Goal:** Build the LLM judge that takes a persona brief + trace summary and returns structured per-dimension scores.

**Requirements:** R3, R6

**Dependencies:** Unit 1 (briefs), Unit 2 (summarizer)

**Files:**
- Modify: `src/opentraces/quality/judge.py` (add judge logic to same module)
- Modify: `src/opentraces/quality/types.py` (add `JudgeDimension`, `JudgeResult` types)
- Test: `tests/test_judge.py`

**Approach:**
- `JudgeDimension`: name, score (1-5), rationale (short string)
- `JudgeResult`: persona_name, dimensions list, overall_score (weighted), model_used, skipped (bool)
- `run_judge(persona_name, trace_summary, model="haiku")` function:
  1. Load the persona brief from `briefs/{persona_name}.md`
  2. Parse YAML frontmatter for dimension definitions
  3. Build system prompt: "You are a {persona description}. Evaluate this trace..." + dimension scoring guide
  4. Build user prompt: serialized trace summary
  5. Call Claude API, parse JSON response
  6. Return `JudgeResult`
- Graceful degradation: if `ANTHROPIC_API_KEY` not set or call fails, return `JudgeResult(skipped=True)` with all dimensions scored at 0 and a note

**Patterns to follow:**
- Use `anthropic` SDK directly (already in project environment per CLAUDE.md)
- JSON mode via system prompt instruction ("respond with JSON only") rather than tool_use, keeping it simple

**Test scenarios:**
- Mock API call returns valid JSON with expected dimensions
- Missing API key produces `skipped=True` result
- Malformed API response (wrong dimension count, missing fields) is handled gracefully
- Each persona's brief produces a valid judge prompt

**Verification:**
- Judge returns structured scores for all dimensions defined in the brief
- Graceful degradation works without API key

---

- [ ] **Unit 4: Hybrid scoring in engine.py**

**Goal:** Integrate LLM judge scores alongside deterministic scores in the assessment engine.

**Requirements:** R4, R6

**Dependencies:** Unit 3

**Files:**
- Modify: `src/opentraces/quality/engine.py` (extend `assess_trace`, `PersonaScore`)
- Modify: `src/opentraces/quality/types.py` (extend `PersonaScore` with judge fields)
- Test: `tests/test_judge.py`

**Approach:**
- Add `judge_score: float | None` and `judge_result: JudgeResult | None` to `PersonaScore`
- In `assess_trace()`, after running deterministic checks, optionally run the LLM judge
- New parameter: `enable_judge: bool = False` (opt-in, not default)
- When judge is enabled: `hybrid_score = 0.6 * deterministic_score + 0.4 * judge_score`
- When judge is skipped: `hybrid_score = deterministic_score` (no penalty)
- `overall_utility` uses hybrid scores when available
- Report generation (`generate_report()`) includes judge dimensions when present

**Patterns to follow:**
- Same optional-enrichment pattern as preservation (run when path is provided, skip otherwise)

**Test scenarios:**
- `enable_judge=False` produces identical scores to current behavior (backward compatibility)
- `enable_judge=True` with mocked judge produces hybrid scores
- `enable_judge=True` with judge failure degrades to deterministic-only

**Verification:**
- Existing test suite passes unchanged when `enable_judge=False`
- New tests cover hybrid scoring math

---

- [ ] **Unit 5: Fix known deterministic check bugs**

**Goal:** Fix the five known bugs in existing checks, improving deterministic baseline accuracy.

**Requirements:** R2

**Dependencies:** None (can be done in parallel with Units 1-4)

**Files:**
- Modify: `src/opentraces/quality/gates.py` (fix 0.0 average bug)
- Modify: `src/opentraces/quality/personas/analytics.py` (A5 cache_read_tokens)
- Modify: `src/opentraces/quality/personas/domain.py` (D8 skip not_yet_implemented)
- Modify: `src/opentraces/quality/conformance.py` (ST3 actual check)
- Modify: `src/opentraces/quality/personas/training.py` (T10 real warmup verification)
- Test: `tests/test_persona_rubrics.py`

**Approach:**
- `gates.py`: Change `if avg > 0` to `if avg >= 0` (or restructure the condition)
- `analytics.py` A5: Add `cache_read_tokens > 0` to the check as the docstring promises
- `domain.py` D8: Skip with score=1.0 and note "not yet implemented" (like D2's N/A pattern) until `environment.os` is populated by the parser
- `conformance.py` ST3: Actually verify `system_prompt_hash` references in steps resolve to entries in `system_prompts` dict
- `training.py` T10: Check that warmup-labeled steps exist at the beginning of the trace (not just that any call_type is set)

**Test scenarios:**
- Gate with 0.0 average now correctly fails
- A5 requires all three token fields (input, output, cache_read)
- D8 passes with note when os is not populated
- ST3 fails when a step references a hash not in the dict
- T10 fails when call_type is set but no warmup steps exist at trace start

**Verification:**
- Existing tests updated for new behavior
- No test regressions on other checks

---

- [ ] **Unit 6: CLI integration and report output**

**Goal:** Wire up the hybrid assessment to the CLI and test harness with proper report output to `.gstack/qa/`.

**Requirements:** R5

**Dependencies:** Unit 4

**Files:**
- Modify: `src/opentraces/cli.py` (add `--judge` flag to relevant commands)
- Modify: `tests/test_harness_integration.py` (optional judge pass)
- Modify: `tests/test_e2e_dogfood.py` (optional judge pass)

**Approach:**
- Add `--judge` / `--no-judge` flag to `opentraces assess` (or equivalent CLI command)
- When `--judge` is passed, enable LLM judge in the assessment pipeline
- Reports include a "Judge Assessment" section when judge was run
- Test harness tests can optionally enable judge via environment variable `OPENTRACES_ENABLE_JUDGE=1`

**Patterns to follow:**
- Same flag pattern as `--tier` for security level selection

**Test scenarios:**
- `opentraces assess --judge` produces reports with judge sections
- `opentraces assess` (default) produces deterministic-only reports
- Reports land in `.gstack/qa/` with timestamps

**Verification:**
- CLI help shows the new flag
- Reports are written to correct location with correct format

## System-Wide Impact

- **Backward compatibility:** All existing tests pass unchanged. The judge is opt-in via flag.
- **API dependency:** The LLM judge introduces a runtime dependency on the Anthropic API, but only when explicitly enabled. No API call happens by default.
- **Cost:** Each judge run costs ~$0.001-0.01 per trace (Haiku). A 10-trace batch costs pennies. Sonnet is ~10x more.
- **Report format:** Reports gain a new section when judge is enabled but the existing sections are unchanged.
- **Gate thresholds:** Not changed in this PR. May need recalibration after seeing hybrid scores in practice.

## Risks & Dependencies

- **LLM judge consistency:** LLM scores may vary between runs. Mitigated by using temperature=0 and structured output constraints. Accept ~0.5 point variance on a 1-5 scale.
- **Prompt engineering iteration:** The initial judge prompts may not produce well-calibrated scores. Plan for 2-3 rounds of prompt refinement based on manual review of judge output vs human assessment.
- **Anthropic SDK availability:** The project already uses the Anthropic ecosystem. If the SDK is not installed, the judge gracefully degrades.

## Sources & References

- **Origin document:** [kb/plans/2026-03-28-001-feat-persona-rubric-harness-plan.md](../../kb/plans/2026-03-28-001-feat-persona-rubric-harness-plan.md)
- ADP paper (Song et al., ICLR 2026): 80% reasoning coverage threshold, cross-task transfer validation
- TRACE/WWW'26: Hierarchical Trajectory Utility Function, high-score illusion problem
- AgentPRM (Nov 2025): Step-level promise + progress for process reward models
- AgentDiet (Sept 2025): 40-60% of trajectory tokens are waste
- ICSE 2026 (Mirchandani et al.): Failed trajectories are longer with higher variance
- Kobe Chen trace analysis: Cache hit rate as architectural fingerprint, 81% cost savings
- Nebius scaling law: 2.9x improvement from quantity of standardized data
- CoderForge: SFT training boosted SWE-Bench from 23% to 59.4%
