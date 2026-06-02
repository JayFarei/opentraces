# Context Warm-Up Prompt

Use this prompt to warm a fresh agent before pasting `GOAL.md`.

```text
You are continuing the Skill Intelligence Pipeline closeout in `/Users/jayfarei/src/tries/community-traces-skillopt` on branch `feat/skillopt-native-slice`. Use the repo `.venv` and absolute paths.

Start by reading:

1. `/Users/jayfarei/src/tries/community-traces-skillopt/runs/skill-intelligence-pipeline/HANDOFF.md`
2. `/Users/jayfarei/src/tries/community-traces-skillopt/runs/skill-intelligence-pipeline/PLAN.md`
3. `/Users/jayfarei/src/tries/community-traces-skillopt/runs/skill-intelligence-pipeline/GOAL.md`
4. `/Users/jayfarei/src/tries/community-traces-skillopt/runs/skill-intelligence-pipeline/log.md`
5. `/Users/jayfarei/src/tries/community-traces-skillopt/runs/skill-intelligence-pipeline/corpus-audit.json`
6. `/Users/jayfarei/src/tries/community-traces-skillopt/runs/skill-intelligence-pipeline/case-study/ci-echo/skill-update-report-v1.json`

Current state: the missing skill invocation count was repaired. The retained corpus now supports real Skill Intelligence work from Trace Index latest-generation `skill_invocation` units. Verified facts: 366 raw sessions scanned, 26 parser quality-gate skips, 0 ingest errors, 1027 retained trace files after additive repairs, 212 latest-generation skill invocation units across 116 traces, agent split `codex-cli=158` and `claude-code=54`, no `skill.name="*"` false positives, `corpus-audit.json` reports `total_skill_invocation_units=212`, the dry-run selected `goal-forge`, split counts are `Dtrain=14`, `Dsel=4`, `Dtest=12`, `dtest_score=1.0`, and promotion is `default_off`.

Important code context: Codex skill body-read extraction is in `src/opentraces/capture/codex_cli/parse.py`; shared detection is in `src/opentraces/core/skill_detection.py`; ingest repair/supersession behavior is in `src/opentraces/core/ingest.py`; repeated init/import repair plumbing is in `src/opentraces/cli/__init__.py`; Trace Index skill units consume the shared detector. Attempt 8 in `runs/skill-intelligence-pipeline/log.md` records the corpus repair facts.

The immediate next action is to make repair/reparse idempotent or safely bounded. Repeated trace-record-only repair currently opens additive new generations on an `auto` project; latest-generation queries collapse these because new records carry supersession metadata, but rerunning repair still grows local state. Fix that first by refreshing the latest generation when source bytes are unchanged and the caller explicitly requests repair/reparse, while preserving additive new-generation behavior for real resumed sessions after terminal statuses.

After idempotence is fixed, inspect and tighten the case-study report quality, then run the Skill Intelligence dry-run, `pytest tests/test_skill_opt.py -q`, `make otbox-journeys`, and finally `pytest tests/ -q` with only documented environment-bound skips.

Keep constraints tight: Skill Intelligence must remain an OpenTraces-native consumer of retained TraceRecords and Trace Index skill_invocation units; do not build a parallel skill ledger; no `src/` imports from `tests/`; no network/live-agent requirement in default CI; otbox stays internal/default-off; preserve additive-only schema policy and existing TraceRecord/bucket/Trail/Context Tree write contracts; no automatic skill promotion. Append every attempt to `runs/skill-intelligence-pipeline/log.md`.
```
