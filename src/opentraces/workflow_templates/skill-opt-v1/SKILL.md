---
name: skill-opt-v1
description: Project already-captured traces into scored-rollout rows for the SkillOpt optimizer.
mode: agent-skill
requires:
  - ot workflow optimize
  - ot trace query
---

# skill-opt-v1

Scored-rollout projection for the SkillOpt text-space skill optimizer
(arXiv 2605.23904; design in `kb/br/66-skillopt-text-space-skill-optimizer.md`).

`scripts/build_rows.py` reads each already-captured trace from the local bucket,
scores it with `opentraces.quality.engine.assess_trace`, and emits one row per
trace. The trace's `overall_utility` (in [0,1] after scaling) is the rollout
`score`; the failed and passed quality checks become `failure_tags` and
`success_tags`, which the optimizer's reflection step and the held-out
validation gate consume.

This workflow is the forward-pass-over-captured-evidence layer. It does not run
an agent; the live re-rollout of a candidate skill is a later slice. The
`opentraces workflow optimize` consumer reads these rows, splits them into a
train and a held-out selection set by trace-id hash, proposes bounded
add/delete/replace edits, accepts only strictly-improving candidates, and
exports `best_skill.md` plus `edit_apply_report.json`.

## Row shape

```jsonc
{
  "trace_id": "…",
  "project_slug": "…",
  "session_id": "…",
  "score": 0.62,            // overall_utility / 100, the rollout score
  "overall_utility": 62.0,
  "failure_tags": ["rl.RL1_grounded_outcome", "training.T3_clean_diff"],
  "success_tags": ["conformance.C1_schema_version"],
  "summary": "task description (truncated)",
  "skill_digest": null
}
```

## Scope

The run packet's `scope` may carry `project_slug` (or `project`) to narrow the
bucket scan to one project; otherwise every project's traces are projected.
