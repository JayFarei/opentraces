# Run Log: Skill Intelligence Eval Pipeline

Worktree: `/Users/jayfarei/src/tries/community-traces-skillopt`

Branch at creation: `feat/skillopt-native-slice`

Plan spine: `runs/skill-intelligence-pipeline/PLAN.md`

Goal: `runs/skill-intelligence-pipeline/GOAL.md`

## Attempt 0 — 2026-05-27

Phase: planning / handoff context.

Change: Created the durable plan spine and pasteable goal for the Skill Intelligence Eval Pipeline. The plan frames the work as a platform-native SkillOpt implementation: usage traces become reviewed skill episodes, episodes become leakage-safe eval tasks, otbox augments tasks into rollouts, and SkillOpt proposes human-approved skill updates through held-out Dsel/Dtest gates.

Evidence: Existing SkillOpt implementation completed Plan 084; current bucket case study showed only four scored rollout rows, so the next plan centers the missing data/eval substrate rather than more optimizer mechanics.

Decision: Next agent should start at Phase 0/1 in `PLAN.md`, choose the first skill pack, then implement skill identity capture/backfill and `skill-episodes-v1` as the first vertical slice.

## Attempt 1 — 2026-05-27

Phase: prototype / core skill detection.

Change: Added `opentraces.core.skill_detection` as the shared normalizer for skill invocation evidence and refactored Trace Index skill units to consume it. The detector now recognizes Claude slash-command metadata (`name`, `source=claude_slash_command`) and Codex skill tool calls/metadata (`skill_name`, `source=codex_cli_tool_call`) without double-counting a Codex tool call that also has parser metadata. This keeps Skill Intelligence as a consumer of the core trace substrate rather than creating a parallel skill ledger.

Evidence:
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest /Users/jayfarei/src/tries/community-traces-skillopt/tests/core/test_skill_detection.py -q` -> 3 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest /Users/jayfarei/src/tries/community-traces-skillopt/tests/core/test_trace_index_plan056.py::test_rebuild_index_uses_metadata_skill_invocations /Users/jayfarei/src/tries/community-traces-skillopt/tests/core/test_trace_index_plan056.py::test_rebuild_index_uses_codex_skill_name_invocations -q` -> 2 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest /Users/jayfarei/src/tries/community-traces-skillopt/tests/capture/test_parser_codex_cli_advanced.py::test_skill_invocation_metadata_does_not_pollute_task_intent /Users/jayfarei/src/tries/community-traces-skillopt/tests/capture/test_parser_claude_code.py::TestClaudeCodeParser::test_slash_skill_command_records_skill_metadata -q` -> 2 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest /Users/jayfarei/src/tries/community-traces-skillopt/tests/core/test_trace_index_plan056.py -q` -> 19 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m ruff check /Users/jayfarei/src/tries/community-traces-skillopt/src/opentraces/core/skill_detection.py /Users/jayfarei/src/tries/community-traces-skillopt/src/opentraces/core/trace_index.py /Users/jayfarei/src/tries/community-traces-skillopt/tests/core/test_skill_detection.py /Users/jayfarei/src/tries/community-traces-skillopt/tests/core/test_trace_index_plan056.py` -> all checks passed.
- `git -C /Users/jayfarei/src/tries/community-traces-skillopt diff --check` -> clean.

Decision: Next slice should build the Skill Intelligence dataset consumer on top of this normalized Trace Index surface: query skill invocation units, attach outcome/review signals from existing trace artifacts, and only add core schema/storage where the existing trace substrate lacks a first-class field.

## Attempt 2 — 2026-05-27

Phase: planning / goal-forge.

Change: Replaced the initial high-level plan and goal with a full execution spine for landing the remaining Skill Intelligence work as an OpenTraces-native consumer. The plan now explicitly starts from core skill detection and Trace Index skill units, then sequences corpus audit, `skill-episodes-v1`, `skill-eval-tasks-v1`, `skill-rollouts-v1`, SkillOpt report generation, OT workflow/dataset wiring, and deterministic plus opt-in real case-study artifacts.

Evidence:
- Read `goal-forge` and updated `runs/skill-intelligence-pipeline/GOAL.md` into a six-slot pasteable `/goal` with outcome, verification, constraints, boundaries, iteration logging, and blocked stop condition.
- Checked existing workflow/dataset surfaces: `skill-command-trajectory-eval-v1`, `skill-opt-v1`, `opentraces workflow optimize`, `opentraces dataset new/run/review`, and the Plan 084 otbox/SkillOpt artifacts.
- Verified the goal text is 2423 / 4000 characters.
- `git -C /Users/jayfarei/src/tries/community-traces-skillopt diff --check -- runs/skill-intelligence-pipeline/PLAN.md runs/skill-intelligence-pipeline/GOAL.md runs/skill-intelligence-pipeline/log.md` -> clean.

Decision: Next implementation turn should begin Phase 1 by adding the corpus audit over Trace Index `skill_invocation` units and producing `runs/skill-intelligence-pipeline/corpus-audit.json`, then use that evidence to choose the case-study skill pack or log a data gap.

## Attempt 3 — 2026-05-27

Phase: final context packaging / paper check.

Paper cross-check: arXiv 2605.23904v2 Section 3.1-3.7, Appendix B, Appendix C.1-C.3, and Appendix C.2 prompt contracts. Confirmed the continuation plan includes the required frozen target agent/harness, single trainable skill artifact, deterministic Dtrain/Dsel/Dtest split, selection-only gate, final Dtest reporting, rollout evidence, failure/success minibatches, bounded patch edits, textual LR, rejected buffer, epoch-boundary slow/meta update, optimizer-side meta-skill, structured JSON prompt contracts, patch safeguards, and reliable verifier/reward requirement.

Experience cross-check: carried forward the local evidence that Plan 084 is complete; the existing-bucket run is only a four-row smoke case study; Claude and Codex skill detection must flow through `opentraces.core.skill_detection`; otbox must stay deterministic/default-off in CI; real-agent evidence is opt-in; and the next slice must begin with a corpus audit rather than assuming there is enough data.

Change: Added `runs/skill-intelligence-pipeline/HANDOFF.md` and `runs/skill-intelligence-pipeline/CONTEXT_WARMUP_PROMPT.md`; updated `PLAN.md` with a final paper/experience checklist; tightened `GOAL.md` to explicitly preserve Algorithm 1 plus Appendix C.2/C.3 prompt-contract fidelity.

Evidence:
- arXiv source checked: `https://arxiv.org/abs/2605.23904` and `https://arxiv.org/html/2605.23904v2`.
- Goal text length checked at 2501 / 4000 characters.
- `git -C /Users/jayfarei/src/tries/community-traces-skillopt diff --check -- runs/skill-intelligence-pipeline/PLAN.md runs/skill-intelligence-pipeline/GOAL.md runs/skill-intelligence-pipeline/HANDOFF.md runs/skill-intelligence-pipeline/CONTEXT_WARMUP_PROMPT.md runs/skill-intelligence-pipeline/log.md` -> clean.

