# Verifier package — goal_contract_downstream_outcome_v1

- **Skill**: `goal-forge`
- **Semantic target**: messy request -> generated goal -> downstream agent rollout: did the agent preserve constraints, verify correctly, avoid drift, and stop honestly?
- **Split**: Dtrain=28 Dsel=23 Dtest=16
- **Dsel**: 0.000 -> 1.000
- **Dtest (held-out)**: 1.000
- **Accepted edits**: 1 (rejected 0)
- **Promotion**: manual_required_default_off (automatic_promotion=False)

## Addressable markers (trace-grounded failure modes)

- `rl.goal-forge.constraint_preservation`
- `rl.goal-forge.honest_stop`

## Graders

- `deterministic_marker_coverage` (deterministic, default-on (CI)) — CI-safe: fraction of trace-grounded contract markers the skill addresses.
- `downstream_agent_rerollout` (agent_rerollout, opt-in) — Re-roll a downstream agent against the generated goal and score whether it preserved constraints / verified / stopped honestly. Opt-in, real agent.
- `goal_contract_llm_rubric` (llm_rubric, opt-in) — LLM-graded rubric over the produced goal's contract completeness. Opt-in.

## Verifier-creator decisions

- **success_definition** [inferred_default]: Goal carries outcome+verification+constraints+honest-stop AND downstream work landed/verified.
- **downstream_signal** [inferred_default]: Trace outcome_reward (committed + verified + Trail survival).
- **drift_policy** [inferred_default]: Yes — missing constraint_preservation is a deficient marker regardless of landing.

## Limitations

- none
