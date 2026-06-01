# RUNLOG — Plan 090: Generalise Trace Capsules as Privacy-Bounded Usage Episodes

Worktree: `community-traces-capsule` · Branch: `feat/capsule-usage-episode` (off `feat/capsule-dependency-unblock`).
Goal: reframe the capsule into a privacy-bounded usage episode ADDITIVELY, proven by a green end-to-end test.

Non-negotiables: `REQUIRED_KEYS` + `CAPSULE_SCHEMA_VERSION` byte-unchanged (`SECURITY_VERSION` may bump);
`test=null` first-class; default-EXCLUDE prompt-bearing fields (opt-IN via `--include-prompts`); wire markers +
`capsule` command noun unchanged; no `TraceRecord`/HF-features change; no v2/v3/auto-populate/capture changes.

---

## Orientation (pre-implementation)

- Branch `feat/capsule-usage-episode` created off `feat/capsule-dependency-unblock`. Venv green:
  `pytest tests/test_capsule*.py -q` → 66 passed, 2 skipped (live-HF/network) at baseline.
- Re-verified 4 load-bearing sites: `contract.py:32-48` (15 REQUIRED_KEYS; test/summary/environment/product/
  bundle/privacy_scope absent), `share.py:239-243` vs `:258-264` (the sha-pin bug), `classifier_tool.py:36`
  (matched_text leak).
- **R5 mechanism decided (operator):** the PUBLISHED `capsule.json` must carry the sha — return-value-only is
  insufficient. After `upload_folder` returns the oid, rewrite `capsule["share"]` with the sha and re-`upload_file`
  just `capsule.json` (single-file commit). Embedded `share.revision` = the FIRST commit's oid (only oid knowable
  at rewrite time); correct because the bundle is byte-identical across both commits. Costs one extra small upload
  per publish — accepted for a self-sufficient artifact.

---

## Phase 1 — Understand (parallel map)

Ran an 8-reader workflow (`capsule-090-understand`) mapping contract/export, redaction, security registry,
classifier, slices, render+CLI, share+deps, and test patterns. Key structural findings that shaped the build:

- `freeze_capsule` (contract.py:88-140) is a literal builder, NO `**extra`; ONE prod call site (export.py:398).
- `sanitize_dict` (security/pipeline.py:166) runs ONLY `Detector`-protocol tools → `business_logic` must be a
  registered Detector and join `REDACTION_FLOOR`; a `capsule_scope` Transformer is filtered OUT of that path, so
  envelope exclusion is a standalone helper applied inside `redact_envelope`.
- The classifier `matched_text` does NOT reach any capsule today (`apply()` drops flags to counts; verdict
  payload is transient/unpersisted; export.py never reads classifier output). U0 is therefore defensive
  hardening at the `flag_payload` source.
- `EXPECTED_TOOL_ORDER` (tests/security/test_tools_registry.py:29-37) is an exact-equality assertion → must be
  updated when `_TOOLS` grows. `SECURITY_VERSION` lives at security/version.py:23, informative-only in manifests.
- `aws_account_id` regex captures `group(1)` (12 digits) → its redaction span must be `start(1)..end(1)`.

(Note: the inherited worktree had an unstaged deletion of repo-root `otd`; it predates plan 090 and is left
untouched to stay in scope.)

---

## Phase 2 — Implementation

### U0 — two bug fixes (DONE, green)

**Files:** `core/capsule/share.py`, `security/tools/classifier_tool.py`, `tests/test_capsule_publish_redaction.py`.