Decision: Context package is ready. Next implementation turn should paste `GOAL.md`, read `HANDOFF.md`, and start Phase 1 corpus audit.

## Attempt 4 — 2026-05-27

Phase: implementation / deterministic Skill Intelligence pipeline.

Paper cross-check: arXiv 2605.23904v2 Section 3.1 / Algorithm 1, Section 3.2,
Sections 3.3-3.6, Appendix B, Appendix C.2, and Appendix C.3. The new pipeline
keeps the target harness frozen, changes only the external skill text during
optimization, uses Dtrain/Dsel/Dtest before rollout augmentation, accepts only
strict Dsel improvements, reports held-out Dtest, preserves bounded SkillOpt
patch mechanics through the existing engine, and treats low-evidence rows as a
data gap rather than optimization evidence.

Diff/change:
- Added `opentraces.consumers.skill_intelligence` as the native consumer for
  corpus audit, `skill-episodes-v1`, `skill-eval-tasks-v1`, `skill-rollouts-v1`,
  and `skill-update-report-v1` artifacts.
- Added built-in workflow packages and row schemas for `skill-episodes-v1`,
  `skill-eval-tasks-v1`, `skill-rollouts-v1`, and `skill-update-report-v1`.
- Added `opentraces workflow skill-intelligence --dry-run` to run audit ->
  episodes -> eval tasks -> Dtrain/Dsel/Dtest -> synthetic rerollouts ->
  SkillOpt Harness optimization -> update report.
- Added focused tests in `tests/test_skill_intelligence.py`.

