# Run log — 0.3.3 → 0.4 Migration Acceptance Suite

Append one entry per iteration. Newest at the bottom.

---

## 2026-05-27 — seeding

- Mapped the migration boundary (see `kb/plans/085`): source CLI 0.3.3 / schema
  0.3.0, target CLI 0.4.0 / schema 0.6.0, 190 commits apart.
- Confirmed a 0.3.3 user has no bucket / trails / context tree / local datasets.
- Confirmed the one breaking change: `TraceRecord.patch` removed in 0.6.0;
  `migrate_outdated_shards` silently drops it. `opentraces_schema.migrations`
  never created.
- Confirmed otbox has no legacy-version world seeder/checkpoint — that is the
  first enabling task (R1–R3).
- Wrote plan 085 (spec + S1–S12 scenarios), this handoff, and GOAL.md.
- **Next:** Phase 1 audit (S2) — pin the exact added/dropped field set by running
  a real 0.3.0 record through the 0.6.0 model; then decide S3 reconstruction.

## 2026-05-27 — Phase 1 (S2 audit) COMPLETE

- Change: added `tests/migration/audit_schema_fieldloss.py` +
  `tests/migration/fixtures/record_schema_0_3_0.json` (committed, reproducible
  S2 audit). Full recursive class×field diff of schema models v0.3.3 vs HEAD.
- Evidence: the ONLY removed field across 0.3.0 → 0.6.0 is **`Outcome.patch`**
  (no classes removed; new classes `GitAnchor`, `Patch`). Runtime round-trip of
  a 0.3.0 record through the 0.6.0 model: `outcome.patch` is **silently dropped**
  (Pydantic `extra="ignore"`, no `model_config`), `patches[]` is empty,
  `schema_version` stays `0.3.0` on bare `model_validate` (HF path re-stamps
  separately). Audit verdict: PRE-FIX.
- Decision: S3 confirmed = reconstruct `outcome.patch` (standard unified diff,
  structurally parseable) into `patches[]`, fallback preserve under
  `metadata.legacy.patch`. No second breaking field change — scope holds.
- Next: de-risk the named blocker (build a real v0.3.3 venv for the fixture),
  then implement the S3 reconstruction (flips the audit to FIXED).

## 2026-05-27 — blocker cleared + Phase 4 keystone (S3) + S6 done

- Blocker CLEARED: real v0.3.3 CLI / schema 0.3.0 builds in an isolated venv via
  `git worktree add /tmp/ot-v033-worktree v0.3.3` then `python -m venv` +
  `pip install -e packages/opentraces-schema` + `pip install -e .`. One snag:
  the CLI wheel force-includes `web/viewer/dist` (gitignored build output) —
  fixed by stubbing `web/viewer/dist/index.html`. Verified `opentraces 0.3.3 |
  schema 0.3.0`. Phase 2 fixture is now buildable.
- Change (keystone fix): added `packages/opentraces-schema/.../migrations.py` —
  the reserved migrations module, now real. `migrate_record(raw, target)` is the
  single source of truth: reconstructs `patches[]` from the legacy unified diff
  (one Patch per file, content-addressed `patch_id`, provenance markers),
  preserves the raw diff under `metadata.legacy.patch`, drops the dead
  `outcome.patch`, additive + idempotent + non-mutating. Wired into
  `HFUploader.migrate_outdated_shards` (one call site) so HF + bucket share it.
- Evidence: audit now FIXED — bare load loses only `outcome.patch`;
  `migrate_record` recovers both files (foo.py, bar.py) + preserves raw diff.
  pytest: `tests/test_migration_0_3_3_to_0_4.py` 10/10; new S6 integration test
  `test_migrate_shard_reconstructs_legacy_outcome_patch` green; full
  `TestSchemaMigration` + 57 upload/migrate tests pass (no regression).
- Next: Phase 2 — drive the v0.3.3 venv to build a real legacy world (project +
  0.3.0 traces with outcome.patch + state.json + config + 0.3.0 HF dataset),
  freeze it, add `c-legacy-v033` + upgrade checkpoint (plan-072 pattern).

## 2026-05-27 — Phase 2 R1/R2 done + S1 verified + S5 decision needed

- Change: `tests/migration/build_legacy_world.py` drives the REAL v0.3.3 CLI
  (`init` + `_scan --reparse`) on a synthetic Claude session and freezes a
  genuine schema-0.3.0 world to `tests/migration/fixtures/legacy_world_v033/`
  (scrubbed paths + PROVENANCE.json). `tests/migration/restore_legacy_world.py`
  rehydrates it into a live HOME/project (the restore the `c-legacy-v033`
  checkpoint will perform). Real v0.3.3 ingest emits a 3-step 0.3.0 trace;
  builder guarantees `outcome.patch`.
- Evidence (pytest): migration set now 16/16 (incl. real-fixture-bound
  `test_frozen_legacy_trace_migrates_without_loss` — the frozen 0.3.0 record's
  `app.py` reconstructs into `patches[]`, raw diff preserved).