1. **sha-pin (share.py `publish_capsule`).** Moved `base` to outer scope; after the folder commit returns its
   oid, re-stamp `capsule["share"]` with `{capsule_url, human_url, revision, published_revision}` all pinned to
   the oid and re-`upload_file` ONLY `capsule.json` (byte-identical serialization to `write_capsule_dir`:
   `json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True) + "\n"`). Guarded on `revision != "main"`.
   Return dict gains `published_revision`. Added a `revision` key to the share block so the published artifact
   carries both `share.revision` and `share.published_revision` = oid (satisfies the goal's literal check).
2. **classifier matched_text (classifier_tool.py).** `flag_payload` now keeps only `{pattern, severity}` —
   dropped `matched_text` (the secret) AND `reason` (can echo content). Verified by grep that NO consumer reads
   the dropped fields: `_classifier_flag_count` (core/pipeline.py:127), `flags_reviewed` (security/pipeline.py:45),
   and `classifier_tool.apply` all use `len(flags)`; the web review UI (`server.py:679`) reads a SEPARATE
   `trace["_security_flags"]` structure, not this verdict payload. No test pins the payload shape
   (`tests/security/test_security.py` asserts on `ClassifierResult.flags[*].pattern_name`, an untouched path).

**Evidence:** new `test_published_capsule_json_is_sha_pinned` asserts the folder upload carries the stale
`main`/None share but the re-uploaded `capsule.json` is sha-pinned (`share.revision == share.published_revision
== oid`, oid in `capsule_url`, no secret). `pytest tests/test_capsule_publish_redaction.py
tests/security/test_security.py tests/security/test_pipeline_api.py -q` → 163 passed.
`pytest tests/test_capsule*.py -q` → 67 passed, 2 skipped (was 66+2; +1 new test). No regression.

**Next:** U1 — thread `product` / `summary.outcome_taxonomy` / `privacy_scope` through `freeze_capsule`
(defaulted params so the 3 existing freeze_capsule test builders stay green) + assert REQUIRED_KEYS /
CAPSULE_SCHEMA_VERSION byte-unchanged.

### U1 — additive episode keys (DONE, green)

**Files:** `core/capsule/contract.py`, `core/capsule/summary.py`, `core/capsule/export.py`.

- `freeze_capsule` gained two keyword-only params `product: dict|None=None`, `privacy_scope: dict|None=None`
  (DEFAULTED → the 3 existing freeze_capsule test builders + the prod call site that don't pass them stay
  green untouched). Emitted additively as `"product": product` (null-tolerant anchor) and
  `"privacy_scope": privacy_scope or {}`. NOT added to `REQUIRED_KEYS`; `CAPSULE_SCHEMA_VERSION` untouched.
- `build_summary` now emits `outcome_taxonomy` via `_derive_outcome_taxonomy(success, terminal_state,
  is_failure)` — honestly derives only `completed`/`abandoned`/`unclear` from the 3 coarse signals; the full
  vocab `OUTCOME_TAXONOMY_VALUES` reserves `workaround_found`/`blocked_by_*` rather than fabricating causes.
- `export_capsule` gained `product: str|None=None` + `include_prompts: bool=False`; assembles the `product`
  anchor (`{name, binding:"inferred"}` — honest: no captured per-step product label) and a structural
  `privacy_scope` (`system_prompt_included`/`reasoning_included` gated on `include_prompts`,
  `messages_included`, `messages_completeness`, `steps_included`, `redaction_floor`, `developer_approved` —
  bools/ints/strings only, NEVER a classifier verdict). NB: the exclusion that physically enforces
  `system_prompt_included=False` lands in U3/U4; the CLI flags land in U5.

**Evidence:** inline check confirms REQUIRED_KEYS=15 + version unchanged, old freeze_capsule callers default the
new keys (product=None, privacy_scope={}), new kwargs thread through, validate_capsule ignores them, taxonomy
derivation maps completed/abandoned/unclear honestly. `pytest tests/test_capsule*.py -q` → 67 passed, 2 skipped.

**Next:** U2 — `product_episode` slice template + `suggest_consumes()` stderr hint; wire product-bounded
slicing into `export_capsule` (fallback to radius slice on no-match; stamp `product_inferred_not_captured`).

### U2 — narrow-by-default scoping (DONE, green)

`core/trace_slices.py`: `SLICE_TEMPLATES` widened to `("bursts","product_episode")`; new `slice_for_product`
(wraps `slice_by_steps`, returns the standard `opentraces.trace_slice.v1` payload, `None` on no-match) +
`_node_references_product` (heuristic substring match over node tool_name/text_preview/files/subagent prompt).
`export_capsule` now uses product-bounded slicing when `--product` is set (falls back to radius slice on
no-match), stamping `product_inferred_not_captured` (+ `product_episode_no_match` when nothing referenced it).
`enrichment/dependencies.py::suggest_consumes` (pure, stderr-only, name-only `package:<name>=` hints, never
auto-writes) + re-exported. NB: product_episode feeds `export_capsule`, NOT the `trace slice` CLI — keeps the
change out of `cli/trace.py`. Verified inline; `pytest tests/test_capsule*.py + slice tests` → 168 passed.

### U3 + U4 — layered redaction + default-exclude polarity (DONE, green)

`SECURITY_VERSION` 0.5.0 → 0.6.0. New `security/tools/business_logic_tool.py` (`BusinessLogicDetector`,
DetectorMixin; the 4 classifier infra regexes promoted to redactable spans — `aws_account_id` spans `group(1)`
only). New `security/tools/capsule_scope_tool.py` (`CapsuleScopeTransformer` + pure `apply_field_exclusion` /
`scan_excluded_paths` + `DEFAULT_PROMPT_EXCLUDE`). Registered both in `_registry.py` canonical order
(business_logic after llm_pii; capsule_scope after path_anonymizer). `REDACTION_FLOOR` →
`("regex","entropy","business_logic")` (runs via explicit `tools=`, so on for every capsule regardless of
opt-in). `redact_envelope(payload, *, exclude_paths=None)` applies exclusion before the floor and records
`fields_excluded`/`excluded_field_paths` (recovered by scanning markers → idempotent across `ensure_redacted`).
`export_capsule` excludes `DEFAULT_PROMPT_EXCLUDE` unless `include_prompts`. **No `core/config.py` change**
(out of scope): tools read cfg defensively via `cfg_block`→None, default disabled; the floor bypasses opt-in.
Key honesty: `business_logic` UNIQUELY redacts internal_url + internal_hostname (regex/entropy miss those);
regex also covers the DB string + ARN, so by_tool attribution splits — all values still redacted. Updated the
registry test order constant + 2 CLI surface tests (tool count 7→9, doctor ordered list). 359 passed.

### U5 + U6 — preview verb, consent-gate parity, naming reframe (DONE, green)

`cli/capsule.py`: `--product`/`--include-prompts` added to `_export_options` (inherited by export/share/issue/
preview), threaded through `_do_export` → `export_capsule`. New read-only `preview` verb (manifest by
field-path + business_logic findings + privacy_scope + destinations; `--json`; calls NONE of
write_capsule_dir/publish). Shared `_confirm_egress` + `_egress_destinations` lifted from issue_cmd and applied
to BOTH `issue --publish` and `share --publish` (added `--yes` to share). `_hint_consumes` wires the
stderr-only suggest_consumes. `render.py`: 3-way banner (Support Packet / blocked episode / usage episode) at
the single `is_failure` label site — markers + command noun untouched. Group + module docstrings reframed.
Updated 2 render-banner test assertions. CLI help verified; registry + render tests green.

### U7 — end-to-end + regression + docs (DONE, green)

New `tests/test_capsule_usage_episode.py` (7 tests, all green): real-pipeline `export_capsule` + subprocess
`capsule preview` over a seeded trace (conftest isolates HOME → in-proc seed + subprocess share one bucket),
plus hermetic `redact_envelope` / `freeze_capsule` / mocked-publish layers. Proves: test=null exports/previews/
opens/renders (R2); preview shows manifest+privacy_scope and writes nothing under `.opentraces/capsules` (R3);
internal URL/host/DB redacted as spans + system_layer/reasoning excluded by default, included on opt-in (R4);
REQUIRED_KEYS/CAPSULE_SCHEMA_VERSION frozen (R1); published capsule.json `share.revision` is a 40-hex oid +
`published_revision` non-null + no matched_text (R5); classifier flags carry only `{pattern,severity}` (U0).
Capture gaps (WebFetch/URL docs, runtime versions, heuristic product binding) documented in
`core/capsule/README.md`.

---

## Phase 3 — Adversarial review + fix

Ran a 6-dimension review workflow (additivity / U0 / redaction / polarity / cli / scope), each finding
adversarially verified (try-to-refute, default refuted). 4 dimensions clean; 3 findings confirmed:

1. **HIGH — sha-pin URL resolution bug (real).** The fix returned `resolve/<oid_A>` (the folder commit), but
   HF `/resolve/<oid>/` serves the blob AS AT that commit, so the handed URL served the STALE `main`/None
   capsule.json; the SHA-pinned capsule.json lives at the SECOND (re-upload) commit `oid_B`, whose oid I
   discarded. **Fix:** capture `pin_commit.oid` and hand out `resolve/<oid_B>` so the URL serves the pinned
   capsule.json. The reviewer's literal suggestion (embed `oid_B` in the blob at `oid_B`) is mathematically
   impossible — a blob cannot contain its own commit's oid — so `published_revision` (= `oid_A`, non-null,
   immutable) is the load-bearing marker; the embedded self-URL references the byte-identical data commit. This
   is the precise refinement of the operator's R5 decision (the operator accepted embedding `oid_A`; the gap
   was that the *handed* URL must point at the re-stamp commit to actually serve the pinned bytes).