Evidence observed:
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/test_skill_intelligence.py -q` -> 3 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m ruff check src/opentraces/consumers/skill_intelligence src/opentraces/cli/workflow.py src/opentraces/workflow_templates/skill-episodes-v1/scripts/build_rows.py src/opentraces/workflow_templates/skill-eval-tasks-v1/scripts/build_rows.py src/opentraces/workflow_templates/skill-rollouts-v1/scripts/build_rows.py src/opentraces/workflow_templates/skill-update-report-v1/scripts/build_rows.py tests/test_skill_intelligence.py` -> all checks passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/core/test_skill_detection.py -q` -> 3 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/core/test_trace_index_plan056.py::test_rebuild_index_uses_metadata_skill_invocations tests/core/test_trace_index_plan056.py::test_rebuild_index_uses_codex_skill_name_invocations -q` -> 2 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/test_skill_opt.py -q` -> 45 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/otbox/test_skillopt_online_harness.py -q` -> 3 passed, 1 skipped.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/opentraces workflow skill-intelligence --dry-run --out runs/skill-intelligence-pipeline --json` -> selected `opentraces`, Dsel 0.0 -> 1.0, Dtest 1.0, split counts Dtrain=18/Dsel=6/Dtest=6.

Split/leakage notes: `skill-eval-tasks-v1` assigns deterministic leakage-key
splits before rollout augmentation; all synthetic rollout variants inherit the
source eval task split. The CI case study uses 30 reviewed episodes and writes
60 rollout rows: 30 baseline plus 30 candidate rerollouts.

Case-study artifact updates:
- `runs/skill-intelligence-pipeline/corpus-audit.json`
- `runs/skill-intelligence-pipeline/case-study/ci-echo/skill-episodes-v1.jsonl`
- `runs/skill-intelligence-pipeline/case-study/ci-echo/skill-eval-tasks-v1.jsonl`
- `runs/skill-intelligence-pipeline/case-study/ci-echo/skill-rollouts-v1.jsonl`
- `runs/skill-intelligence-pipeline/case-study/ci-echo/skill-update-report-v1.json`
- `runs/skill-intelligence-pipeline/case-study/ci-echo/README.md`

BLOCKED: existing-bucket case-study leg only. Attempted the corpus audit through
the dry-run pipeline; evidence shows `total_skill_invocation_units=0`, so the
current local bucket cannot support an evidence-selected existing-bucket
Dtrain/Dsel/Dtest case study. Unlock by capturing Claude/Codex traces with
normalized `skill_invocation` evidence and rerunning the audit.

BLOCKED: real-agent case-study leg only. The deterministic path is complete and
default-CI safe. Live Claude/Codex execution is explicit opt-in and was not run
as part of this default dry-run. Unlock by explicitly running the real-agent leg
with binary/auth and opt-in live execution.

Next-step rationale: run `make otbox-journeys`, full tests, and `git diff --check`;
fix any real regressions before closeout.

## Attempt 5 — 2026-05-27

Phase: closeout verification.

Paper cross-check: rechecked the final artifacts against Algorithm 1 and
Appendix B/C constraints. The committed deterministic case study keeps the
target harness frozen, edits only the candidate skill text, splits eval tasks
before augmentation, gates on Dsel, reports Dtest, and leaves promotion
manual/default-off.

Evidence observed:
- `make otbox-journeys` -> 72 passed in 76.93s, including
  `skillopt-online-loop-echo`.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/ -q -p no:cacheprovider --timeout=120` -> 1 failed, 3037 passed, 168 skipped, 2 xfailed in 1130.73s. The single failure was `tests/perf/test_bucket_performance_gates.py::test_bench_capture_hot_path`, measuring 25.4ms/event against a 25.0ms/event perf budget.
- Isolated rerun:
  `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/perf/test_bucket_performance_gates.py::test_bench_capture_hot_path -q -p no:cacheprovider --timeout=120` -> 1 passed in 1.80s.
- `git diff --check` -> clean.

Split/leakage notes: no split changes after Attempt 4. The committed CI case
study remains Dtrain=18, Dsel=6, Dtest=6.

Case-study artifact updates: no new artifact changes beyond Attempt 4.

Decision: deterministic Skill Intelligence pipeline is landed with one
documented full-suite perf-budget blip that passed on isolated rerun. Existing
bucket and real-agent case-study legs remain scoped blockers; they do not block
the default deterministic case study.

## Attempt 6 — 2026-05-27

Phase: skill-invocation capture-count troubleshooting.

Diff/change:
- Added Codex parser detection for the current real skill-use shape where a
  normal tool call reads `.../.codex/skills/<name>/SKILL.md`,
  `.../.claude/skills/<name>/SKILL.md`, or
  `.../.agents/skills/<name>/SKILL.md`.
- Preserved the existing synthetic `skill_name` tool-call path and added
  per-step/tool/skill dedupe.
- Updated the Codex parser regression fixture for skill-body reads.
- Updated otbox simulated-user hook setup to install the OpenTraces skill into
  the selected harness namespace, not only the project-local `.agents` mirror.
- Updated otbox simulated-user dispatch tests to assert `setup skill --harness`
  runs before project registration.
