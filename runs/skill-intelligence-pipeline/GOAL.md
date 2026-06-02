# Goal: Skill Intelligence Pipeline Closeout

Pasteable goal:

```text
/goal Land the remaining Skill Intelligence pipeline from the repaired OpenTraces corpus through a verified SkillOpt case study: retained TraceRecords and Trace Index latest-generation skill_invocation units must drive skill-episodes-v1 rows, leakage-safe skill-eval-tasks-v1 rows, otbox-backed skill-rollouts-v1 rows, and a SkillOpt skill-update-report-v1 for the selected goal-forge pack. Done means repair/reparse is idempotent or safely bounded, the corpus audit reports the repaired latest-generation skill usage with no wildcard false positives, the deterministic pipeline runs audit -> episodes -> eval tasks -> Dtrain/Dsel/Dtest split -> otbox synthetic rerollouts -> SkillOpt Harness optimization -> accepted/rejected candidate report, and the committed case study under runs/skill-intelligence-pipeline/case-study/ shows baseline/candidate digests, split counts, Dsel gate result, held-out Dtest score, rollout transcript refs, and human-approval/default-off promotion state. Verified by schema/workflow/unit/integration tests for skill-episodes-v1, skill-eval-tasks-v1, skill-rollouts-v1, and skill-update-report-v1; focused core skill-detection, ingest supersession, Trace Index, Claude, and Codex regressions; `pytest tests/test_skill_opt.py -q`; a default-CI otbox journey in `make otbox-journeys`; the dry-run pipeline command surfacing the report path and Dtest score; and `pytest tests/ -q` with only documented environment-bound skips. Preserve additive-only schema policy, existing TraceRecord/bucket/Trail/Context Tree write contracts, dataset review/publish semantics, SkillOpt Algorithm 1 plus Appendix C.2/C.3 prompt-contract fidelity, frozen target agent/harness discipline, security-default-off posture, no `src/` imports from `tests/`, no network/live-agent requirement in default CI, no otbox end-user product surface, and no automatic skill promotion. Use only the relevant core skill-detection/index read side, ingest repair plumbing, workflow templates, dataset/workflow CLI plumbing, skill_opt consumer/harness modules, tests including tests/otbox, and runs/skill-intelligence-pipeline artifacts. Log each attempt to runs/skill-intelligence-pipeline/log.md with phase, diff, evidence observed, split/leakage notes, case-study artifact updates, and next-step rationale. On block, append a BLOCKED entry to that log with attempted paths, evidence gathered, the blocker, and the input that would unlock progress; if the current bucket/corpus is insufficient or a real Claude/Codex binary/auth is unavailable, block only that leg while keeping the deterministic CI case study complete.
```

Audit:

Outcome ✓ | Verification ✓ | Constraints ✓ | Boundaries ✓ | Iteration ✓ | Blocked ✓

Character count: 2612 / 4000.

Run log: `runs/skill-intelligence-pipeline/log.md`

Plan spine: `runs/skill-intelligence-pipeline/PLAN.md`