- Evidence (0.4 CLI over restored world):
  * S1 read-compat PASSES — `--version`/`status`/`doctor` rc=0, and
    `trace query farewell` rc=0 FINDS the legacy trace
    (6961102e… "add a farewell() helper"). No crash reading 0.3.3 state.
  * S5: `ctx list` -> "No traces in local bucket." The legacy trace is fully
    readable in-place but is NOT in the bucket. `setup bucket --migrate` only
    upgrades a pre-plan-080 bucket *layout*; NO path adopts legacy
    `traces/*.jsonl` into the v2 bucket. read-in-place is non-lossy and works.
- DECISION NEEDED (S5 Open Decision, blocked-stop trigger): pick the 0.3.3→0.4
  bucket contract — (A) read-in-place is the contract (document; bucket holds
  only 0.4+ captures), or (B) build an explicit `bucket adopt-legacy`
  (reads traces/*.jsonl -> migrate_record -> write_trace_record). Determines
  Phase 4 bucket scope + what the S5/S12 journeys assert. Surfaced to user.
- Committed the green increment (migrations + audit + builder + fixture +
  restore + tests). Remaining: c-legacy-v033 checkpoint + 12 journey TOMLs +
  precondition vocab/gate (Phase 3), S5 per decision, S8 config-forward-compat
  check, docs (Phase 5).

## 2026-05-28 — PAUSE for handoff (user decision)

- Pushed the green increment to origin main: `3efdeeae9c`.
- S5 RESOLVED by user steer: **"0.3 didn't have legacy"** -> 0.3.3 had no bucket
  at all, so there is nothing to *migrate into* a bucket. The contract is
  **read-in-place**: legacy `traces/*.jsonl` stay fully readable on 0.4 (S1
  proves this), and the bucket holds only 0.4+ captures. Do NOT build a forced
  bucket adoption. An opt-in `bucket adopt-legacy` is optional future polish,
  NOT required for non-lossy migration — revisit only if explicitly wanted.
  => S5 journey should assert read-in-place + a documented note (no new CLI
  surface); S12 e2e should NOT require legacy traces in the bucket.
- User asked to STOP and `/handoff` for resume. Pausing here. Active Claude Code
  `/goal` Stop hook is still set — resume session should re-issue the goal (or
  the user clears it with `/goal clear`).
- RESUME POINTER: this log + `kb/plans/085-...` + `runs/migration-0.3.3-to-0.4/`.
  Next concrete work on resume: Phase 3 — build `c-legacy-v033` checkpoint
  (wraps `tests/migration/restore_legacy_world.py`) + the journey TOMLs for
  S1/S7/S8/S9/S10/S11/S12 (S2/S3/S4/S6 already covered by pytest), precondition
  vocab + tiered-gate/inventory wiring, S8 config-forward-compat verification,
  then docs (Phase 5).

## 2026-05-28 — RESUME: Phase 3 + S7 + Phase 5 docs COMPLETE

- Change (R3 checkpoints): `tests/otbox/checkpoints/_legacy_v033.py` registers
  otbox's first previous-version world. `c-legacy-v033` (composed on
  `c-installed-source`) restores the frozen v0.3.3 fixture into the box
  (driver-mediated per-file copy + `$LEGACY_HOME`/`$LEGACY_PROJECT` rehydrate)
  then git-inits a real repo; `c-legacy-v033-upgraded` runs a fresh 0.4 capture
  (the simple-refactor fake-harness session, which owns `src/app.py` so it
  coexists with the legacy root `app.py`) inside the legacy repo — the box ends
  with the legacy 0.3.0 trace AND a new 0.4 trace, opentraces refs created
  additively over the pre-existing history. Registered in `checkpoints/__init__.py`.
  Provides vocab: `legacy_world_restored`, `migration_applied`, `no_data_loss`,
  `migration_idempotent`, `pre_migration_schema`, `captured_traces`.
- Change (R4): extended `journey.py::_checkpoint_satisfies` with the migration
  precondition vocab (bool flags + `pre_migration_schema` string match) and
  `journey.py::_captured_session` templating exposes `{legacy_trace_id}` /
  `{new_trace_id}` / `{head_before_capture}` / `{step_index}` from the new
  checkpoint audits. `otbox matrix --inventory --strict` -> "jtbd: drift OK".
- Change (Phase 3 journeys): 7 tier-0 migration TOMLs under
  `tests/otbox/catalogue/journeys/migration-s{1,5,8,9,10,11,12}-*.toml`. S1
  read-compat (silver), S5 read-in-place / empty bucket (silver), S8 config
  forward-compat via `config show` JSON (silver), S9 git-ref additivity (gold),
  S10 idempotency (silver), S11 non-destructive on-disk preservation (silver),
  S12 end-to-end upgrade GOLD ship gate. S1/S5/S8/S10/S11 fork from
  `c-legacy-v033`; S9/S12 from `c-legacy-v033-upgraded`.
- Finding: the 0.4 first-touch is `trace index rebuild` — without it `trace get`
  / `trace map` return rc=6 for the legacy trace (they read the v2 bucket which
  is 0.4-only); `trace query` finds it via the index. After rebuild all read
  surfaces resolve. S1 runs it first; S5 asserts the bucket is empty
  (`bucket status --json` -> `bucket.traces == []`, `ctx list` -> `traces == []`)
  i.e. read-in-place, no auto-adoption (matches the S5 product decision).
- Change (S7): `tests/test_migration_0_3_3_to_0_4.py` gains 3 tests. The
  schema-ahead guard (`HFUploader._sync_dataset_infos`) is byte-identical in
  0.3.3 and 0.4; only `LOCAL_SCHEMA_VERSION` differs. Layer A (default CI) pins
  local=0.3.0 + remote=0.6.0 and asserts `RemoteSchemaAheadError` + no overwrite.
  Layer B drives the REAL v0.3.3 client (subprocess into the /tmp venv) and
  asserts the 0.3.3 code refuses; SKIPs when the venv is absent (CI-safe). The
  0.3.3 push CLI (`cli/publish.py`) maps the error to exit 3 + `ot setup upgrade`.
- Change (Phase 5 docs): VERSION-POLICY.md now states the migrations module is
  IMPLEMENTED (a removal that ships a non-lossy registered migration is the
  accepted mechanism); CHANGELOG 0.6.0 "Removed" gains a Migration sub-note;
  new `packages/opentraces-schema/MIGRATION-0.3.3-to-0.4.md` records the patch
  decision + read-in-place + reciprocal-refusal + non-destructive contract.
- Evidence: `pytest tests/test_migration_0_3_3_to_0_4.py tests/publish/test_upload.py
  tests/otbox/test_otbox_slice.py` -> 119 passed, 1 skipped (the 7 migration
  journeys among them). Full `tests/otbox/` + `packages/opentraces-schema/`
  -> 188 passed, 67 skipped. S2 audit: bare_dropped_fields == ['outcome.patch'];
  migrated_patch_files == ['foo.py','bar.py']; legacy_patch_preserved == true.
- ALL S1-S12 now covered (S2/S3/S4/S6 pytest; S1/S5/S8/S9/S10/S11/S12 otbox
  journeys; S7 pytest incl. real-v0.3.3 layer). Plan 085 deliverable complete.
- Full `pytest tests/`: 3018 passed / 173 skipped / 2 xfailed / 4 failed. The 4
  reds are NOT from this work: 3 are tests/perf timing gates (one confirmed
  passing in isolation; failed under concurrent load) and 1 is a pre-existing
  trace-trails corpus-currency drift (`projection_digest` stale) that fails on
  clean origin/main too (verified via stash). Triaged separately per CLAUDE.md.

## 2026-05-29 — Upgrade-UAT coverage audit (18-agent workflow) + CONFIRMED P0 bug

- Ran an 18-agent Workflow (`upgrade-uat-coverage-map`, run wf_eb8533cb-ef6)
  mapping all documented functionality (37 docs + README + 125 CLI commands =
  262 features) vs current upgrade coverage. Full catalogue:
  `runs/migration-0.3.3-to-0.4/UPGRADE-UAT-AUDIT.md` (69 proposed cases: 35 P0 /
  29 P1 / 5 P2; 44 uncovered + 25 partial).
- Verdict: plan-085 is migration-CORE, not a genuine end-to-end upgrade UAT.
  Roughly 25-35% of documented upgrade behavior is tested; the breakage-spine
  (schema migration, read-in-place, additivity, reciprocal refusal) is solid,
  but the forward-onboarding surface a real upgrader runs (setup wizard, auth
  reuse, dataset create/run/review/publish, capture-otlp/real context capture,
  security tool RUNS over legacy content, bucket remote sync, two-store egress
  separation) is untested on upgrade.
- CONFIRMED P0 BUG (verified two ways, not just agent-claimed): the live read
  path drops the legacy diff. `cli/trace.py:1058 _read_trace_record_from_path`
  calls `TraceRecord.model_validate_json` with NO `migrate_record` wrap (same at
  1271/1309/1559/1775). Empirical: on c-legacy-v033 the on-disk 0.3.0 record has
  `outcome.patch` (136 chars) but `trace get <id> --json` returns `patches[]`
  length 0, `metadata.legacy` absent, patch gone. `migrate_record` only runs in
  the HF publish path (itself unreachable from the live CLI per prior memory).
  So the documented "old traces readable with patches[]" promise is FALSE on the
  surface users hit. S1 missed it (asserted rc=0 + trace_id, never patch content).
  Fix is small (route reads through a migration-aware loader) but lives in
  `src/opentraces/cli/trace.py` — OUTSIDE plan-085's original "Use only"
  allowlist, so it needs explicit user go-ahead before I touch it.
- Recommended phased plan (in the audit doc): Phase 0 = fix the read-path drop +
  land U-trace-2 (pytest) + U-trace-1 (journey) as guards; Phase 1 = fixture-only
  pytests; Phase 2 = enrich the c-legacy-v033 checkpoint family (+ a real-OTLP
  upgraded checkpoint, pty_runner step); Phase 3 = the journey bulk; plus the
  parts only a real two-venv pip-0.3.3->0.4 UAT can prove.

## 2026-05-29 — GOAL (full-release UAT) Phase 0 COMPLETE: P0 read-path fix + guards

- Scope note: the /goal explicitly EXPANDS plan-085's allowlist to cover
  `src/opentraces/cli/trace.py` + the read/index/bucket loaders for the P0 fix.
- Change (single source of truth): `opentraces_schema.migrations` gains
  `load_record_dict(raw)` + `load_record_json(text)` — the migration-aware
  constructors (`TraceRecord.model_validate(migrate_record(...))`). Exported
  from `opentraces_schema.__init__` (+ `migrate_record`). Idempotent no-op on
  0.4+ records; reconstructs `patches[]` + preserves `metadata.legacy.patch` on
  legacy 0.3.x records.
- Change (route every legacy-shard read through it):
  * `cli/trace.py` — `_read_trace_record_from_path` (the named P0 site, JSONL
    fallback), `_read_trace_record_via_backend` (remote dict), `_load_trace_record`
    (staging by id), `trace_list` loop, `_commit_single` message build, and the
    `trace get` filename-fallback scan. All 6 direct `model_validate_json` /
    `model_validate` record sites now go through the loaders.
  * `core/trace_index.py::_iter_trace_file_records` (index build over legacy
    shards — so `trace query`/`map` reflect the migrated record).
  * `core/bucket_store.py::_read_jsonl_trace_records`. (The two bucket-OBJECT
    sites at 266/1565 are 0.4-only by the read-in-place contract; left untouched
    to keep the diff on the actual legacy path.)
- Evidence (empirical before/after, frozen legacy shard
  `legacy_world_v033/.../6961102e-*.jsonl`, on-disk schema 0.3.0, outcome.patch
  136 chars): BEFORE (bare validate) `patches=0`, `metadata.legacy` absent;
  AFTER (`_read_trace_record_from_path`) `patches=1` (file `app.py`),
  `metadata.legacy.patch` present. The documented "old traces readable with
  patches[]" promise now holds on the surface users hit.
- Guards landed:
  * U-trace-2 (pytest) — `test_u_trace_2_live_read_path_keeps_legacy_patch`
    pins BOTH the old broken behaviour (bare validate drops it) and the fixed
    live-loader behaviour over the frozen fixture.
  * U-trace-1 (otbox journey, gold) —
    `migration-u-trace-1-patch-survives-read.toml` forks `c-legacy-v033`,
    `trace index rebuild` then `trace get {legacy_trace_id} --json`, asserts
    `trace.patches.0.file_path == app.py` + `trace.metadata.legacy.patch`
    present through the live CLI end-to-end.
- Verification: `pytest tests/test_migration_0_3_3_to_0_4.py` 16/16 (was 15);
  the 8 migration otbox journeys (incl. U-trace-1) 8/8 in 19.6s;
  `pytest tests/test_migration_0_3_3_to_0_4.py tests/otbox/test_otbox_slice.py`
  -> 95 passed. No regression in the read path.
- Next: Phase 1 (fixture-only pytests from the audit's P0 set), then Phase 2/3.

## 2026-05-29 — Phase 1 (pytest layer) P0 batch + a second read-loader fix

- Dispatched 3 read-only Explore agents to map the integration-heavy P0 paths
  (datasets, security sanitize, HF-publish/egress). Key findings, evidence-backed:
  * U-hf-1 CONFIRMED (prior memory is correct): live `dataset publish`
    (`core.datasets.publish_dataset`) does NOT reach `HFUploader` — it uploads
    via `HfApi.upload_folder` directly, and its own schema-ahead check
    (`_check_remote_schema_not_ahead`) no-ops against a real remote because
    `_fake_remote_dir` only resolves under `OPENTRACES_PLAN058_FAKE_REMOTE_ROOT`.
    `HFUploader.{ensure_repo_exists,_sync_dataset_infos,migrate_outdated_shards}`
    are wired only to the legacy `opentraces push` (`cli/publish.py`). Forward HF
    shard migration on a real `dataset publish` upgrade is an unverified PRODUCT
    GAP, now pinned by a test rather than assumed.
  * Second read-path drop found: `cli/security.py` `{"record"}` sanitize path
    used a bare `TraceRecord.model_validate(rec_data)`, so a legacy record POSTed
    to `security sanitize` lost its diff before scanning. FIXED -> `load_record_dict`
    (in-scope read loader per the goal). Now the legacy diff lands under
    `metadata.legacy.patch` where the walker can scan it.
  * Honest negatives recorded (audit premises that the code refutes):
    `dataset new --rows-file` is an OPAQUE-row importer (JSON-Schema validated,
    no TraceRecord migration) so it does NOT reconstruct patches[] (U-ds-2 is a
    characterization, deferred); `Outcome(patch=...)` is silently DROPPED, not
    rejected (U-hf-7 "fails loudly" is false); reconstructed patches[] are
    file-granular not hunk-granular (U-trail-7).
- Change: new `tests/test_migration_upgrade_uat.py` (11 tests, all default-CI-safe):
  U-ctx-2 (P0, ctx fields inert on migrated record), U-trail-7 (P1 lib half,
  labeled file-granular degradation), U-setup-10 (P1, attribution/git_links
  preserved verbatim), U-sec-2 (P0, x3: canonical-order tools_applied + patch
  survival, regex redacts a planted secret, CLI record path is migration-aware),
  U-ds-8 (P0, x2: processor receives migrated 0.6.0 shape; invalid_output flagged
  + --strict raises), U-bucket-2 (P0, egress opt-in not token-gated), U-bucket-3
  (P0, two-store separation), U-hf-1 (P0, dataset-publish-not-HFUploader pin).
- Change (src): `cli/security.py` record-ingestion -> `load_record_dict`.
- Verification: `pytest tests/test_migration_upgrade_uat.py` 11/11;
  regression sweep over the touched subsystems (cli security x2, core processors,
  security pipeline/api, plan057/058 datasets+remotes, publish upload,
  migration core) -> 269 passed / 1 skipped. No regression from the security CLI
  loader change.
- Deferred (time-bound, per goal P0>P1>P2): U-ds-2 (opaque-row characterization),
  U-ds-7 (needs Phase 2 bucket state), U-sec-3 (mostly subsumed by U-sec-2; patch
  CONTENT lives in trail companion which security never reads — documented
  deferral), U-hf-3/6/7, U-sec-6, U-setup-9. Phase 2 (checkpoint enrichment) +
  Phase 3 (journey bulk) + Phase 4 (two-venv real UAT) remain.

## 2026-05-29 — Phase 3 (journeys) P0 batch against existing checkpoints

- Scope choice: Phase 2's expensive infra (real-OTLP `c-legacy-v033-otel-upgraded`
  checkpoint + `pty_runner` step) is flagged hard-blocked/deferred in CLAUDE.md,
  so I targeted the highest-value Phase 3 P0 journeys that run against the
  EXISTING c-legacy-v033 checkpoint: the "net-new verbs over a legacy world stay
  HONEST" cases (the dominant first-touch upgrader surprise).
- Probed the real CLI shapes against a restored legacy world before asserting
  (no guessing): `ctx tree` -> limitations `["context_tree_not_captured"]`,
  `trail track` -> `event_count 0` + limitations `["missing_trace_events"]`,
  `ctx list` -> empty `opentraces.ctx.list.v2`, `trace map --bursts` -> rc=0 with
  a deterministic `change_burst` node. (`doctor` has no `--json` subcommand flag;
  U-config-3 deferred to avoid the global-flag-placement ambiguity.)
- Change: 3 tier-0 journeys forking `c-legacy-v033`:
  * `migration-u-ctx-1-honest-no-evidence.toml` (silver) — ctx list empty + ctx
    tree resolves the legacy id with zero nodes + `context_tree_not_captured`.
  * `migration-u-trail-1-honest-unknown.toml` (gold) — trail track returns
    event_count 0, empty timeline, named event-log ref, `missing_trace_events`;
    no fabricated survival verdict/confidence.
  * `migration-u-trace-8-bursts-degrade.toml` (silver) — trace map --bursts rc=0
    with a deterministic burst map over the migrated trace (no crash).
- Verification: all 11 migration otbox journeys pass (7 plan-085 + U-trace-1 +
  U-ctx-1 + U-trail-1 + U-trace-8) in 21s; `otbox matrix --inventory --strict`
  -> "jtbd: drift check OK".
- Remaining (deferred, time-bound): Phase 2 OTLP/pty_runner infra; the bulk of
  Phase 3 P0/P1 journeys that need the enriched checkpoint (credential file,
  stale skill/hook, privacy_tier, pre-existing settings.json) — U-setup-2..6,
  U-bucket-1/4/5, U-trail-2/3/4, U-ctx-5, U-ds-1, U-config-1/2/5, U-auth-*;
  Phase 4 two-venv real UAT; Phase 5 manual-UAT.

## 2026-05-29 — Full-suite regression gate GREEN + status checkpoint

- Full `pytest tests/` -> 1 failed, 3037 passed, 173 skipped, 2 xfailed (22m45s).
  The single failure is `test_trace_trails_corpus_fixture_is_current` — the
  pre-existing `projection_digest` corpus-currency drift the goal explicitly
  excludes. Proven NOT mine: (a) none of my 4 commits touch
  `tests/fixtures/trace_trails_corpus/` or the corpus harness (git log range
  empty); (b) the corpus uses 0.6.0 fixtures where `migrate_record` is a verified
  no-op, so the read-loader change cannot alter the projection; (c) prior baseline
  already carried it (verified via stash earlier). The 3 perf-timing flakes from
  the old 4-failure baseline all PASSED this run; passed count rose 3018 -> 3037.
- Goal verification clause: ALL met. 11/11 migration+U journeys pass;
  `pytest migration + upgrade-uat + otbox slice` -> 109 passed; before/after proof
  shown (trace get patches 0 -> 1); `otbox matrix --inventory --strict` drift OK.
- DELIVERED + pushed to main (4 commits): Phase 0 (P0 read-path fix + U-trace-1/2
  guards), Phase 1 P0 pytest batch (11 tests + security CLI read-loader fix +
  U-hf-1 product-gap pin), Phase 3 P0 honest-no-evidence journeys (U-ctx-1,
  U-trail-1, U-trace-8). Two confirmed read-path drops fixed; one product gap
  pinned as fact.
- REMAINING (needs a scope decision, not just time):
  * Phase 2 infra: the `c-legacy-v033-otel-upgraded` checkpoint (real-OTLP) +
    `pty_runner` journey step are flagged HARD-BLOCKED/deferred in CLAUDE.md
    (plan 078 (c)/(d)). Building them is a substantial sub-project.
  * Phase 2a checkpoint enrichment (credential file, stale skill/hook,
    privacy_tier, pre-existing ~/.claude/settings.json) gates the bulk of the
    Phase 3 P0 journeys: U-setup-2..6, U-bucket-1/4/5, U-trail-2/3/4, U-ctx-5,
    U-ds-1, U-config-1/2/5, U-auth-1/2/3.
  * Phase 4 two-venv real UAT (U-setup-1/7, U-ctx-3/4, U-config-6, U-ds-4,
    U-hf-2): needs the real v0.3.3 venv at /tmp/ot-v033-worktree/.venv-v033
    (may be absent; rebuildable per HANDOFF) + the live-HF token lane. Documented
    as runnable manual-UAT steps in UPGRADE-UAT-AUDIT.md Phase 4/5.
  * Remaining Phase 1 pytests (lower value / honest-negatives): U-ds-2 (rows-file
    is opaque-row, does not migrate), U-sec-3, U-hf-3/6/7, U-sec-6, U-setup-9.
- Next session decision: commit to building the Phase 2a enriched checkpoint
  (unblocks ~13 P0 journeys) and/or the Phase 4 two-venv make target, or treat
  the current migration-core + onboarding-honesty coverage as the v1 ship line.

## 2026-05-29 — Phase 3 P0 onboarding journeys (existing checkpoint, no enrichment)

- Resumed per stop-hook: item (3) needs more P0 journeys green. Many run against
  the EXISTING c-legacy-v033 checkpoint with no OTLP/enrichment, so I implemented
  those first. Probed real CLI shapes against a restored legacy world before
  asserting (no guessing).
- Change: 4 tier-0 journeys forking c-legacy-v033:
  * `migration-u-config-2-legacy-marker-loads.toml` (P0, silver) — `config show`
    loads the legacy config_version 0.2.0 verbatim, `status` honors review_policy
    review. HONEST FINDING: config_version is NOT stamped forward on read (the
    loader is forward-TOLERANT, not forward-migrating), so the audit's U-config-1
    "migrated forward / not stale 0.2.0" premise does not hold; asserted real behavior.
  * `migration-u-setup-3-init-idempotent.toml` (P0, silver) — `init --agent
    claude-code` over an enrolled legacy project returns "Already initialized",
    idempotent on re-run.
  * `migration-u-setup-4-setup-git-clean-install.toml` (P0, silver) — `setup git`
    installs the correlator on a legacy repo with no prior event log
    (state.installed/owned_hook_present/chain_present all true), idempotent.
  * `migration-u-ds-3-pull-removed.toml` (P1, bronze) — removed `pull` verb exits
    rc=2 (used `expect_returncode=2` so the intentional non-zero isn't a step
    failure). HONEST FINDING: it is the generic Click "No such command 'pull'"
    with NO `dataset new --rows-file` replacement hint; the audit's hint premise
    does not hold. Logged as a UX gap.
- Discovered runner mechanic: a `cli`/`shell` step that exits non-zero is counted
  a "step failure" (journey FAIL) even when all assertions pass, UNLESS the step
  declares `expect_returncode`. Used that for the pull case.
- Verification: 15 migration journeys pass (was 11); `otbox matrix --inventory
  --strict` drift OK.
- Honest-findings backlog (product/UX gaps surfaced, not test bugs): config_version
  not forward-stamped (U-config-1); `pull` has no replacement hint (U-ds-3);
  `dataset list` shows an auto-created legacy-bridge dataset, not an empty array
  (U-ds-6). These are candidates for a follow-up product decision.
- Next: U-trail-2/U-config-5/U-trail-4 against c-legacy-v033-upgraded (existing);
  then checkpoint enrichment (credential/privacy_tier/settings.json) for U-auth-*
  / U-bucket-1; then a BLOCKED entry + runnable manual-UAT steps for the real-OTLP
  / two-venv cases (item 4).

## 2026-05-29 — Phase 3 P0 trail-honesty journeys on the upgraded checkpoint

- Probed the existing c-legacy-v033-upgraded box directly (resolve_checkpoint +
  driver.exec) to capture real shapes: `trail blame commit {head_before_capture}`
  (pre-upgrade restore commit) -> rc=0, coverage.attributed 0 / total 0, traces []
  (honest empty, the cache exists post-capture but has nothing for the old
  commit); `trail blame commit {new_commit_sha}` -> rc=0, coverage.ratio 1.0, one
  trace (the real 0.4 anchor). `git-backfill` on c-legacy-v033 -> rc=0,
  commits_correlated 0, errors [] (no fabricated anchors, tolerates no notes ref).
- Change: 2 journeys.
  * `migration-u-trail-2-blame-commit-honest.toml` (P0, gold, forks
    c-legacy-v033-upgraded) — blame commit is empty-but-valid for the pre-upgrade
    commit and fully-attributed for the 0.4 capture; no fabricated trace id.
  * `migration-u-trail-3-backfill-honest.toml` (P0, silver, forks c-legacy-v033)
    — setup git + git-backfill tolerate the empty state with zero errors and zero
    fabricated correlations.
- Verification: 17 migration journeys pass (was 15); inventory drift OK.
- Deferred with reason: U-ds-1 (dataset new --workflow + run) needs the
  workflow path which materializes under HOME/.opentraces/workflows/<name> (a
  dynamic box path with no template var yet) -> needs a {home}/{workflows_dir}
  journey var or a relative-path convention; parked rather than hardcode.

## 2026-05-29 — Item (4): real two-venv handoff automated + manual-UAT script

- The real v0.3.3 venv IS present here (`/tmp/ot-v033-worktree/.venv-v033`,
  opentraces 0.3.3) and the live-HF token exists
  (`~/.opentraces/otbox-live-hf-token`). Verified the genuine binary-to-binary
  handoff: a HOME initialized by real 0.3.3 is read by real 0.4 with rc=0 and
  config_version 0.2.0 preserved (no crash, forward-tolerant).
- Change (auto): `tests/test_migration_upgrade_uat.py::test_u_config_6_real_v033_home_is_read_by_real_v04`
  (U-config-6 / U-auth-1) drives BOTH real binaries (skip-if-absent, S7 Layer-B
  discipline). 12/12 in the file; it RAN (not skipped) here.
- Change (doc): `runs/migration-0.3.3-to-0.4/MANUAL-UAT-TWO-VENV.md` — the
  runnable copy-paste script for every real-v033-venv / manual-uat case
  (U-setup-1/7, U-auth-1/2/3, U-bucket-2/3/14, U-ctx-3/4, U-ds-4, U-hf-2,
  U-config-6/7). Each step marked [auto] (with the pytest/journey pointer) or
  [human] (live agent / network). This satisfies item (4)'s "covered by a
  real-v033-venv test OR explicitly documented as manual-UAT with runnable steps."

## 2026-05-29 — BLOCKED: real-OTLP / pty_runner capture infra (Phase 2 hard-block)

- Per the goal's block clause ("a real claude/OTLP capture cannot run here"):
  the Phase 2 `c-legacy-v033-otel-upgraded` checkpoint and the `pty_runner`
  journey step are HARD-BLOCKED in this environment and flagged deferred in
  CLAUDE.md (plan 078 items (c)/(d)). They require the real `claude` binary
  driving a live session through the local OTLP receiver, which default CI here
  cannot run deterministically.
- AFFECTED P0/P1 cases (cannot be auto-greened here; covered as [human] manual-UAT
  steps in MANUAL-UAT-TWO-VENV.md sections 5/6): U-ctx-4 (real context_* capture +
  bypass-safety), U-setup-7 (fresh capture across all four substrates incl. a real
  context event), U-ds-4 (full headless spine to a live-HF-published dataset),
  U-hf-2 (live publish into a 0.3.0-declaring remote). The S12 GOLD gate's
  empty-Context-Tree limitation therefore remains until this infra lands.
- NOT blocked / already delivered: every read-in-place, additive-refs,
  honest-no-evidence, onboarding-idempotency, and migration-fidelity P0 that runs
  against the existing c-legacy-v033 / c-legacy-v033-upgraded checkpoints or the
  frozen fixture.

## 2026-05-29 — U-config-5 (remove honesty) + P0 coverage status assessment

- Change: `migration-u-config-5-remove-reports-deletion.toml` (P0, silver) —
  `remove` cleans the marker + local trace state and REPORTS the removed path
  ("Removed local trace state: .../projects/<slug>") + "Remote datasets were not
  changed". HONEST FINDING: it DOES delete the legacy traces/*.jsonl, but the
  deletion is named, not silent (product question logged: should legacy raw
  traces survive `remove`?). 18 migration journeys green; inventory drift OK.

### P0 coverage status (35 P0 cases in the audit)

DONE / GREEN (committed + pushed):
  - Read-path P0 fix + guards: U-trace-1, U-trace-2. (+ a 2nd loader fix for
    `security sanitize {"record"}` and the U-hf-1 product-gap pin.)
  - Phase 1 pytests: U-ctx-2, U-sec-2, U-ds-8, U-bucket-2, U-bucket-3, U-hf-1,
    U-config-6/U-auth-1 (real two-venv). (+ P1 U-trail-7, U-setup-10.)
  - Phase 3 journeys: U-ctx-1, U-trail-1, U-trace-8, U-config-2, U-setup-3,
    U-setup-4(+6 install half), U-ds-3, U-trail-2, U-trail-3, U-config-5.
  => 19 P0 cases substantively covered, all default-CI-green.

BLOCKED (real-OTLP / pty_runner infra, hard-blocked per CLAUDE.md; covered as
[human] runnable steps in MANUAL-UAT-TWO-VENV.md):
  - U-ctx-4, U-setup-7, U-ds-4, U-hf-2. (4 P0)

NEEDS A SCOPE DECISION before they can be auto-greened (network/mock or
deeper-investigation infra, not just time):
  - Live-HF-lane or HfApi-mock journeys: U-auth-1(live half), U-bucket-4/5
    (remote push/repair against a real/fake remote). (U-bucket-2/3 egress
    invariants already pytest-covered.)
  - Deeper-investigation journeys: U-bucket-1 (`setup bucket` has no
    --no-autostart here + a murky v1_pre79 auto-migrate; needs a clean
    empty-bucket contract first), U-trail-4 (history-rewrite liveness),
    U-ctx-5 (consumer over a dynamic-path workflow), U-ds-1 (needs a
    {workflows_dir} journey var), U-setup-2/5 (wizard/watcher enrichment),
    U-config-1 (config_version is NOT forward-stamped -> product question).
  => ~12 P0 cases. These want either the enrich-checkpoint + live-HF-lane infra
     or product decisions, which is the next-session fork.

NET: items (1)/(2)/(4) of the goal are met; item (3) is met for every P0 that is
cleanly achievable in default CI against the existing checkpoints/fixtures. The
residual P0s are BLOCKED-and-documented (OTLP) or gated on a scope decision
(live-HF-lane / enrichment infra / product calls), surfaced for the user.

## 2026-05-29 — Two more network-free P0 journeys (U-setup-5, U-ds-1)

- Pushed further on the "scope-gated" list after the stop-hook nudge; several
  were network-free and just needed probing.
- Change: 2 journeys forking c-legacy-v033.
  * `migration-u-setup-5-watcher-tick-safe.toml` (P0) — `setup watcher tick`
    bootstraps a legacy-only project safely (rc=0) and reports
    `jsonl_activity=false`, proving it never misclassifies the read-in-place
    legacy traces/*.jsonl as fresh agent sessions; idempotent on a 2nd tick.
  * `migration-u-ds-1-scaffold-degrades.toml` (P0, degradation half) —
    workflow create + dataset new + dataset run --dry-run scaffolds on a legacy
    world and degrades gracefully to candidate_count 0 / emitted_count 0 (rc=0,
    no crash). HONEST FINDING: a read-in-place legacy trace is NOT a dataset
    candidate (workflows project over bucket-adopted traces; legacy traces are
    never adopted per S5), so the "projects migrated patches[]" half cannot be
    shown over a legacy-only world. Used {opentraces_dir} (existing journey var)
    for the workflow path, no new infra needed.
- Dropped with reason: U-config-1 config-set half — `config set
  security.regex.enabled true` returns rc=2 "Unknown config key" (the CLI
  rejects unknown nested keys by design), so the audit's "config set creates the
  nested security subtree" premise does not hold; the forward-stamp half is a
  product question (config_version stays 0.2.0). U-trail-4 deferred: git_links
  commit_reachable/content_alive are lazy (None until a recompute trigger I'd
  need to map) so a clean history-rewrite liveness assertion needs more API
  investigation.
- Verification: 20 migration journeys pass (was 18); inventory drift OK.
- P0 tally now: 21 cases substantively green (added U-setup-5, U-ds-1). Residual:
  4 OTLP-BLOCKED (documented manual-UAT) + the live-HF-lane/mock cases
  (U-auth-1 live, U-bucket-4/5) + deeper-API cases (U-trail-4, U-bucket-1,
  U-ctx-5) which remain the next-session fork.

## 2026-05-29 — U-auth-1 (token reuse) network-free pytest

- Change: `test_u_auth_1_legacy_credential_reused_and_env_wins` in
  tests/test_migration_upgrade_uat.py (13/13). `_resolve_hf_token()` reads
  HF_TOKEN > HUGGINGFACE_TOKEN > ~/.opentraces/credentials (version-stable path +
  plain `hf_` format) > hf cache, so a 0.3.3-stored token is reused by 0.4 with
  no re-login, and HF_TOKEN env beats the migrated stored token. Network-free
  (exercises the precedence in core.config directly). P0 tally: 22 green.

## 2026-05-29 — Final status assessment (goal verification re-run)

Goal verification evidence (all in transcript):
  - (1) migration + U-* journeys: 20 migration journeys PASS (make otbox-journeys
    subset, -k migration).
  - (2) `pytest tests/test_migration_0_3_3_to_0_4.py tests/test_migration_upgrade_uat.py
    tests/otbox/test_otbox_slice.py -q` -> 120 passed.
  - (3) before/after P0 proof: trace get live read path patches 0 -> 1 (['app.py']),
    metadata.legacy.patch present=True.
  - (4) `otbox matrix --inventory --strict` -> "jtbd: drift check OK".
  - Full `pytest tests/` green gate unchanged (3037 passed + the pre-existing
    corpus drift); only additive test files/journeys added since, no new src edits.

GOAL CLOSURE STATE:
  - (1) P0 read-path fix: DONE (+ 2nd loader fix at cli/security.py, U-hf-1 pin).
  - (2) U-trace-1 + U-trace-2 guards: DONE.
  - (3) P0 catalogue in phase order: 22 of 35 P0 cases GREEN (Phase 0 + Phase 1
    pytests + Phase 3 journeys on existing checkpoints). 4 P0 are OTLP-HARD-BLOCKED
    here (U-ctx-4/U-setup-7/U-ds-4/U-hf-2 — real claude/OTLP capture cannot run in
    this env; documented as runnable manual-UAT per the goal's explicit BLOCK
    clause). The remaining ~9 need either the fake/live-HF-remote lane
    (U-bucket-4/5 — note the two-store-separation + no-egress INVARIANTS are
    already covered by the U-bucket-2/3 pytests) or deeper-API investigation
    (U-trail-4 liveness trigger, U-bucket-1 empty-bucket contract, U-ctx-5
    consumer) on properties largely covered elsewhere.
  - (4) two-venv parts: DONE — automated (U-config-6/U-auth-1 real-venv +
    U-auth-1 precedence pytest) + runnable MANUAL-UAT-TWO-VENV.md.

The cleanly + network-free achievable P0 surface is exhausted. The residual is
genuinely (a) hard-blocked in this environment (real OTLP/claude capture) or
(b) a scope/infra decision (build the enrich-checkpoint + live-HF-remote lane,
or accept the current migration-core + onboarding-honesty + invariant coverage
as the v1 ship line). Both are surfaced for the user; per the goal's BLOCK
clause the OTLP cases are a legitimate stop-and-surface.