- Hardened core skill detection so Trace Index rebuilds can detect Codex
  skill-body reads from stored tool-call inputs even when older records lack
  `metadata.skill_invocations`.

Evidence observed:
- Current `runs/skill-intelligence-pipeline/corpus-audit.json` reports
  `total_skill_invocation_units=0`; the earlier low count is therefore a
  capture/index input problem, not a SkillOpt report aggregation issue.
- Current bucket pointers resolve to four Claude records with many tool calls,
  but no `metadata.skill_invocations` and no regex hits for
  `skill_invocations`, `skill_name`, `<command-name>`, `Base directory for this
  skill`, or `<skill_instructions>` in the resolved bucket envelopes.
- `opentraces trace query --candidate-kind skill_invocation --force-rebuild
  --json` against the current bucket returns `total=0`.
- Regex searches over raw local session stores found broad skill evidence:
  Claude command wrappers, Claude skill-body markers, Codex `SKILL.md` reads,
  and Codex function calls reading skill files. That evidence has not flowed
  into the current bucket audit.
- Otbox checkpoint `c-captured-multi-skill` proved the Claude path works:
  audit captured three skill invocations, and skill-specific Trace Index
  queries returned two `skill-alpha` units and one `skill-beta` unit.
- Live Codex otbox capture did not reach skill pickup because `/opt/homebrew/bin/codex`
  version `0.77.0` was launched with host config `model = "gpt-5.5"`, which
  requires a newer Codex CLI. The interrupted box was torn down with
  `tests.otbox down --box otb_504f071a --json`.
- The same failed Codex box showed a harness setup gap before the patch:
  `project/.agents/skills/opentraces/SKILL.md` existed, but
  `home/.codex/skills/opentraces/SKILL.md` did not.
- Parsing the current Codex session after the parser change detects real
  `investigate` skill-body reads from
  `/Users/jayfarei/.claude/skills/gstack/investigate/SKILL.md`.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/capture/test_parser_codex_cli_advanced.py -q`
  -> 4 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/core/test_skill_detection.py tests/core/test_trace_index_plan056.py::test_rebuild_index_uses_codex_skill_name_invocations -q`
  -> 4 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/core/test_skill_detection.py tests/core/test_trace_index_plan056.py::test_rebuild_index_uses_codex_skill_name_invocations tests/core/test_trace_index_plan056.py::test_rebuild_index_uses_codex_skill_body_read_invocations -q`
  -> 6 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/otbox/test_codex_simulated_user_runner.py::test_codex_capture_refresh_dry_run_needs_no_codex_binary -q`
  -> 1 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m pytest tests/otbox/simulated_users/test_runner.py::test_install_hooks_dispatches_codex_cli tests/otbox/simulated_users/test_runner.py::test_install_hooks_dispatches_claude_and_noops_echo -q`
  -> 2 passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m ruff check src/opentraces/capture/codex_cli/parse.py tests/capture/test_parser_codex_cli_advanced.py tests/otbox/simulated_users/runner.py tests/otbox/simulated_users/test_runner.py`
  -> all checks passed.
- `/Users/jayfarei/src/tries/community-traces-skillopt/.venv/bin/python -m ruff check src/opentraces/core/skill_detection.py tests/core/test_skill_detection.py tests/core/test_trace_index_plan056.py`
  -> all checks passed.
- `git diff --check -- src/opentraces/capture/codex_cli/parse.py tests/capture/test_parser_codex_cli_advanced.py tests/otbox/simulated_users/runner.py tests/otbox/simulated_users/test_runner.py runs/skill-intelligence-pipeline/log.md`
  -> clean.
- `git diff --check -- src/opentraces/core/skill_detection.py tests/core/test_skill_detection.py tests/core/test_trace_index_plan056.py`
  -> clean.
- After the core detector fallback, direct structured scanning of the current
  bucket still finds 4 parseable trace records and 0 skill invocations, and
  `opentraces trace query --candidate-kind skill_invocation --force-rebuild
  --json` still returns `total=0`.

Split/leakage notes: no split or leakage-policy changes. This attempt only
changes capture normalization and otbox harness setup so real skill evidence
can become Trace Index `skill_invocation` units.

Case-study artifact updates: no case-study data rows were regenerated. The
current existing-bucket audit still lacks skill-invocation evidence until
relevant raw sessions are ingested or new Claude/Codex sessions are captured
with the fixed paths. The detector can now find skill-body-read evidence if it
is present in stored tool-call inputs, but the current bucket does not contain
that evidence.

