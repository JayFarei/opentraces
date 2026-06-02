# Verifier package — generic_skill_outcome_v1

- **Skill**: `architecture-patterns`
- **Semantic target**: did the skill invocation read context, produce verified changes, and land an honest (non-abandoned) outcome?
- **Split**: Dtrain=23 Dsel=9 Dtest=17
- **Dsel**: 0.000 -> 1.000
- **Dtest (held-out)**: 1.000
- **Accepted edits**: 1 (rejected 0)
- **Promotion**: manual_required_default_off (automatic_promotion=False)

## Addressable markers (trace-grounded failure modes)

- `rl.architecture-patterns.generic.produced_changes`
- `rl.architecture-patterns.generic.verification_run`

## Graders

- `deterministic_marker_coverage` (deterministic, default-on (CI)) — CI-safe: fraction of trace-grounded contract markers the skill addresses.
- `generic_agent_rerollout` (agent_rerollout, opt-in) — Re-roll the agent on the task and score whether it read context, ran verification, and landed. Opt-in, real agent.

## Verifier-creator decisions

- **outcome_floor** [inferred_default]: Read context + produced changes that ran verification and landed.
- **verification_required** [inferred_default]: Yes — verification_run is high-weight; partial credit without it.
- **promote_to_curated** [inferred_default]: Pending human review — generic drafts are advisory until approved.

## Limitations

- none
