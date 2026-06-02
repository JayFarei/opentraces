# skill-verifier-candidates-v1 tests

Coverage for this template lives in the repo test suite:

- `tests/consumers/test_verifier_factory.py` exercises
  `mine_verifier_candidates` (the function this template wraps) and validates rows
  against `opentraces.skill_verifier_candidates.v1`.
- `tests/consumers/test_verifier_factory_workflow.py` runs this template end-to-end
  through `execute_workflow` and validates emitted rows against
  `schemas/row.schema.json`.