BLOCKED: live Codex otbox capture only. Attempted the real Codex scenario, but
the installed Codex CLI rejected the configured `gpt-5.5` model before skill
pickup. Unlock by upgrading Codex CLI or running the otbox Codex scenario with a
model supported by `/opt/homebrew/bin/codex` 0.77.0.

Next-step rationale: rerun an opt-in live Codex capture after the Codex
model/version mismatch is removed, then rebuild the Trace Index and rerun the
corpus audit to confirm nonzero skill-invocation counts from real bucket data.

## Attempt 7 — 2026-05-27

Phase: retained-corpus root-cause analysis for missing skill invocations.

Diff/change: no code changes. This pass was read-only except for this log entry.

Evidence observed:
- The enrolled project is `/Users/jayfarei/src/tries/2026-03-27-community-traces-hf`
  with `.opentraces.json` project id `24eb286be6874af0bd7b9918e94952bc` and
  `agents=["claude-code"]`.
- `/Users/jayfarei/src/tries/community-traces-skillopt` is not an OpenTraces
  project; `opentraces --json status --limit 0` there returns
  `PROJECT_NOT_OPTED_IN`.
- `~/.opentraces/config.json` currently contains only `{"config_version":
  "0.2.0"}`; `opentraces --json doctor` reports `opted_in_projects.count=0`.
- Current Claude/Codex capture hooks are not installed: doctor reports
  `claude-code.installed=false`, `codex-cli.installed=false`; direct inspection
  of `~/.claude/settings.json` shows no OpenTraces hook command and
  `~/.codex/hooks.json` is missing.
- Project status for `/Users/jayfarei/src/tries/2026-03-27-community-traces-hf`
  shows exactly 4 retained trace sessions, all task text `do we know how to
  slice trajectory per ...`.
- Bucket status reports `trace_records.object_count=4`,
  `raw_sources.object_count=4`, and Trace Index doctor reports
  `source_trace_files=4`, `trace_count=4`, `unit_count=923`.
- The bucket has 8 trace JSON files: 4 trace-record objects and 4
  `current.json` pointers. The 4 records all share session id
  `c4a8168f-295f-4079-acbe-a1d5e2c61090`, agent `claude-code`, and have no
  `metadata.skill_invocations`.
- `~/.opentraces/projects/2026-03-27-community-traces-hf-24eb286b/state.json`
  records only one session under `sessions`: `c4a8168f-...`; the sole ingest
  lock is also `c4a8168f-....lock`.
- The raw Claude corpus for the enrolled project has 66 top-level JSONL
  transcripts and 200 sub-agent JSONLs. OpenTraces discovery would find 66
  top-level Claude candidates for the project.
- Parsing those 66 raw top-level Claude transcripts with the current parser is
  possible for 61 of them and yields 56 detectable skill invocations across 27
  records. Top skills include `otbox=12`, `scout-research=9`,
  `goal-forge=6`, `simplify=5`, `docs-update=3`, `agent-browser=3`,
  `gitsync=3`, and `tailscale-expose=3`.
- Raw regex scan over the enrolled Claude project found 106 JSONL files with
  skill markers when including sub-agents.
- Codex raw session search found 4 sessions mentioning
  `community-traces-skillopt`, all with skill-body-read evidence, but that
  worktree is not opted in. The enrolled project marker only lists
  `claude-code`, so normal project scanning ignores Codex sessions for that
  project.
- Context Tree bucket state has 17 trace context directories, but this is not
  equivalent to retained `TraceRecord` rows. Trace Index and Skill Intelligence
  consume retained trace records and therefore see only the 4 trace files.

Split/leakage notes: no split or leakage changes. The problem is upstream of
dataset splitting: the retained trace corpus is much smaller than the raw agent
session corpus.

Case-study artifact updates: none. No rows regenerated.

Conclusion: the local machine does have many raw Claude/Codex session logs with
skill evidence, but they have not flowed into the retained OpenTraces
TraceRecord corpus. The current bucket/index cannot surface those skill
invocations because they are not present in retained trace records. Unlock by
reinstalling capture hooks, opting in the SkillOpt worktree if it should be
captured, adding `codex-cli` to the relevant project agents if Codex sessions
should be imported, and running a controlled import/reparse of the 66 raw Claude
sessions into retained TraceRecords.

## Attempt 8 — 2026-05-27

Phase: retained-trace repair with the new skill extraction engine.

Diff/change:
- Updated repeated `opentraces init` so an already initialized project can merge
  newly requested agents and run `--import-existing` instead of returning before
  import.
- Deduped normalized project agents so repeated init cannot persist
  `claude-code, claude-code, codex-cli`.
