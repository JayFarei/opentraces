# Skill Verifier Factory — how it works (multi-skill run)

Generated from a real-bucket run over five skills (workflow `verifier-factory-multiskill`,
~1,000 trace records loaded per skill).

## The pipeline

Captured `skill_invocation` traces are projected into `EpisodeEvidence` (intent/summary
text, files touched, the real shell commands — classified into command *families* so
Codex's `exec_command` opacity is resolved — plus `committed` and the outcome). An
**archetype** is pure data: `contract_elements[]`, each carrying a `marker`, a `weight`,
and a declarative `detectors` object (`text_any` / `command_families` / `file_globs` /
`tool_any` / `command_any` / `has_files` / `has_commands` / `committed` /
`outcome_reward_min` / `outcome_label_in`). A fixed interpreter (`detector_matches`, **no
`eval`, no callables**) marks each element present when ANY populated field matches. Per
episode that yields present/deficient markers and a weighted `contract_completeness`. The
**reward is binary and computed in packaging, not the spec**: a trace is a verifier
*success* only if `completeness ≥ 0.999`, else it's a failure that teaches its specific
deficient markers. Traces are split leakage-safe into Dtrain/Dsel/Dtest (a trace never
spans splits), then run through the real SkillOpt loop — strict Dsel gate (accept only
strict improvement) + held-out Dtest — emitting a verifier package (`spec.yaml`,
`scorer.py`, fixtures, rows, report).

## Results (real bucket)

| Skill | Archetype | usable eps | examples / counter | Dsel | Dtest | recommended | addressable markers |
|---|---|---|---|---|---|---|---|
| goal-forge | goal_contract_downstream_outcome_v1 | 67 | 12 / 55 | 0.000 → 1.000 | 1.000 | ✅ | constraint_preservation, honest_stop, verification_surface |
| tdd | tdd_red_green_refactor_v1 | 48 | 0 / 48 | 0.000 → 1.000 | 1.000 | ✅ | green_pass, red_first, refactor |
| review | review_grounded_findings_v1 | 16 | 6 / 10 | 0.000 → 1.000 | 1.000 | ✅ | cite_file_line, grounded_findings |
| docs-update | docs_update_reflects_change_v1 | 43 | 3 / 40 | 0.000 → 1.000 | 1.000 | ✅ | changelog, code_grounded, no_stale |
| architecture-patterns | generic_skill_outcome_v1 | 49 | 18 / 31 | 0.000 → 1.000 | 1.000 | ❌ (generic) | generic.produced_changes, generic.verification_run |

The `examples / counter` split is the mined signal: e.g. all 48 `tdd` traces are
counterexamples (none satisfied the full red→green→refactor contract), so every element is
a learnable deficiency; `review` shows the dominant gap is uncited findings
(`cite_file_line`).

## Why `architecture-patterns` is not recommended

It has no curated archetype, so it falls back to the **generic, skill-agnostic** archetype
(markers namespaced `rl.architecture-patterns.generic.*`). Generic archetypes are proposed
from universal trace signal families and are an advisory, human-review **draft** — they are
*never* auto-recommended even when the gate plumbing passes (Dsel/Dtest = 1.0), because
broad, text-adjacent markers are the highest reward-hacking risk (br/56).

## Addressable markers come only from the TRAIN failure minibatch

The markers the optimizer is allowed to enforce are the deficiencies observed on
**low-reward Dtrain traces only** — never Dsel/Dtest. This keeps the held-out gate honest
(it can't be gamed by enforcing markers derived from the traces it scores) and is why the
binary full-contract reward matters: a mostly-good trace (e.g. completeness 0.71) that
drops one element is still a *failure for that element* and teaches it, instead of being
washed out as "good enough."

## Trust boundary

The agent **PROPOSES** a declarative spec; the factory **SCORES** it mechanically (a spec
cannot set `reward`/`gate`/`split` — the validator rejects unknown keys — and
over-permissive detectors are flagged and never auto-recommended); a human **APPROVES**
promotion (always `manual_required_default_off`).

## Verifier-creator skill loop

`list_candidates` (which markers are deficient) → `get_skill_examples` (real example /
counterexample traces, pulled with `opentraces trace slice/get`) → `draft_archetype`
(editable starting point) → `author_archetype` (validate + lint the declarative spec) →
`score_authored` (run the deterministic gate, emit the package for human approval).
