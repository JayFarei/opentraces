# Verifier package — tdd_red_green_refactor_v1

- **Skill**: `tdd`
- **Semantic target**: red (failing test first) -> green (make it pass) -> refactor, with the test as the executable intent and a landed verified outcome.
- **Split**: Dtrain=25 Dsel=12 Dtest=11
- **Dsel**: 0.000 -> 1.000
- **Dtest (held-out)**: 1.000
- **Accepted edits**: 1 (rejected 0)
- **Promotion**: manual_required_default_off (automatic_promotion=False)

## Addressable markers (trace-grounded failure modes)

- `rl.tdd.refactor`

## Graders

- `deterministic_marker_coverage` (deterministic, default-on (CI)) — CI-safe: fraction of trace-grounded contract markers the skill addresses.
- `tdd_agent_rerollout` (agent_rerollout, opt-in) — Re-roll an agent on the task and assert a test was added and fails before the fix, then passes after. Opt-in, real agent.

## Verifier-creator decisions

- **cycle_strictness** [inferred_default]: Yes — red_first is the highest-weight element; test-before-impl ordering is the signal.
- **refactor_required** [inferred_default]: Partial credit without it; full credit needs all three phases.

## Limitations

- none