2. **MEDIUM — the sha-pin tests asserted the in-memory bytes, not the resolved URL (real).** **Fix:** the U7
   publish test now models per-commit HF snapshots and asserts that resolving the *handed* `info["capsule_url"]`
   (`/resolve/<oid_B>/`) yields a capsule.json with `published_revision` non-null + 40-hex `revision`, and that
   the stale folder commit (`oid_A`) is NOT what's handed out.
3. **MEDIUM — out-of-scope `otd` deletion (real, inherited).** The worktree carried a pre-existing unstaged
   deletion of the `otd` dev shim (present at orientation). **Fix:** `git restore --source=...dependency-unblock
   -- otd` so the plan-090 working tree is scoped to capsule/security/test files only.

Refuted (not real): the `polarity` and `cli` dimensions each raised 1 finding, both refuted on verification
(privacy_scope is honest; preview is genuinely read-only). `additivity` + `redaction` raised none.

Post-fix: `pytest tests/test_capsule_publish_redaction.py tests/test_capsule_usage_episode.py -q` → 10 passed.

---

## Phase 4 — Final verification (goal met)

Green across every risk surface (post-fix):
- `pytest tests/test_capsule*.py -q` → **74 passed, 2 skipped** (incl. new `test_capsule_usage_episode.py`; the
  2 skips are the live-bucket export-integration tests).
