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
