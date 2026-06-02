# Verifier package — review_grounded_findings_v1

- **Skill**: `review`
- **Semantic target**: findings grounded in the actual diff/code (file:line citations to real artifacts), not hallucinated, with a verdict consistent with the outcome.
- **Split**: Dtrain=10 Dsel=5 Dtest=1
- **Dsel**: 0.000 -> 1.000
- **Dtest (held-out)**: 1.000
- **Accepted edits**: 1 (rejected 0)
- **Promotion**: manual_required_default_off (automatic_promotion=False)

## Addressable markers (trace-grounded failure modes)

- `rl.review.cite_file_line`

## Graders

- `deterministic_marker_coverage` (deterministic, default-on (CI)) — CI-safe: fraction of trace-grounded contract markers the skill addresses.
- `review_groundedness_llm_rubric` (llm_rubric, opt-in) — LLM-graded rubric scoring each finding's groundedness against the diff. Opt-in.

## Verifier-creator decisions

- **grounding_floor** [inferred_default]: Each finding must reference a file present in files_touched.
- **verdict_consistency** [inferred_default]: Yes — a 'looks good' verdict on reverted work is a deficient marker.

## Limitations

- none
