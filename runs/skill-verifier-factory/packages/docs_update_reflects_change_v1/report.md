# Verifier package — docs_update_reflects_change_v1

- **Skill**: `docs-update`
- **Semantic target**: code change -> docs edit: do the docs reference the real changed code, update the changelog/readme, and remove stale references (not drift out of date)?
- **Split**: Dtrain=24 Dsel=9 Dtest=10
- **Dsel**: 0.000 -> 1.000
- **Dtest (held-out)**: 1.000
- **Accepted edits**: 1 (rejected 0)
- **Promotion**: manual_required_default_off (automatic_promotion=False)

## Addressable markers (trace-grounded failure modes)

- `rl.docs-update.changelog`
- `rl.docs-update.no_stale`

## Graders

- `deterministic_marker_coverage` (deterministic, default-on (CI)) — CI-safe: fraction of trace-grounded contract markers the skill addresses.
- `docs_freshness_llm_rubric` (llm_rubric, opt-in) — LLM-graded rubric scoring whether the docs edit matches the diff and leaves no stale references. Opt-in.

## Verifier-creator decisions

- **grounding_floor** [inferred_default]: Yes — code_grounded is high-weight; ungrounded docs edits are a failure mode.
- **stale_policy** [inferred_default]: Yes — no_stale is high-weight; leaving outdated docs is the common failure.

## Limitations

- none
