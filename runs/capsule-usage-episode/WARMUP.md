# Context Warm-Up — Generalise Capsules as Usage Episodes (plan 090)

Read this first. It orients a fresh session to execute `kb/plans/090-capsule-usage-episode.md` without
rediscovering the codebase. Goal: reframe the Trace Capsule from a test/repro packet into a
**privacy-bounded usage episode (Agent Experience Report)** that does NOT depend on a test — entirely
additively. Then test it end to end.

## Where you are
- **Worktree:** `/Users/jayfarei/src/tries/community-traces-capsule` (this is where ALL the work happens —
  the capsule subsystem lives only on this branch).
- **Branch:** `feat/capsule-dependency-unblock` (continue here, or branch `feat/capsule-usage-episode` off it).
- **Main repo** (shared git history, separate working dir): `/Users/jayfarei/src/tries/2026-03-27-community-traces-hf`.

## How to run (the worktree already has a venv with the capsule-enabled CLI)
```bash
cd /Users/jayfarei/src/tries/community-traces-capsule
# venv exists from plan-089 work (editable .[dev,web] install). If absent, rebuild:
#   python3 -m venv .venv && .venv/bin/python -m pip install -e packages/opentraces-schema -e ".[dev,web]"
.venv/bin/opentraces capsule --help          # capsule-enabled CLI
.venv/bin/python -m pytest tests/test_capsule*.py -q     # the suite to keep green
```
`opentraces capsule {export, open, share, issue, test, replay, verdict, watch}` — you are ADDING a
`preview` verb and `--include-prompts`/`--product` options, plus two new security tools and an additive
`freeze_capsule` signature. Nothing in the frozen contract moves.

## Source-of-truth docs (read in this order)
1. **`kb/plans/090-capsule-usage-episode.md`** — the plan: requirements R1–R8, units U0–U7, files, verification.
2. **`kb/projects/opentraces/capsule-usage-episode-design.md`** — the full design (object model, consent,
   redaction polarity, naming) with file-cited reuse-vs-new tables and a **verified ground-truth appendix**.
   The appendix has exact `file:line` citations — trust them, re-verify before editing.
3. `kb/plans/089-capsule-dependency-unblock.md` + `runs/capsule-dependency-unblock/RUNLOG.md` — prior context
   (the consumes model, run/oracle verdict, the live loop, the otbox/venv setup gotchas).

## The non-negotiable constraints (violating these fails the plan)
1. **ADDITIVE ONLY.** Do NOT change `REQUIRED_KEYS` or `CAPSULE_SCHEMA_VERSION` in
   `src/opentraces/core/capsule/contract.py`. New keys are optional and threaded through `freeze_capsule`.
   (`SECURITY_VERSION` DOES bump — new redaction tools. That is expected.)
2. **`test = null` is first-class.** No new field or path may depend on a test existing. Every feature must
   work on a `test=null` usage episode.
3. **Opt-in INCLUSION, not opt-in protection.** Prompt-bearing fields
   (`context_resume_packet.system_layer`, `slice.steps.*.reasoning_content`) are EXCLUDED by default; the
   developer adds them with `--include-prompts`. Default-safe.
4. **Narrow + consented by default.** `suggest_consumes` is a STDERR hint only — never auto-write a product.
   `capsule preview` writes/publishes NOTHING.
5. **Markers + command noun are wire contracts — do not rename.** `<!-- opentraces-capsule: <id> -->`,
   `opentraces-capsule-verdict: <id> state=<s>`, and the `capsule` command word stay literal (issue
   idempotency via `find_capsule_issue`/`issue_state`). Naming reframe is PRESENTATION ONLY (render banner +
   help text).
6. **Out of scope:** v2 aggregation/DX-graph, v3 vendor routing, auto-population of consumes, any `capture/`
   change. (One cheap optional forward-pull allowed: a bare `capsules/v1/index.jsonl` append in `publish_capsule`.)

## Verified ground-truth (from the design appendix — re-verify, then build)
- `REQUIRED_KEYS` — `core/capsule/contract.py:31-48` (15 keys; `test`, `summary`, `environment`, `product`,
  `bundle`, `privacy_scope` are NOT in the set; `validate_capsule` is presence-only, ignores unknown keys).
- `freeze_capsule` — `contract.py:88` (builder `:108-140`) is a **literal dict builder with NO `**extra`
  passthrough**. New keys require a real signature change + call-site updates. Additive, but NOT "free."
- `REDACTION_FLOOR = ("regex","entropy")` — `redaction.py:30`; runs over the whole envelope via
  `sanitize_dict` (`:141`); manifest is counts-only with `by_field_path`, never `matched_text`
  (`build_redaction_manifest`). This is your inspectability primitive.
- Security pipeline is a flat registry of `Detector`/`Judge`/`Transformer` — `security/tools/_registry.py`.
  Your `business_logic` is a Detector (emits spans); `capsule_scope` is a Transformer (field-path exclusion,
  like `PathAnonymizerTransformer`). Register in canonical order.
- Classifier patterns to promote to redaction: `internal_hostname`, `internal_url`, `db_connection_string`,
  `aws_account_id` in `security/classifier.py` (currently a Judge that only flags).
- `_extract_snippets` handles `Read/Edit/Write/Grep` only (`capture/claude_code/parse.py:~1201-1231`);
  **WebFetch is absent** → URL/docs consultation is NOT captured. Document as a gap; do not pretend it exists.

## The two bug fixes (U0 — ship FIRST, independent of the reframe)
1. **Self-reference sha-pin** — `core/capsule/share.py:239-243` stores `revision="main"` +
   `published_revision: None` into the uploaded JSON while *returning* the sha-pinned URL separately
   (`:258-264`). Fix in-place: after `upload_folder` returns `commit.oid`, re-write `capsule["share"]` with
   `revision=oid` + `published_revision=oid` before returning. No version bump.
2. **Classifier `matched_text` leak** — `security/.../classifier_tool.py:36`. Any classifier→capsule
   surfacing must strip `matched_text` and keep only `{pattern, severity, field_path}`.

## First moves
1. `git switch -c feat/capsule-usage-episode` (off the current branch).
2. **U0 first:** land the two bug fixes + tests (`pytest tests/test_capsule*.py -q` stays green).
3. Then U1→U7 per the plan. Log progress to `runs/capsule-usage-episode/RUNLOG.md` after each unit.
4. The end-to-end test (R8/U7) is the finish line: a real-shaped trace → `test=null` usage-episode capsule →
   `capsule preview` shows redaction/exclusion/privacy_scope → mocked publish asserts sha-pin + no
   matched_text + business-logic redaction + prompts excluded by default.

## Honest reminders
- The verdict mechanism (when a test exists) runs a DECLARED command against frozen source with the
  dependency swapped — it does NOT re-run the agent/LLM. Never present a verdict as "the agent succeeded."
- The capsule is only as good as what capture supplied. URL docs + runtime-resolved versions are genuine
  gaps today; label them, don't imply them.