- security suite → 367 passed; cli security (registry surface) → green.
- Targeted collateral (SECURITY_VERSION bump + registry change): `bucket_store`, `bucket_remote`,
  `remote_ready`, `pipeline_version`, `ingest`, `doctor_panels`, `migration_upgrade_uat`,
  `otbox_journey_assertions` → **76 passed, 1 skipped**. No collateral breakage.
- `test_capsule_usage_episode.py -v` → 7/7 PASSED (R1 frozen-contract, R2/R4 export+redact+exclude, R3
  preview-read-only, R4 hermetic exclude+spans, R5 sha-pin 40-hex, U0 classifier no-matched_text).

Transcript-surfaced evidence (per the goal):
- LIVE `opentraces capsule preview demo-ue --product acme-sdk --json` → floor `[regex,entropy,business_logic]`,
  `by_field_path`, `business_logic.findings=4`, `fields_excluded=2` (both reasoning_content), full
  `privacy_scope`; read-only check confirmed NO file under `.opentraces/capsules`.
- Mocked-HF publish → handed URL resolves at the re-stamp commit; resolved capsule.json `share.revision` is
  40-hex, `published_revision` non-null, no `matched_text`.

Scope confirmed: branch `feat/capsule-usage-episode`; only capsule subsystem + security tool registry +
plan-090-named files + the new test modified; `otd` restored; `git diff` shows ZERO lines touching
`REQUIRED_KEYS` / `CAPSULE_SCHEMA_VERSION`; no `packages/` (schema) or `publish/huggingface/` change. Out of
scope (v2/v3/auto-populate/capture) untouched. Changes left UNCOMMITTED on the branch (the goal did not request
a commit).

**GOAL MET.**
