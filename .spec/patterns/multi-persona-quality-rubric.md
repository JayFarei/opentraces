---
schema_version: "1.0"
title: Multi-Persona Quality Rubric
scope: quality assessment
pattern_type: architectural
transferable: true
---

# Multi-Persona Quality Rubric

## Overview

Quality assessment is organized as multiple independent "personas," each evaluating the same data from a different consumer's perspective. Each persona defines a set of weighted check functions that return pass/fail with a 0-1 score and evidence text. An engine aggregates checks into per-persona rubric scores (0-100), and an optional LLM judge provides qualitative scoring that blends with the deterministic results.

## How It Works

1. **PersonaDef** declares a name, description, and list of **CheckDef** entries
2. Each **CheckDef** wraps a callable `(TraceRecord, dict) -> CheckResult` with a weight (0.0-1.0) and category label
3. The engine runs all checks for all personas against each trace, catching exceptions to avoid one bad check poisoning the batch
4. Scores are computed as `sum(score * weight) / sum(weight) * 100` per persona
5. An optional LLM judge reads persona-specific brief files (YAML frontmatter + markdown prose) and scores qualitative dimensions on a 1-5 scale
6. Hybrid blending: `0.6 * deterministic + 0.4 * judge` when the judge runs

Five personas in this project: Conformance (structural validity), Training/SFT (fine-tuning fitness), RL (reward modeling fitness), Analytics (observability completeness), Domain (dataset discovery metadata).

## Key Files

- `src/opentraces/quality/types.py` - CheckResult, CheckDef, PersonaDef, RubricItem, RubricReport
- `src/opentraces/quality/engine.py` - assess_trace, assess_batch, score computation
- `src/opentraces/quality/conformance.py` - 27 structural checks
- `src/opentraces/quality/personas/training.py` - 10 SFT fitness checks (exemplar)
- `src/opentraces/quality/judge.py` - LLM judge with persona briefs
- `src/opentraces/quality/personas/briefs/` - YAML+markdown rubric definitions for the judge

## How to Replicate

1. Define a `CheckResult` dataclass with `passed`, `score`, `evidence` fields
2. Define a `CheckDef` that pairs a check callable with a weight and category
3. Define a `PersonaDef` that groups related checks under a consumer persona name
4. Write check functions as pure functions: `(record, context) -> CheckResult`
5. Name checks with a prefix convention (e.g., `_t1_`, `_d1_`, `_rl1_`) for traceability
6. Build an engine that iterates personas and checks, catches exceptions, computes weighted scores
7. Optionally add an LLM judge layer that reads persona-specific briefs and blends scores

## When to Use

- Data pipeline output needs quality scoring from multiple consumer perspectives
- You want both deterministic (fast, cheap, reproducible) and qualitative (LLM-based) evaluation
- Quality gates need per-persona thresholds (e.g., training fitness > 80, analytics > 60)
- The system serves multiple downstream consumers with different quality definitions

## When NOT to Use

- Single-consumer data pipelines where one quality score suffices
- When all quality checks are binary pass/fail with no gradation
- When LLM judge latency is unacceptable and deterministic checks alone are insufficient