- Added `_scan --trace-record-only` for bulk repair/reparse runs that need to
  refresh retained TraceRecords and bucket trace objects without rebuilding
  Trail/Context Tree side projections per session.
- Rejected glob/wildcard skill-body paths such as `skills/*/SKILL.md` so the
  Codex/body-read fallback cannot create `skill.name="*"` false positives.
- Stamped new generations with `metadata.supersedes`,
  `metadata.supersedes_reason`, and `metadata.superseded_trace_ids` so Trace
  Index latest-generation queries can collapse additive repair generations.
- Kept live/hook ingestion defaults on the full substrate path; the fast path is
  an explicit repair mode.
- Re-ran targeted parser, core detector, Trace Index, init, and scan tests.

Evidence observed:
- Initial `opentraces init --agent claude-code --agent codex-cli
  --import-existing` proved the public repair path but was too slow for backlog
  import: it wrote only 7-8 retained trace files after several minutes because
  each session attempted expensive Trail/Context Tree side work. The run was
  interrupted and replaced by the explicit trace-record repair path.
- `opentraces --json _scan --reparse --trace-record-only --project
  /Users/jayfarei/src/tries/2026-03-27-community-traces-hf` completed with
  `sessions_seen=366`, `created=328`, `refreshed=1`, `new_generations=11`,
  `skipped=26`, `errored=0`.
- Follow-up trace-record-only repairs after the wildcard/supersession fixes
  opened 340 new additive generations per run because the project is in
  `review_policy=auto`. The final retained trace-file count is therefore 1027;
  latest-generation queries collapse those additive generations using the new
  writer-declared supersession metadata.
- The 26 skipped sessions all reported `below parse quality gate`; there were no
  parser or ingest errors. Stderr showed several circular sub-agent reference
  warnings, but the command exited successfully.
- Retained project trace files increased from 4 to 1027 under
  `~/.opentraces/projects/2026-03-27-community-traces-hf-24eb286b/traces`, and
  bucket trace-record current pointers increased to 1027.
- Project marker now lists `agents=["claude-code","codex-cli"]`; Codex hooks are
  installed in `~/.codex/hooks.json`, the project-local Claude SessionEnd hook
  exists at `.claude/settings.json`, and doctor reports one opted-in project.
- Trace Index raw index contents now include 1027 traces, 333916 units, and 647
  raw `skill_invocation` rows across additive generations. The default
  latest-generation query reports `total=212` `skill_invocation` units across
  116 retained traces: 158 Codex units and 54 Claude units. Top skills:
  `otbox=29`, `goal-forge=22`, `tdd=16`, `architecture-patterns=16`,
  `docs-update=14`, `agent-browser=11`, `scout-research=10`,
  `opentraces=9`, `investigate=8`, `playwright=7`, `review=6`. No
  `skill.name="*"` rows remain.
- `opentraces workflow skill-intelligence --project
  2026-03-27-community-traces-hf-24eb286b --out
  runs/skill-intelligence-pipeline --dry-run --json` completed. It surfaced
  report path
  `runs/skill-intelligence-pipeline/case-study/ci-echo/skill-update-report-v1.json`,
  `dtest_score=1.0`, and split counts `Dtrain=14`, `Dsel=4`, `Dtest=12`.
- The regenerated `runs/skill-intelligence-pipeline/corpus-audit.json` now has
  `total_skill_invocation_units=212`. The case-study report selected
  `skill_id="goal-forge"` with promotion state `default_off`, baseline digest
  `sha256:c0f1002d1e4113e85cf8c7f1e2034687657c1e831cd3e191373e18cd4758dbf9`,
  and candidate digest
  `sha256:010f128110b1b7e81a2b1ddd4c3f64f1096043e2029392b61a1a0cbab229b3ab`.

Split/leakage notes: the deterministic dry-run regenerated the split after the
corpus repair. The selected CI case-study split is `Dtrain=14`, `Dsel=4`,
`Dtest=12`; no automatic promotion was made and the report remains
`default_off`.

Case-study artifact updates: refreshed `corpus-audit.json`, workflow dataset
rows, rollout/report artifacts under `runs/skill-intelligence-pipeline/`, and
the `case-study/ci-echo/skill-update-report-v1.json` report from the repaired
Trace Index corpus.

Next-step rationale: continue from the corrected retained corpus. The remaining
pipeline work should now use Trace Index `skill_invocation` units instead of raw
regex counts, and any full-substrate backfill should be handled separately from
the trace-record-only repair so Context Tree/Trail projections can be profiled
without blocking Skill Intelligence corpus selection.

## Attempt 9 — 2026-05-27

Phase: continuation artifact refresh after retained-corpus repair.

Diff/change:
- Rewrote `runs/skill-intelligence-pipeline/GOAL.md` with a compact
  goal-forge-style completion contract for the remaining closeout.
- Rewrote `runs/skill-intelligence-pipeline/PLAN.md` around the corrected facts:
  212 latest-generation skill units, selected `goal-forge`, current split
  counts, and the next repair-idempotence phase.
- Rewrote `runs/skill-intelligence-pipeline/HANDOFF.md` and
  `runs/skill-intelligence-pipeline/CONTEXT_WARMUP_PROMPT.md` so fresh agents no
  longer start from the pre-repair corpus-audit blocker.
- Wrote a temp handoff at
  `/tmp/skill-intelligence-pipeline-handoff-2026-05-27.md`.

Evidence observed:
- `GOAL.md` pasteable goal is 2612/4000 characters.
- The refreshed plan points to current artifacts:
  `corpus-audit.json` and
  `case-study/ci-echo/skill-update-report-v1.json`.
- The next immediate action is now explicit: make repair/reparse idempotent or
  safely bounded before running long closeout loops.

Split/leakage notes: no split changed in this attempt. The plan records the
current deterministic dry-run split as `Dtrain=14`, `Dsel=4`, `Dtest=12`.

Case-study artifact updates: no JSON case-study rows regenerated. Documentation
artifacts now reference the existing repaired case study and report.

Next-step rationale: the branch now has enough durable context for a new session
to continue directly into repair idempotence, report-quality audit, and final
SkillOpt/otbox verification.

## Attempt 10 — 2026-05-27

Phase: repair/reparse idempotence.

Diff/change:
- Split ingest's "force a reparse" decision from the "source changed since the
  last observation" decision.
- Updated terminal-generation handling so `reparse=True` with unchanged source
  refreshes the latest generation in place instead of opening another additive
  generation.
- Preserved existing additive `new_generation` behavior, `supersedes`, and
  `supersedes_reason="resume"` for terminal sessions whose source has grown.
- Added direct-ingest and `scan_project` regressions for unchanged terminal
  repair staying bounded.

Evidence observed:
- New regression first failed with `new_generation` for unchanged terminal
  repair before the ingest policy patch.
- `.venv/bin/python -m pytest tests/core/test_ingest.py::TestIngestOneSession::test_reparse_unchanged_terminal_generation_refreshes_in_place tests/core/test_ingest.py::TestIngestOneSession::test_grown_file_after_upload_opens_new_generation tests/core/test_ingest.py::TestScanProject::test_reparse_scan_does_not_grow_unchanged_terminal_generations -q`
  -> 3 passed.
- `.venv/bin/python -m pytest tests/core/test_ingest.py tests/cli/test_cli_init.py -q`
  -> 28 passed.
- `.venv/bin/python -m ruff check src/opentraces/core/ingest.py tests/core/test_ingest.py tests/cli/test_cli_init.py`
  -> all checks passed.
- `git diff --check -- src/opentraces/core/ingest.py tests/core/test_ingest.py tests/cli/test_cli_init.py`
  -> clean.

Split/leakage notes: no dataset split or leakage-policy changes. This attempt
only bounds repeated repair/reparse growth before longer Skill Intelligence
closeout loops.

Case-study artifact updates: none. The existing `goal-forge` case-study JSON
and Markdown report were not regenerated in this attempt.

Next-step rationale: audit the case-study report and rerun the deterministic
Skill Intelligence dry-run now that repeated repair is bounded.

## Attempt 11 — 2026-05-27

Phase: bounded repair closeout, Skill Intelligence case-study regeneration, and
verification.

Diff/change:
- Added scan-candidate dedupe by `(parser_name, native_session_id)` so duplicate
  Codex session files are processed once per scan, preferring the newest source
  path. This preserves additive generation semantics while preventing one
  repair pass from opening multiple generations for the same native session.
- Added a Trace Index direct reader for latest `skill_invocation` TraceUnits
  from retained bucket TraceRecords, avoiding full Trace Map rebuilds in the
  Skill Intelligence audit path.
- Narrowed that direct reader to the trace facets actually retained by
  `skill_invocation` units, instead of running semantic/command/file-operation
  facet extraction over every retained trace.
- Replaced the core and Codex-parser `SKILL.md` path regex with a bounded string
  scanner. The scanner keeps wildcard/reserved-name rejection and avoids
  pathological regex work on long shell strings.
- Added optional row `schema_version` fields to `skill-episodes-v1`,
  `skill-eval-tasks-v1`, and `skill-rollouts-v1` schemas and builders.
- Expanded the case-study README and Markdown report renderer with rollout
  transcript refs, limitations, promotion state, and automatic-promotion state.

Evidence observed:
- Full trace-record repair after candidate dedupe:
  `.venv/bin/opentraces --json _scan --reparse --trace-record-only --project
  /Users/jayfarei/src/tries/2026-03-27-community-traces-hf` ->
  `sessions_seen=359`, `created=0`, `refreshed=328`, `new_generations=5`,
  `errored=0`. The remaining new generations were bounded to canonical-source
  changes/current activity, not repeated duplicate candidates.
- Targeted duplicate-session repair checks for
  `019d3a0f-5ad8-7e20-8340-dd983215d86a`,
  `019dd4a0-d8ca-7261-9365-9c60e185ba88`,
  `019dc9f0-925e-7be2-9e58-65fd3a7256a2`, and
  `019dcffc-4965-72d2-98d7-a5aced1f9ad8` each returned
  `sessions_seen=1`, `refreshed=1`, `new_generations=0`.
- The deterministic dry run now completes in about 15 seconds:
  `.venv/bin/opentraces workflow skill-intelligence --project
  2026-03-27-community-traces-hf-24eb286b --out
  runs/skill-intelligence-pipeline --dry-run --json` -> `selected_skill=goal-forge`,
  `report_path=runs/skill-intelligence-pipeline/case-study/ci-echo/skill-update-report-v1.json`,
  `dtest_score=1.0`, split counts `Dtrain=41`, `Dsel=9`, `Dtest=17`.
- `runs/skill-intelligence-pipeline/corpus-audit.json` reports
  `total_skill_invocation_units=609`, `goal-forge total_units=67`,
  `usable_episodes=67`, `claude-code=142`, `codex-cli=467`, and no
  wildcard/reserved skill keys from the audit key scan.
- Generated row samples carry `schema_version` values
  `opentraces.skill_episodes.v1`, `opentraces.skill_eval_tasks.v1`, and
  `opentraces.skill_rollouts.v1`.
- Focused verification:
  `.venv/bin/python -m pytest tests/test_skill_intelligence.py
  tests/test_skill_opt.py tests/core/test_skill_detection.py
  tests/core/test_trace_index_plan056.py tests/core/test_ingest.py
  tests/cli/test_cli_init.py tests/capture/test_parser_claude_code.py
  tests/capture/test_parser_codex_cli_advanced.py -q` -> 160 passed, 1 skipped.
- `make otbox-journeys` -> 72 passed.
- Hygiene:
  `.venv/bin/python -m ruff check` on touched Python files -> all checks passed;
  `git diff --check` on touched files and pipeline artifacts -> clean.
- Full suite:
  first `.venv/bin/python -m pytest tests/ -q` run had one transient
  perf-budget failure in
  `tests/perf/test_bucket_performance_gates.py::test_bench_capture_hot_path`
  at 26.0ms/event against a 25.0ms/event budget; the isolated retry passed.
  A second full run passed with 3052 passed, 168 skipped, 2 xfailed in 1077.80s.

Split/leakage notes: the regenerated deterministic split is `Dtrain=41`,
`Dsel=9`, `Dtest=17`. A leakage-key audit over
`skill-eval-tasks-v1.jsonl` found 34 leakage keys and zero cross-split leakage
keys. The report remains human-approval/default-off with
`approval_state=manual_required_default_off`, `promotion_state=default_off`, and
`automatic_promotion=false`.

Case-study artifact updates: regenerated
`runs/skill-intelligence-pipeline/corpus-audit.json` and
`runs/skill-intelligence-pipeline/case-study/ci-echo/` artifacts:
`skill-episodes-v1.jsonl` (67 rows), `skill-eval-tasks-v1.jsonl` (67 rows),
`skill-rollouts-v1.jsonl` (134 rows), baseline/candidate transcript refs,
`skill-update-report-v1.json`, `skill-update-report-v1.md`, and `README.md`.
The report records baseline digest
`sha256:c0f1002d1e4113e85cf8c7f1e2034687657c1e831cd3e191373e18cd4758dbf9`
and candidate digest
`sha256:010f128110b1b7e81a2b1ddd4c3f64f1096043e2029392b61a1a0cbab229b3ab`.

Next-step rationale: the deterministic CI-safe Skill Intelligence pipeline is
complete for the selected `goal-forge` pack. Any future live Claude/Codex or
non-deterministic harness leg should remain blocked/default-off unless explicit
auth/binary availability and human approval are provided.
