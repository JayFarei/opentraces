# 0.3.3 -> 0.4 Upgrade-UAT Coverage Audit

Source: 18-agent workflow `upgrade-uat-coverage-map` (run wf_eb8533cb-ef6), 2026-05-29.
Mapped Map complete: 262 documented features, 125 commands, 15 covered scenarios.

## Executive summary

The plan-085 suite is a strong MIGRATION-CORE proof but a weak UPGRADE UAT. Its strength is the one confirmed breaking change: schema 0.3.0 -> 0.6.0 removing Outcome.patch. That single change is covered to a high standard — S2 (exactly-one-field-dropped audit), S3 + S3-frozen-world (migrate_record reconstructs patches[], preserves the raw diff under metadata.legacy.patch, byte-idempotent, real-v0.3.3-built record), and S6/S7 (HF shard forward-migration + schema-ahead refusal, the latter even driving the real v0.3.3 client). Seven otbox journeys (S1/S5/S8/S9/S10/S11/S12) plus two checkpoints (c-legacy-v033, c-legacy-v033-upgraded) prove read-compat, read-in-place / no-auto-adoption, config forward-compat, git-ref additivity, idempotency, and non-destructive reads.

But two facts gut the breadth claim. FIRST, a code-level finding I confirmed: the live read path (cli/trace.py:1058 _read_trace_record_from_path, also 1271/1309/1559/1775) calls TraceRecord.model_validate_json DIRECTLY with NO migrate_record wrap; migrate_record is invoked only in publish/huggingface/upload.py. So a 0.3.3 record's diff is SILENTLY DROPPED by every live inspection verb (trace get/query/index/map/slice), and the documented promise "trace get returns the migrated record with patches[] populated + metadata.legacy.patch preserved" is FALSE on the path users actually hit. The covered tests assert migrate_record in isolation; no journey asserts the diff survives through the live CLI. This is a real data-presentation-loss bug, not merely a test gap.

SECOND, every post-0.3.3 subsystem an upgrader actually adopts is untested on upgrade: the entire setup-wizard / setup-verb family, auth survival + reuse, the full dataset create/run/review/publish arc, capture-otlp / Context Tree real capture, security tool RUNS (only the disabled defaults are checked, never a sanitize over legacy content), bucket remote sync / repair / replay / verify / prune, dataset schedule, workflow create/templates, trace teleport, and the trail blame commit/pr/graph surfaces. S6/S7 prove the HFUploader guards only in isolation with a mocked HfApi; per project memory those code paths are unreachable from the live dataset publish CLI, so forward HF shard migration on a real upgrade is an unverified product gap. The S12 GOLD gate runs a SYNTHETIC fake-harness capture that emits zero context_* events, so "all three substrates coherent" is really trace+trail coherent with the Context Tree provably empty.

## Honest coverage verdict

It is migration-core, not a genuine end-to-end upgrade UAT. The suite rigorously proves the schema-layer mechanics of the one breaking change and the read-in-place / no-auto-adoption / additive-refs invariants — that is real, valuable work and the strongest part. But measured against documented user-facing functionality, only roughly 25-35% of upgrade behavior is tested, and most of that is read-compat-no-crash plus the migration unit. The breadth is hollow in three ways: (1) the migration is proven only at the library level and on the HF publish path, while the live inspection CLI provably does NOT run it (confirmed at cli/trace.py:1058) — so the headline "your old traces are readable with patches[]" claim is actually broken on the surface users hit; (2) the entire forward-onboarding surface a real upgrader runs (setup verbs, auth, datasets, workflows, security runs, bucket remote, capture-otlp) is untested on upgrade; (3) the one GOLD end-to-end gate uses a synthetic capture with an empty Context Tree and never reaches live HF egress. So: genuine and trustworthy for "0.3.3 data is non-destructively migratable and the box does not crash on read," but NOT a credible UAT for "a 0.3.3 user can pip-upgrade and successfully use 0.4."

## Biggest gaps (ranked)

1. Live read path drops the legacy diff: trace get/query/index/map/slice call model_validate_json with no migrate_record wrap (cli/trace.py:1058), so the documented patches[]+metadata.legacy.patch fidelity is FALSE on the surface users hit. This is a confirmed bug, ranked #1.
2. setup upgrade (the documented PREFERRED 0.3.3->0.4 path) and the whole setup-verb / first-run wizard family are completely undriven end-to-end on a legacy world — hook/skill refresh, config-version forward-patch, idempotency all unverified.
3. The full dataset create/run/review/publish arc is untested on upgrade; combined with the project-memory note that HFUploader schema-safety is unreachable from the live dataset publish CLI, forward HF shard migration on a real upgrade is an unverified product gap (S6/S7 are isolation-only).
4. Security tool RUNS are untested on migrated data: only disabled defaults checked. The 0.3.3 privacy-tier knob is gone and every tool defaults OFF, so an upgrader who relied on tier redaction is silently unprotected, and custom_redact_strings/sanitize over a legacy 0.3.0 record is never proven.
5. Trail survival/anchor honesty on migrated traces: trail track / blame commit must return unknown / empty for reconstruction-only legacy patches, never fabricated alive/lost verdicts that corrupt review decisions — entirely untested (only the new-capture alive case in S9/S12).
6. auth survival + reuse: no test proves a 0.3.3-stored hf_token survives migration and is reused (not re-prompted, not shadowing HF_TOKEN) by the new bucket/dataset remote consumers.
7. Two-store egress separation (raw bucket vs dataset rows) — the security-critical 'dataset publish never egresses the raw bucket' invariant that did not exist in 0.3.3 — is asserted nowhere; a leak of raw transcripts is a data-exposure incident.
8. capture-otlp / real Context Tree: no migration test ever captures a real context_* event or proves the bypass-safe property (down receiver never blocks agent traffic) or the idempotent ~/.claude/settings.json patch on an upgrader's pre-existing settings.
9. Net-new ctx / bucket / trail read verbs (ctx tree/show/step/reads/writes/diff/resume/prune, bucket remote/repair/replay/verify/prune/prefetch, trail blame commit/pr/graph) crash-vs-honest-empty behavior on legacy-only data is largely untested.
10. Removed CLI verb opentraces pull errors-cleanly-with-replacement-hint, and the decommissioned TUI/viewer review path redirect, are untested documented behavior changes that break upgrader automation silently.

## Proposed upgrade-UAT test cases

| ID | Pri | Subsystem | Status | Mechanism | Title |
|----|-----|-----------|--------|-----------|-------|
| U-trace-1 | P0 | trace | uncovered | otbox_journey | Live trace get on a legacy 0.3.0 record exposes patches[] + metadata.legacy.patch (or honestly fails, documenting the drop) |
| U-trace-2 | P0 | trace | uncovered | pytest | trace read loader applies migrate_record (unit, fix-guard for all read surfaces) |
| U-trace-8 | P0 | trace | uncovered | otbox_journey | trace map --bursts on a legacy 0.3.0 trace degrades cleanly |
| U-trace-10 | P1 | trace | uncovered | otbox_journey | trace slice step-window works, patch-window empties cleanly, on a legacy trace |
| U-trace-12 | P1 | trace | uncovered | otbox_journey | trace teleport export/open round-trips a legacy trace without losing the diff |
| U-trace-13 | P1 | trace | uncovered | otbox_journey | trace get --resume / --at-step on a legacy snapshot-less trace fails honestly |
| U-setup-1 | P0 | capture-setup | uncovered | real-v033-venv | setup upgrade against a real 0.3.3 install refreshes skill + hooks idempotently, non-destructively |
| U-setup-2 | P0 | capture-setup | uncovered | otbox_journey | setup wizard / interview over an upgraded box preserves legacy config and does not auto-enable opt-in subsystems |
| U-setup-3 | P0 | capture-setup | uncovered | otbox_journey | init / init --import-existing idempotency and backfill on an already-enrolled legacy project |
| U-setup-4 | P0 | capture-setup | partial | otbox_journey | setup git on an upgraded legacy repo promotes a provisional trace and writes notes without a prior event log |
| U-setup-5 | P0 | capture-setup | partial | otbox_journey | watcher tick / scan_project on a legacy-only project bootstraps safely and never re-ingests legacy JSONL |
| U-setup-6 | P0 | capture-setup | uncovered | otbox_journey | Full setup chain on a migrated project respects read-in-place and installs cleanly |
| U-setup-7 | P1 | capture-setup | partial | real-v033-venv | Fresh 0.4 capture in an upgraded repo emits patches[] (no Outcome.patch) across all substrates incl. a real context_* event |
| U-setup-8 | P1 | capture-setup | partial | otbox_journey | Per-tool setup verbs (trufflehog/privacy-filter/llm-review --enable) extend a legacy config cleanly |
| U-setup-9 | P1 | capture-setup | partial | pytest | Adapter/importer contract emits schema-0.6.0 records (Hermes diff-only import + protocol regression guard) |
| U-setup-10 | P1 | capture-setup | uncovered | pytest | Migrated 0.3.3 trace preserves original attribution, not re-derived; honestly lacks new evidence fields |
| U-auth-1 | P0 | auth-remotes | uncovered | otbox_journey | 0.3.3-stored hf_token survives migration and is reused (not re-prompted, not shadowing HF_TOKEN) |
| U-auth-2 | P1 | auth-remotes | uncovered | otbox_journey | Legacy token drives the new bucket-remote and dataset-remote auth paths without re-login |
| U-auth-3 | P1 | auth-remotes | uncovered | otbox_journey | Headless HF_TOKEN run on an upgraded world forces no auth login |
| U-bucket-1 | P0 | bucket | uncovered | otbox_journey | setup bucket on a restored 0.3.3 world: v2 layout, token reuse, no legacy adoption; --migrate degrades honestly |
| U-bucket-2 | P0 | bucket | uncovered | pytest | No surprise egress: capture with a pre-existing 0.3.3 hf_token stays local until explicit setup bucket + push |
| U-bucket-3 | P0 | bucket | uncovered | pytest | Two-store egress separation: dataset publish never egresses the raw bucket; bucket push never writes dataset rows |
| U-bucket-4 | P0 | bucket | uncovered | otbox_journey | bucket remote push targets a distinct private-bucket repo, never the legacy 0.3.3 dataset repo; diff/pull safe on empty |
| U-bucket-5 | P0 | bucket | uncovered | otbox_journey | repair/rebuild/replay never auto-adopt legacy JSONL and preserve legacy git history |
| U-bucket-6 | P1 | bucket | partial | otbox_journey | Net-new bucket verbs degrade gracefully on the empty boundary and validate on first capture |
| U-bucket-7 | P1 | bucket | partial | otbox_journey | Remote-bucket / resolve-back reads behave on empty boundary and error legibly for legacy ids predating the bucket |
| U-bucket-14 | P1 | bucket | uncovered | manual-uat | Manual UAT: a 0.3.3 user's `push` mental model is honestly redirected (no surprise egress, no surprise silence) |
| U-trail-1 | P0 | trail | partial | otbox_journey | trail track on a migrated legacy trace returns unknown/no-anchor, never fabricated survival or confidence |
| U-trail-2 | P0 | trail | uncovered | otbox_journey | trail blame commit honest empty-state on a pre-upgrade commit; resolves only real 0.4 anchors |
| U-trail-3 | P0 | trail | uncovered | otbox_journey | git-backfill on an upgraded repo with no prior notes ref produces honest correlations |
| U-trail-4 | P0 | trail | uncovered | otbox_journey | GitLink liveness recompute against a force-pushed-away commit reports content_alive=false without error |
| U-trail-5 | P1 | trail | uncovered | otbox_journey | trail graph / blame pr render empty-but-valid on legacy-only, populated on the one 0.4 capture |
| U-trail-6 | P1 | trail | uncovered | otbox_journey | backfill / watcher tick over a legacy 0.3.0 inbox record is migration-aware or honestly skips |
| U-trail-7 | P1 | trail | partial | pytest | Migration emits zero TrailEvents; migrated patches[] are hunk-granular (or labeled-degraded) |
| U-trail-8 | P1 | trail | partial | otbox_journey | bucket replay rebuilds the canonical event ref byte-identically on an upgraded world |
| U-trail-9 | P2 | trail | partial | otbox_journey | Public/hidden trail verb split holds on an upgraded box |
| U-ctx-1 | P0 | context-tree | partial | otbox_journey | ctx tree/show/step/reads/writes/diff/compactions/resume/prune/resolve/anchor-for-step return honest no-evidence on a legacy trace |
| U-ctx-2 | P0 | context-tree | partial | pytest | Migrated 0.3.0 record validates with context_tree_summary and per-step context_node_id empty/absent |
| U-ctx-3 | P1 | context-tree | uncovered | otbox_journey | setup capture-otlp preserves a pre-existing ~/.claude/settings.json and is idempotent |
| U-ctx-4 | P1 | context-tree | uncovered | real-v033-venv | capture-otlp bypass-safety + first real context_* capture inside a migrated repo |
| U-ctx-5 | P0 | context-tree | uncovered | otbox_journey | Downstream consumer degrades gracefully when ctx/trail evidence is absent on a legacy trace |
| U-ds-1 | P0 | workflows-datasets | uncovered | otbox_journey | dataset new --workflow then run over read-in-place legacy traces projects migrated patches[] |
| U-ds-2 | P0 | workflows-datasets | uncovered | pytest | dataset new --rows-file (the documented pull-replacement) migrates legacy 0.3.0 rows -> patches[] |
| U-ds-3 | P1 | workflows-datasets | uncovered | otbox_journey | opentraces pull errors cleanly with a non-zero exit and a replacement hint |
| U-ds-4 | P0 | workflows-datasets | uncovered | real-v033-venv | Full headless spine from a freshly-migrated 0.3.3 project to published 0.6.0 rows |
| U-ds-5 | P1 | workflows-datasets | uncovered | otbox_journey | dataset review/approve/--all + state-file no-collision; --web/--tui decommission notice |
| U-ds-6 | P2 | workflows-datasets | uncovered | otbox_journey | Net-new dataset/workflow/schedule surfaces report honest empty-state; schedule preserves gate-bypass invariant |
| U-ds-7 | P0 | workflows-datasets | uncovered | pytest | OT_RUN_PACKET candidate carries patches[]/metadata.legacy.patch, not Outcome.patch |
| U-ds-8 | P0 | workflows-datasets | uncovered | pytest | Post-processor receives a migrated 0.6.0 record; legacy-shaped output rejected (and --strict raises); doctor probes it |
| U-sec-1 | P1 | security | partial | otbox_journey | Legacy privacy-tier knob is honestly neutralized and the upgrade UX surfaces that tier redaction is gone |
| U-sec-2 | P0 | security | uncovered | pytest | security sanitize over legacy/migrated records: payload shapes, all-off default, canonical order, new metadata |
| U-sec-3 | P0 | security | partial | pytest | Sanitize workflow finds reconstructed patches[] and survives an absent trail companion; custom_redact_strings honored |
| U-sec-4 | P1 | security | uncovered | otbox_journey | setup <tool> --enable patches a legacy config cleanly and sanitize then runs the enabled tools (CI flow) |
| U-sec-5 | P1 | security | partial | otbox_journey | doctor --json security block well-formed/green on a migrated config; SECURITY_VERSION reported (resolves 0.4.0-vs-0.5.0 doc drift) |
| U-sec-6 | P2 | security | uncovered | pytest | security tools info <tool> --json and field-type/redaction-marker/path-anonymization behave on legacy-derived content |
| U-hf-1 | P0 | schema-hf-publish | partial | pytest | Confirm/refute whether live dataset publish reaches HFUploader schema-ahead/migrate/dedup paths |
| U-hf-2 | P0 | schema-hf-publish | partial | real-v033-venv | Live upgrade publish into a 0.3.0-declaring remote triggers shard migration; into a newer remote fails closed (exit-3) |
| U-hf-3 | P1 | schema-hf-publish | partial | pytest | Mixed-version JSONL loads uniformly; migrated record stamps schema_version 0.6.0; content_hash dedup break characterized |
| U-hf-4 | P1 | schema-hf-publish | partial | otbox_journey | git_links liveness / committed-commit_sha / lifecycle-promotion behave on migrated reconstructed-anchor traces |
| U-hf-5 | P1 | schema-hf-publish | partial | otbox_journey | Migration auto-fires on every read/publish surface; no public migrate verb; schema CHANGELOG/rationale + registration exist |
| U-hf-6 | P1 | schema-hf-publish | uncovered | pytest | load_dataset of a freshly-published 0.4 dataset matches regenerated 0.6.0 features; publish gates handle no-Trail rows |
| U-hf-7 | P2 | schema-hf-publish | uncovered | pytest | Constructing a TraceRecord with Outcome.patch under the 0.6.0 package fails loudly; consumer reading outcome.patch fails predictably |
| U-config-1 | P0 | config-state | partial | otbox_journey | config_version migrated/stamped forward (idempotent); config set creates missing nested security subtree without clobbering legacy keys |
| U-config-2 | P0 | config-state | uncovered | otbox_journey | Legacy .opentraces.json marker (no bucket defaults / no post_processors) loads and defaults sanely |
| U-config-3 | P0 | config-state | partial | otbox_journey | doctor --json reports all new subsystems as not-yet-configured (not errored) on a legacy world |
| U-config-4 | P1 | config-state | partial | otbox_journey | status honestly distinguishes legacy on-disk traces from bucket captures; full --json agent-flow bundle returns honest envelopes |
| U-config-5 | P0 | config-state | uncovered | otbox_journey | remove / remove --all cleans mixed legacy + 0.4 state consistently and never silently destroys legacy devtime JSONL |
| U-config-6 | P1 | config-state | partial | real-v033-venv | Real installed 0.4.0 CLI first-run migrates a 0.3.3 home config in place |
| U-config-7 | P2 | config-state | uncovered | manual-uat | Decommissioned TUI/viewer entrypoints fail honestly; dataset review is the documented replacement |

### Scenarios (detail)

#### U-trace-1 (P0, uncovered) — Live trace get on a legacy 0.3.0 record exposes patches[] + metadata.legacy.patch (or honestly fails, documenting the drop)
- **subsystem**: trace  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run `opentraces --json trace get {legacy_trace_id}` and assert the emitted TraceRecord has non-empty patches[] and metadata.legacy.patch. Today this FAILS: cli/trace.py:1058 calls model_validate_json with no migrate_record wrap, so the diff is dropped on read.
- **proves**: Whether the documented patches[]-populated contract holds through the live CLI; this test is the regression guard for the confirmed read-path bug.

#### U-trace-2 (P0, uncovered) — trace read loader applies migrate_record (unit, fix-guard for all read surfaces)
- **subsystem**: trace  **mechanism**: pytest
- **scenario**: Feed _read_trace_record_from_path a frozen 0.3.0 JSONL line (migration/fixtures/legacy_world_v033) and assert the returned record carries reconstructed patches[] with RECONSTRUCTED_CAPTURE_METHOD and metadata.legacy.patch == the raw diff. Cover the parallel direct-validate sites at trace.py:1271/1309/1559/1775.
- **proves**: The read loader, not just the HF publish path, runs the registered migration so no inspection verb silently loses the legacy diff.

#### U-trace-8 (P0, uncovered) — trace map --bursts on a legacy 0.3.0 trace degrades cleanly
- **subsystem**: trace  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run `trace map {legacy_trace_id} --bursts --no-commit-lookup --json`; assert rc=0 with either empty bursts (current drop-patch path) or diff-derived bursts whose burst_commit_sha is null and intent fields present-but-empty. Document which occurs.
- **proves**: Burst projection over a legacy trace lacking patch nodes and hook trail does not crash and reports absent commit/intent honestly.

#### U-trace-10 (P1, uncovered) — trace slice step-window works, patch-window empties cleanly, on a legacy trace
- **subsystem**: trace  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run `trace slice {legacy_trace_id} --from-step 0 --to-step 3` and `--around-step 1 --radius 2` (assert bounded packets from steps[]), then `--template bursts` and `--around-patch <id>` (assert rc=0 empty or clear no-such-patch, never a traceback).
- **proves**: Step-range slicing works on legacy records; patch-window templates degrade gracefully when no patch ids resolve.

#### U-trace-12 (P1, uncovered) — trace teleport export/open round-trips a legacy trace without losing the diff
- **subsystem**: trace  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 `trace teleport export {legacy_trace_id}` then `open` into a fresh box; assert the imported record resolves and (post U-trace-2 fix) carries metadata.legacy.patch/patches[]; absent Trail evidence does not crash export.
- **proves**: A legacy trace survives a workspace move with recoverable diff and absent Trail evidence handled gracefully.

#### U-trace-13 (P1, uncovered) — trace get --resume / --at-step on a legacy snapshot-less trace fails honestly
- **subsystem**: trace  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run `trace get {legacy_trace_id} --resume` and an --at-step invocation; assert --resume resolves the record cleanly and --at-step exits non-zero with a clear 'no Trace Trails snapshots' message, not a traceback or wrong fork.
- **proves**: Resume/snapshot-fork surfaces refuse legibly on legacy traces predating the Trail snapshot substrate.

#### U-setup-1 (P0, uncovered) — setup upgrade against a real 0.3.3 install refreshes skill + hooks idempotently, non-destructively
- **subsystem**: capture-setup  **mechanism**: real-v033-venv
- **scenario**: On a restored 0.3.3 world with old skill/hook files run `opentraces setup upgrade`. Assert install method detected; project skill + capture hooks refreshed to 0.4 shape exactly once; config_version handled (bumped via documented merge or left intact, never corrupted); legacy traces/*.jsonl byte-intact; a second run is a no-op; a subsequent capture works.
- **proves**: The documented preferred upgrade path works end-to-end, is non-destructive and idempotent against a legacy project.

#### U-setup-2 (P0, uncovered) — setup wizard / interview over an upgraded box preserves legacy config and does not auto-enable opt-in subsystems
- **subsystem**: capture-setup  **mechanism**: otbox_journey
- **scenario**: Seed config_version 0.2.0 (dataset_visibility=private, classifier_sensitivity=medium). Drive the non-interactive wizard/interview declining bucket/otlp/watcher. Assert rc=0; legacy keys verbatim; config_version handled; bucket/otlp/watcher remain disabled (no silent auto-adoption); legacy traces untouched; diff confirms no unrelated key dropped/flipped.
- **proves**: Re-running the machine wizard/interview on an upgraded box respects read-in-place / no-auto-adoption and does not clobber prior config.

#### U-setup-3 (P0, uncovered) — init / init --import-existing idempotency and backfill on an already-enrolled legacy project
- **subsystem**: capture-setup  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 (existing .opentraces.json + state.json) run `init --agent claude-code`; assert marker not duplicated, state merged not clobbered, legacy traces untouched, second init a no-op; repeat --agent codex-cli (additive). Then `init --agent claude-code --import-existing` over pre-existing Claude session JSONL; assert imports land in the 0.4 bucket as schema 0.6.0 with patches[] and no Outcome.patch, no duplicates on re-run, legacy JSONL untouched.
- **proves**: Re-enrollment is idempotent and the documented backfill normalizes pre-existing sessions to the new schema/substrate without disturbing legacy data.

#### U-setup-4 (P0, partial) — setup git on an upgraded legacy repo promotes a provisional trace and writes notes without a prior event log
- **subsystem**: capture-setup  **mechanism**: otbox_journey
- **scenario**: On a legacy world with a provisional-attribution trace and no event log, run `setup git`, make a commit, assert the post-commit hook correlates the trace, pins attribution.revision, writes refs/notes/opentraces, and does not require/error on a missing bucket. Standalone clean-install + idempotent re-run (no duplicate hook).
- **proves**: Provisional->final promotion works for an upgrader independent of bucket/event-log existing beforehand; correlator installs cleanly on a repo with no prior opentraces refs.

#### U-setup-5 (P0, partial) — watcher tick / scan_project on a legacy-only project bootstraps safely and never re-ingests legacy JSONL
- **subsystem**: capture-setup  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 (legacy traces, no event log) run `setup watcher tick --project ... --json`; assert rc=0, creates the event log additively, does NOT re-ingest or modify legacy traces/*.jsonl (byte-compare), second tick idempotent. Trigger a scan with no new agent activity and assert no new bucket trace/event from the pre-existing JSONL.
- **proves**: The new attribution daemon enrolls a migrated project without erroring on missing event log and never misclassifies the read-in-place corpus as fresh sessions.

#### U-setup-6 (P0, uncovered) — Full setup chain on a migrated project respects read-in-place and installs cleanly
- **subsystem**: capture-setup  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run setup claude-code, codex-cli, git, skill, bucket, capture-otlp, watcher install. Assert each rc=0; setup bucket does NOT adopt/move legacy traces (bucket stays empty until a fresh capture; legacy JSONL byte-unchanged); setup git installs the correlator without disturbing legacy state; no opt-in subsystem silently ingests legacy data.
- **proves**: The complete onboarding chain works for an upgrader and honors read-in-place / no-auto-adoption end-to-end.

#### U-setup-7 (P1, partial) — Fresh 0.4 capture in an upgraded repo emits patches[] (no Outcome.patch) across all substrates incl. a real context_* event
- **subsystem**: capture-setup  **mechanism**: real-v033-venv
- **scenario**: On c-legacy-v033-upgraded fetch the new trace record and assert populated patches[], NO outcome.patch, a bucket envelope, Trail events, AND at least one context_* event (closing the S12 empty-context-tree gap). Legacy 0.3.0 trace coexists unchanged.
- **proves**: Post-upgrade live capture honors the new schema contract and populates all four substrates, not just trace+trail.

#### U-setup-8 (P1, partial) — Per-tool setup verbs (trufflehog/privacy-filter/llm-review --enable) extend a legacy config cleanly
- **subsystem**: capture-setup  **mechanism**: otbox_journey
- **scenario**: Seed a legacy config, run `setup trufflehog --enable` (and privacy-filter, llm-review). Assert the security.<tool> block is added/flipped enabled, all legacy keys survive verbatim, config_version handled, re-run idempotent; doctor probes the configured endpoint without crashing.
- **proves**: Per-tool setup verbs upgrade a legacy config without corrupting its structure or dropping prior keys.

#### U-setup-9 (P1, partial) — Adapter/importer contract emits schema-0.6.0 records (Hermes diff-only import + protocol regression guard)
- **subsystem**: capture-setup  **mechanism**: pytest
- **scenario**: Feed a Hermes record carrying only a unified diff through the registered FormatImporter; assert the TraceRecord validates under 0.6.0 with reconstructed patches[] and no Outcome.patch. Iterate registered SessionParser/FormatImporter impls against a minimal fixture asserting each output validates under 0.6.0 and never sets outcome.patch.
- **proves**: The adapter/importer contract has fully migrated off the removed field and Hermes import survives the Outcome.patch removal.

#### U-setup-10 (P1, uncovered) — Migrated 0.3.3 trace preserves original attribution, not re-derived; honestly lacks new evidence fields
- **subsystem**: capture-setup  **mechanism**: pytest
- **scenario**: Take a frozen 0.3.3 record carrying attribution + git_links, run migrate_record + TraceRecord load; assert attribution.revision, confidence tiers, git_links preserved verbatim and NOT recomputed from reconstructed patches[]; assert metadata.skill_invocations is absent/empty and consumers degrade gracefully.
- **proves**: Migration does not silently rewrite legacy provenance and absence of new evidence fields is handled honestly.

#### U-auth-1 (P0, uncovered) — 0.3.3-stored hf_token survives migration and is reused (not re-prompted, not shadowing HF_TOKEN)
- **subsystem**: auth-remotes  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 with a 0.3.3-format credential + cfg.hf_token in the 0.2.0 config: run `auth whoami --json` (rc=0, username resolved, no re-login). Export HF_TOKEN=hf_ENV and assert it beats the migrated stored hf_OLD. Run `auth login --token` / `logout` and assert the 0.3.3-format file is read/cleared at the same path 0.4 uses.
- **proves**: An upgrader does not lose HF auth across the jump; env-over-stored precedence survives migration; logout targets the legacy file.

#### U-auth-2 (P1, uncovered) — Legacy token drives the new bucket-remote and dataset-remote auth paths without re-login
- **subsystem**: auth-remotes  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 with a legacy token, run `setup bucket` then `bucket remote status` (or a mocked push) and `dataset remote create my-ds owner/team --private` against a mocked HfApi; assert the migrated cfg.hf_token reaches both auth calls, --private is the default, and dataset vs bucket remote bindings do not cross-contaminate (disjoint repo ids, shared token only).
- **proves**: The 0.3.3 token is reused by the post-0.3.3 bucket/dataset remote consumers and the independent-remotes contract holds.

#### U-auth-3 (P1, uncovered) — Headless HF_TOKEN run on an upgraded world forces no auth login
- **subsystem**: auth-remotes  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 with HF_TOKEN exported and no TTY, run `dataset new` + `dataset run` + `dataset publish` (mocked HF); assert no auth login prompted and HF_TOKEN is the credential used end-to-end after config migration.
- **proves**: The documented CI bypass survives migration; an upgraded 0.3.3 CI pipeline is not blocked by a new auth gate.

#### U-bucket-1 (P0, uncovered) — setup bucket on a restored 0.3.3 world: v2 layout, token reuse, no legacy adoption; --migrate degrades honestly
- **subsystem**: bucket  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run `setup bucket --no-autostart` with the 0.3.3 token; assert rc=0, no re-auth, additive config keys, bucket reports zero adopted traces, legacy JSONL byte-intact + queryable. Separately run `setup bucket --migrate --json` and assert clean no-op OR clear actionable error (NOT a raw _handle_bucket_migrate NotImplementedError traceback).
- **proves**: First-run bucket opt-in does not auto-adopt legacy data, does not force re-auth, writes only additive keys, and --migrate degrades honestly on the true 0.3.3 boundary.

#### U-bucket-2 (P0, uncovered) — No surprise egress: capture with a pre-existing 0.3.3 hf_token stays local until explicit setup bucket + push
- **subsystem**: bucket  **mechanism**: pytest
- **scenario**: Fork c-legacy-v033 with the 0.3.3 hf_token present (no setup bucket). Drive a 0.4 capture with network egress trapped/offline; assert the capture lands only in the local bucket and NO HF call is made. Then run `setup bucket` + `bucket remote push` and assert egress happens only now and only to the bucket repo.
- **proves**: A pre-existing 0.3.3 hf_token is not treated as consent to auto-egress; raw captures stay local until explicit opt-in.

#### U-bucket-3 (P0, uncovered) — Two-store egress separation: dataset publish never egresses the raw bucket; bucket push never writes dataset rows
- **subsystem**: bucket  **mechanism**: pytest
- **scenario**: On c-legacy-v033-upgraded with both a dataset remote and a bucket remote bound, instrument/mock the HF client. Run a dataset publish flow and assert NO write touches the bucket remote repo or any raw blob; run `bucket remote push` and assert NO write touches the dataset repo and no projected rows are uploaded. Assert the two repo ids are disjoint.
- **proves**: The security-critical two-store egress separation holds: raw bucket evidence is never published by dataset publish, and dataset rows are never published by a bucket push.

#### U-bucket-4 (P0, uncovered) — bucket remote push targets a distinct private-bucket repo, never the legacy 0.3.3 dataset repo; diff/pull safe on empty
- **subsystem**: bucket  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033-upgraded with setup bucket run and a 0.3.3 dataset remote configured: `bucket remote status`/`push` asserts the bucket repo id differs from the legacy dataset repo, push order blobs->events->envelopes->manifest, legacy dataset repo never touched. On an empty freshly-initialized bucket, `bucket remote diff`/`pull` are non-destructive no-ops (no --force clobber).
- **proves**: Bucket remote sync uses a separate repo/egress path, cannot clobber an upgrader's existing dataset, and is safe on the empty boundary.

#### U-bucket-5 (P0, uncovered) — repair/rebuild/replay never auto-adopt legacy JSONL and preserve legacy git history
- **subsystem**: bucket  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033-upgraded snapshot legacy JSONL bytes and HEAD ancestry. Run `bucket repair`, `bucket rebuild --substrate all`, `bucket replay --repo <clone>` in sequence. Assert legacy JSONL byte-unchanged, manifest legacy-trace count stays 0 (only the 0.4 capture), pre-capture HEAD remains an ancestor (history appended not rewritten), replay reconstructs only the 0.4-capture batches byte-identically.
- **proves**: Maintenance verbs honor read-in-place: they never ingest legacy JSONL into the bucket and never rewrite legacy git history; replay is byte-identical.

#### U-bucket-6 (P1, partial) — Net-new bucket verbs degrade gracefully on the empty boundary and validate on first capture
- **subsystem**: bucket  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 cold: `bucket status`/`manifest`/`verify --full`/`prune --dry-run`/`prefetch <legacy_id>` all rc=0 with honest empty/absent (no fabricated manifest, clear no-remote error for prefetch). On c-legacy-v033-upgraded after one capture: manifest schema_version == opentraces.bucket.manifest.v2 + events index opentraces.bucket.events.v2, verify recomputes blob hashes clean, prune preserves events+trace.json+legacy JSONL, v2 directory layout materialized.
- **proves**: Bucket read/maintenance verbs are honest no-ops on the empty upgrader boundary and initialize at the documented v2 layout once a real capture exists.

#### U-bucket-7 (P1, partial) — Remote-bucket / resolve-back reads behave on empty boundary and error legibly for legacy ids predating the bucket
- **subsystem**: bucket  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 (no bucket): `trace query --remote-bucket`, `ctx tree {legacy_id}` handle empty/no-bucket gracefully. On c-legacy-v033-upgraded with setup bucket + push: `trace get {legacy_trace_id} --remote owner/bucket` errors clearly ('predates bucket') not silent-empty, while the new 0.4 id resolves with trail/ctx companions via --remote; `trail blame commit`/`ctx tree` read the 0.4 capture.
- **proves**: The new remote-bucket read flag and resolve-back-to-evidence behave sanely on the empty boundary and distinguish legacy ids from real 0.4 captures.

#### U-bucket-14 (P1, uncovered) — Manual UAT: a 0.3.3 user's `push` mental model is honestly redirected (no surprise egress, no surprise silence)
- **subsystem**: bucket  **mechanism**: manual-uat
- **scenario**: On a restored 0.3.3 box a human follows the upgrade docs: confirm the old workflow does NOT auto-publish raw traces, the docs/CLI clearly explain where 0.3.3 push maps (dataset publish for rows; bucket remote push for raw evidence), and no egress happens without explicit opt-in. Capture the first-run UX.
- **proves**: An upgrader is neither surprised by silent egress of raw evidence nor by traces silently no longer being published; the push->two-store transition is legible.

#### U-trail-1 (P0, partial) — trail track on a migrated legacy trace returns unknown/no-anchor, never fabricated survival or confidence
- **subsystem**: trail  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033-upgraded run `--json trail track {legacy_trace_id}` (and --patch <reconstructed-id>, --anchor <id>); assert rc=0 with an empty/unknown evidence chain, NO Git anchors, NO alive/reverted/lost verdict, NO firm/provisional confidence. `trail explain {legacy_trace_id}` reports zero anchors. Contrast with the new-capture path carrying a real anchor.
- **proves**: Migrated legacy traces (reconstructed patches[], zero anchors) honestly degrade to unknown rather than fabricating Git positions/confidence that corrupt review decisions.

#### U-trail-2 (P0, uncovered) — trail blame commit honest empty-state on a pre-upgrade commit; resolves only real 0.4 anchors
- **subsystem**: trail  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033-upgraded run `--json trail blame commit <legacy-restore-sha>` (predates any 0.4 anchor); assert rc=0 + empty/no-attribution envelope (no fabricated trace ids). Then `trail blame commit <new-0.4-capture-sha>` resolves to {new_trace_id}.
- **proves**: blame commit degrades honestly to empty for pre-0.4 commits and resolves correctly only for commits carrying real 0.4 anchors.

#### U-trail-3 (P0, uncovered) — git-backfill on an upgraded repo with no prior notes ref produces honest correlations
- **subsystem**: trail  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 (legacy commits, no notes ref) run `setup git` then `--json git-backfill --max-commits 2000 --window-hours 48`; assert rc=0, refs/notes/opentraces created without error, legacy commits correlated to the migrated trace anchored only where real evidence exists (no fabricated anchors for reconstruction-only patches[]).
- **proves**: git-backfill tolerates the no-prior-notes-ref state and produces honest correlations for migrated traces instead of erroring or inventing anchors.

#### U-trail-4 (P0, uncovered) — GitLink liveness recompute against a force-pushed-away commit reports content_alive=false without error
- **subsystem**: trail  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033-upgraded with a 0.4 capture anchored to a commit, rewrite history so the commit's bytes no longer exist at HEAD. Read the trace (trace get --json) and assert GitLink commit_reachable=false / content_alive=false without error / sane orphan-unknown tiers. Confirm reading a legacy trace with no event log does not error on liveness recompute.
- **proves**: Lazy liveness recomputation against an evolved post-upgrade repo honestly reports vanished bytes and tolerates the no-event-log case.

#### U-trail-5 (P1, uncovered) — trail graph / blame pr render empty-but-valid on legacy-only, populated on the one 0.4 capture
- **subsystem**: trail  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033: `--json trail graph` and `trail graph --trace {legacy_trace_id}` rc=0 empty (no crash); `trail blame pr render --base main` rc=0 empty-but-valid markdown. On c-legacy-v033-upgraded: graph shows the {new_trace_id} node, pr render shows the single 0.4 capture as one blame row.
- **proves**: graph and the PR consumer render honest empties for a legacy-only world and coherent output once one 0.4 capture exists.

#### U-trail-6 (P1, uncovered) — backfill / watcher tick over a legacy 0.3.0 inbox record is migration-aware or honestly skips
- **subsystem**: trail  **mechanism**: otbox_journey
- **scenario**: Seed c-legacy-v033 with a 0.3.0-shaped session JSONL in the inbox. Run `backfill` and `setup watcher tick --json`; assert rc=0 and the record is either ingested with patches[] reconstructed or honestly skipped with a logged reason, never ingested as a malformed event into refs/opentraces/local/events/v1.
- **proves**: The catch-up ingest read path handles legacy 0.3.0 inbox records without corrupting the new event log.

#### U-trail-7 (P1, partial) — Migration emits zero TrailEvents; migrated patches[] are hunk-granular (or labeled-degraded)
- **subsystem**: trail  **mechanism**: pytest
- **scenario**: pytest: feed migrate_record a legacy Outcome.patch with TWO hunks in ONE file; assert patches[] is hunk-granular OR the v1 file-granular degradation is explicit and labeled synthetic. otbox: capture event-ref batch count (zero) on c-legacy-v033, run first-touch migration/index-rebuild, assert ref batch count STILL zero while c-legacy-v033-upgraded's new-capture path adds batches.
- **proves**: The documented per-hunk authorship unit is honored or its degradation is explicit, and read-in-place migration never emits spurious TrailEvents.

#### U-trail-8 (P1, partial) — bucket replay rebuilds the canonical event ref byte-identically on an upgraded world
- **subsystem**: trail  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033-upgraded delete refs/opentraces/local/events/v1 and run `bucket replay --repo`; assert the reconstructed ref is byte-identical to the pre-delete ref (same batch hashes) and trail track {new_trace_id} resolves identically afterward.
- **proves**: The bucket events mirror rebuilds the canonical Trail event ref byte-identically after an upgrade-driven capture.

#### U-trail-9 (P2, partial) — Public/hidden trail verb split holds on an upgraded box
- **subsystem**: trail  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033-upgraded run `trail track/graph/blame --help` and a hidden verb (`trail timeline`/`trail sync`) with --json; assert public verbs listed in help, hidden verbs absent from help but rc=0 when invoked directly.
- **proves**: The documented public/hidden trail verb split holds — public verbs discoverable, hidden verbs still scriptable.

#### U-ctx-1 (P0, partial) — ctx tree/show/step/reads/writes/diff/compactions/resume/prune/resolve/anchor-for-step return honest no-evidence on a legacy trace
- **subsystem**: context-tree  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run the full ctx verb set against {legacy_trace_id} / bogus node ids; assert each returns rc=0 (or a clean defined non-crash error) with a canonical envelope indicating no context tree / no node, NOT a Python traceback. `ctx prune --source-jsonl` makes record_count=0 unmistakable (no false success). `ctx info {legacy_trace_id}` shows no context tree on a missing manifest.
- **proves**: The brand-new ctx navigation/projection/resume verbs degrade gracefully on a migrated trace with zero context nodes, the dominant first-touch surprise for a curious upgrader.

#### U-ctx-2 (P0, partial) — Migrated 0.3.0 record validates with context_tree_summary and per-step context_node_id empty/absent
- **subsystem**: context-tree  **mechanism**: pytest
- **scenario**: Extend S3 / S3-frozen-world pytest: after migrate_record + model_validate on legacy_world_v033, assert context_tree_summary is None/empty (no node_count, no active_path_leaf_id), every Step.context_node_id is None/absent, and no context node ids were fabricated.
- **proves**: The post-0.3.3 Context Tree schema fields stay inert/empty on a migrated legacy record under the additive contract and do not block validation.

#### U-ctx-3 (P1, uncovered) — setup capture-otlp preserves a pre-existing ~/.claude/settings.json and is idempotent
- **subsystem**: context-tree  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 seed ~/.claude/settings.json with realistic pre-existing user keys. Run `setup capture-otlp --no-autostart`. Assert all 12 OTel env vars present, every pre-existing key survives byte-for-byte, a write-once backup created, second run a no-op (no second backup, no duplicate keys). doctor --json reports context_tree.otel_receiver block honestly.
- **proves**: The 0.3.3 Claude Code user's own settings are not clobbered and the patch is idempotent across re-runs.

#### U-ctx-4 (P1, uncovered) — capture-otlp bypass-safety + first real context_* capture inside a migrated repo
- **subsystem**: context-tree  **mechanism**: real-v033-venv
- **scenario**: On c-legacy-v033 with OTel env vars set but receiver DOWN, run a real Claude Code session; assert it completes normally (traffic to Anthropic not blocked), no user-visible error, status reports not-running. Then start receiver, run another session, flush, assert context_* events exist in refs/opentraces/local/events/v1; the legacy trace still has no context_tree_summary while the new one does; new layers carry capture_method=otel while legacy stays approximation.
- **proves**: The bypass-safe contract holds and Context Tree population works end-to-end inside a repo holding a migrated legacy trace, closing the S12 empty-context gap.

#### U-ctx-5 (P0, uncovered) — Downstream consumer degrades gracefully when ctx/trail evidence is absent on a legacy trace
- **subsystem**: context-tree  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run `dataset new` + `dataset run` (or a branch-context/capsule consumer) scoped to the legacy 0.3.0 trace whose build_rows.py reads ctx step/resume + trail track; assert the consumer completes rc=0 with context/trail fields empty/omitted rather than crashing on missing context_node_id / context_tree_summary, and the projected row still carries migrated patches[].
- **proves**: Workflow/agent/capsule consumers that read Context Tree/Trail degrade gracefully on a migrated legacy trace with no substrate evidence.

#### U-ds-1 (P0, uncovered) — dataset new --workflow then run over read-in-place legacy traces projects migrated patches[]
- **subsystem**: workflows-datasets  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033: workflow create --template default; `dataset new ds1 --workflow ./workflows/default/`; `dataset run ds1 --dry-run --json` (and --scope trace --trace {legacy_trace_id}). Assert the legacy trace surfaces as an eligible candidate and the projected row carries reconstructed patches[] + metadata.legacy.patch (NOT None Outcome.patch), rc=0, non-zero row count. Run with empty index and assert graceful zero-eligible (no crash). Assert --privacy-tier changes only the publication-compat field, not which security tools run.
- **proves**: A fresh upgrader can scaffold a dataset and project the migrated legacy trace into a 0.6.0 row; the 'reads Trace Index candidates' claim holds; empty index degrades; --privacy-tier is not the old security knob.

#### U-ds-2 (P0, uncovered) — dataset new --rows-file (the documented pull-replacement) migrates legacy 0.3.0 rows -> patches[]
- **subsystem**: workflows-datasets  **mechanism**: pytest
- **scenario**: Take the frozen legacy_world_v033 0.3.0 JSONL (populated outcome.patch) as rows.jsonl and a schema.json matching 0.6.0. Run `dataset new my-import --rows-file rows.jsonl --schema schema.json`. Assert each seeded row stored as 0.6.0 with patches[] reconstructed and metadata.legacy.patch preserved verbatim, rc=0.
- **proves**: The documented pull-replacement import path migrates legacy-shaped rows rather than dropping patch silently or rejecting the file.

#### U-ds-3 (P1, uncovered) — opentraces pull errors cleanly with a non-zero exit and a replacement hint
- **subsystem**: workflows-datasets  **mechanism**: otbox_journey
- **scenario**: On any 0.4 box run `opentraces pull` and `opentraces pull --help`. Assert a clean non-zero exit (no traceback) pointing the user to `dataset new --rows-file` as the replacement.
- **proves**: A 0.3.3 user invoking the removed verb gets a deterministic, actionable failure rather than a silent no-op or crash.

#### U-ds-4 (P0, uncovered) — Full headless spine from a freshly-migrated 0.3.3 project to published 0.6.0 rows
- **subsystem**: workflows-datasets  **mechanism**: real-v033-venv
- **scenario**: On c-legacy-v033 drive: workflow create --template skill-command-trajectory-eval-v1; dataset new ds1 --workflow ...; dataset run ds1 --executor headless --json; dataset review approve ds1 --all; dataset remote create; dataset publish ds1. Assert each stage rc=0, the legacy trace projects into rows with reconstructed patches[], published rows are schema 0.6.0. Live-HF token lane for the publish leg.
- **proves**: The canonical 0.4 automation spine works from a migrated project, tolerates read-in-place legacy traces at every stage, and emits correctly-migrated rows.

#### U-ds-5 (P1, uncovered) — dataset review/approve/--all + state-file no-collision; --web/--tui decommission notice
- **subsystem**: workflows-datasets  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 build a dataset with rows: `dataset review ds1 --json` (rows in inbox), `review approve ds1 --all`, `status ds1 --json` (approved count). Confirm legacy top-level state.json untouched and the dataset row-state file lives in its own 0.4 location. Run `dataset review ds1 --web` and `--tui`; assert each emits the documented decommission notice with a deterministic exit, not a traceback.
- **proves**: Per-row review works on an upgraded world without state collision and scripted web/TUI review degrades to a clean notice.

#### U-ds-6 (P2, uncovered) — Net-new dataset/workflow/schedule surfaces report honest empty-state; schedule preserves gate-bypass invariant
- **subsystem**: workflows-datasets  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 assert `dataset list`/`workflow list`/`dataset schedule list` return clean empty arrays (rc=0, no phantom dataset). `workflow templates` lists default/skill-command-trajectory-eval-v1/pr-intent-summary-v1; `workflow create --template` scaffolds a workflows dir into a project with none. `dataset schedule add ds1 --every 1h` (no flags) registers without clobbering the 0.2.0 config and a scheduled run does not auto-approve/publish.
- **proves**: All net-new dataset/workflow/schedule surfaces report honest empty-state on a fresh upgrade and the schedule no-bypass invariant holds.

#### U-ds-7 (P0, uncovered) — OT_RUN_PACKET candidate carries patches[]/metadata.legacy.patch, not Outcome.patch
- **subsystem**: workflows-datasets  **mechanism**: pytest
- **scenario**: Run a workflow whose build_rows.py dumps the raw OT_RUN_PACKET to OT_DATASET_OUTPUT over a migrated legacy trace. Assert the trace candidate exposes patches[] and metadata.legacy.patch and candidate.outcome.patch is absent. A second script reading the old Outcome.patch field must fall back to patches[]/metadata.legacy to recover the diff.
- **proves**: Ported 0.3.3 workflow scripts keying on Outcome.patch read None; the migrated diff is recoverable only via patches[]/metadata.legacy.patch, making the silent data-shape regression explicit.

#### U-ds-8 (P0, uncovered) — Post-processor receives a migrated 0.6.0 record; legacy-shaped output rejected (and --strict raises); doctor probes it
- **subsystem**: workflows-datasets  **mechanism**: pytest
- **scenario**: Declare a post_processors[] entry in an upgraded config. Pipe a migrated legacy trace through it: assert stdin carries patches[] + metadata.legacy.patch and NO outcome.patch. A processor emitting a legacy 0.3.0-shaped record (re-adding outcome.patch) is flagged status=invalid_output by default and --strict promotes to a hard error. `doctor` probes the configured (present and absent) processor binaries non-fatally; redaction-before-processor ordering holds with the migrated record.
- **proves**: Post-processors get migrated 0.6.0 records, legacy-shaped output is rejected against the new schema, --strict works, the 0.3.3-declared config survives and is probed, and the redaction-first invariant holds.

#### U-sec-1 (P1, partial) — Legacy privacy-tier knob is honestly neutralized and the upgrade UX surfaces that tier redaction is gone
- **subsystem**: security  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 whose config carries a 0.3.3 privacy_tier: run `security tools list --json` + `config show --json`; assert all seven tools enabled==false, the stale tier key is dropped or carried-but-inert (no tool enabled BY the tier value), rc=0. Run doctor/setup and assert a visible signal that previously-enabled redaction is now opt-in and disabled.
- **proves**: The 0.3.3 tier setting does not silently re-enable or break the per-tool registry, and a user who relied on tier redaction is not silently left unprotected without notice.

#### U-sec-2 (P0, uncovered) — security sanitize over legacy/migrated records: payload shapes, all-off default, canonical order, new metadata
- **subsystem**: security  **mechanism**: pytest
- **scenario**: Pipe a schema-0.3.0 {record} (Outcome.patch, no patches[]) and its migrated form into `security sanitize --tools regex,entropy`; assert rc=0 both, patches[] survives, metadata.security.tools_applied==['regex','entropy'] in canonical order, no error on removed/added fields. `--use-config` on the all-off tree runs zero tools (unmodified record); --tools regex standalone redacts a planted secret. Validate {text}/{row}/{record} shapes and that re-sanitizing clears stale legacy summary fields; classifier (Judge) does not mutate content.
- **proves**: The full-record sanitize path tolerates legacy/migrated records, honors the all-off default, emits the new metadata contract in deterministic order, and judge non-mutation holds.

#### U-sec-3 (P0, partial) — Sanitize workflow finds reconstructed patches[] and survives an absent trail companion; custom_redact_strings honored
- **subsystem**: security  **mechanism**: pytest
- **scenario**: Migrate a real 0.3.0 trace, run the documented patch-reading sanitize path; assert it reads reconstructed patches[] content, tolerates trail.jsonl.gz absent, redacts a secret planted in the legacy diff. Negative control: an UN-migrated record (patches[] empty) must not silently scan nothing — it falls back to metadata.legacy.patch/Outcome.patch or surfaces 'no patch evidence'. Confirm custom_redact_strings from a legacy config is honored by --use-config over a migrated record.
- **proves**: Sanitization of patch content works end-to-end on migrated traces and degrades safely (no silent no-op) when patches[]/trail companion are missing; a redaction setting the user relied on still fires — closing a privacy-regression gap.

#### U-sec-4 (P1, uncovered) — setup <tool> --enable patches a legacy config cleanly and sanitize then runs the enabled tools (CI flow)
- **subsystem**: security  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run the CI enable sequence (`setup trufflehog --enable`, `setup privacy-filter --enable`, `setup llm-review`); assert valid security.<tool> keys written into the migrated config, surviving 0.3.3 keys preserved, config_version handled, no KeyError on the previously-absent security subtree. Then `security sanitize --use-config` over a migrated record runs the enabled tools (tools_applied includes them).
- **proves**: The enable verbs write valid per-tool keys into a freshly-migrated config without dropping prior settings and integrate cleanly with sanitize.

#### U-sec-5 (P1, partial) — doctor --json security block well-formed/green on a migrated config; SECURITY_VERSION reported (resolves 0.4.0-vs-0.5.0 doc drift)
- **subsystem**: security  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run `doctor --json`; assert the security.tools block lists all seven tools enabled==false, no probe raises, the block is structurally green despite the legacy config lacking those keys, and the reported SECURITY_VERSION constant is asserted and cross-checked against docs (flag if docs say 0.5.0 and code says 0.4.0).
- **proves**: doctor degrades gracefully on a config predating the per-tool keys and exposes the documented version drift.

#### U-sec-6 (P2, uncovered) — security tools info <tool> --json and field-type/redaction-marker/path-anonymization behave on legacy-derived content
- **subsystem**: security  **mechanism**: pytest
- **scenario**: On c-legacy-v033 `security tools info regex|trufflehog --json` rc=0 with descriptor, enabled==false, no KeyError. Pipe migrated-record secret text through `sanitize --tools regex --field-type tool_input` vs `tool_result` (assert stricter-on-input), and `--tools regex,path_anonymizer` (assert [REDACTED] markers + username path rewrite per docs).
- **proves**: Per-tool descriptors read on a pre-keys config and the field-type/marker/path-anonymization contracts are intact post-upgrade.

#### U-hf-1 (P0, partial) — Confirm/refute whether live dataset publish reaches HFUploader schema-ahead/migrate/dedup paths
- **subsystem**: schema-hf-publish  **mechanism**: pytest
- **scenario**: Instrument or assert (pytest against the dataset publish code path) whether dataset publish invokes HFUploader.ensure_repo_exists and the schema-version comparison / migrate_outdated_shards, or whether real HF is stubbed. Record the verdict as covered vs confirmed product gap.
- **proves**: Turns the project-memory suspicion (HF schema-safety unreachable from the live publish CLI) into a documented fact and pins where the wiring must land.

#### U-hf-2 (P0, partial) — Live upgrade publish into a 0.3.0-declaring remote triggers shard migration; into a newer remote fails closed (exit-3)
- **subsystem**: schema-hf-publish  **mechanism**: real-v033-venv
- **scenario**: On c-legacy-v033-upgraded (live-HF lane): dataset run over the migrated legacy trace -> remote create against a mock/real repo declaring dataset_infos 0.3.0 -> publish. Assert the live CLI routes through migrate_outdated_shards: published rows 0.6.0 with patches[] reconstructed + metadata.legacy.patch, only strictly-older rows rewritten, --private default, regenerated dataset_infos.json omits a patch feature and declares 0.6.0. Then target a remote already newer and assert exit-3 + setup-upgrade hint before any upload. Reciprocally, drive the real v0.3.3 CLI publishing to a 0.6.0 remote and assert its exit-3 refusal is observable (not just inferred by S7 comment).
- **proves**: Closes the documented product gap that S6/S7 only prove in isolation: the LIVE upgrade publish CLI reaches forward shard migration and the fail-closed schema-ahead guard against real 0.3.3-era / newer remotes.

#### U-hf-3 (P1, partial) — Mixed-version JSONL loads uniformly; migrated record stamps schema_version 0.6.0; content_hash dedup break characterized
- **subsystem**: schema-hf-publish  **mechanism**: pytest
- **scenario**: Construct JSONL with a migrated legacy line + a native 0.6.0 line; assert both parse, the legacy line stamps 0.6.0 explicitly (to_jsonl_line emits 0.6.0), consumers get a uniform view. Compute the 0.3.0 record's SHA-256 content_hash, migrate, recompute: assert it DIFFERS (documenting the upload dedup break) while AttributionRange murmur3 hashes are byte-identical; characterize the upload dedup outcome (duplicate vs skip). max(generation_index) over a legacy + higher native generation returns the 0.6.0 record.
- **proves**: A dataset of legacy-migrated + native rows reads as a uniform 0.6.0 stream, version is unambiguously stamped, and the content_hash dedup-identity break is characterized so upgraders are warned about re-publish duplication.

#### U-hf-4 (P1, partial) — git_links liveness / committed-commit_sha / lifecycle-promotion behave on migrated reconstructed-anchor traces
- **subsystem**: schema-hf-publish  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 with a legacy git_links commit_sha that no longer exists, read the migrated trace and trigger commit_reachable/content_alive recompute; assert no crash, tiers degrade to orphan/unknown. Assert outcome.committed/commit_sha still equal the legacy values (not nulled/recomputed by the patch-anchor projection). Install the 0.4 post-commit hook, commit touching the legacy trace's files, watcher tick + trail mature; assert lifecycle provisional->final without a populated bucket.
- **proves**: git_links liveness recompute against derived anchors/dead commits is crash-safe, devtime outcome projections preserve legacy values, and provisional->final promotion works for an upgrader.

#### U-hf-5 (P1, partial) — Migration auto-fires on every read/publish surface; no public migrate verb; schema CHANGELOG/rationale + registration exist
- **subsystem**: schema-hf-publish  **mechanism**: otbox_journey
- **scenario**: Across trace get/query/map and dataset run on a restored legacy world, assert each surface returns a 0.6.0-migrated view (patches[] + metadata.legacy.patch present) — this overlaps and depends on the U-trace-1/U-trace-2 read-loader fix. Assert no public `opentraces migrate` command exists, migrate_record is registered (not merely importable) under opentraces_schema.migrations and dispatched by the version machinery, and the schema CHANGELOG records the 0.3.0->0.6.0 Outcome.patch removal with a rationale.
- **proves**: Migration is reliably invoked on the surfaces an upgrader uses (currently FALSE on the live read path), is backed by a registered+dispatched migration, and the breaking change is documented.

#### U-hf-6 (P1, uncovered) — load_dataset of a freshly-published 0.4 dataset matches regenerated 0.6.0 features; publish gates handle no-Trail rows
- **subsystem**: schema-hf-publish  **mechanism**: pytest
- **scenario**: Publish an upgraded dataset, then load_dataset (or offline equivalent) and assert the loaded row schema matches the regenerated dataset_infos.json (patches[] present, no outcome.patch). Run `dataset publish --check-only --min-retention 0.5 --exclude-state lost` over rows built from migrated legacy traces (no Trail survival evidence) and assert unknown/absent survival state is handled by a documented rule (not silently dropped, not silently all-passed); interrupt and --resume consistently.
- **proves**: The dataset-consumer contract holds end-to-end and survival-state publish gates + --resume behave predictably for migrated legacy rows lacking Trail evidence.

#### U-hf-7 (P2, uncovered) — Constructing a TraceRecord with Outcome.patch under the 0.6.0 package fails loudly; consumer reading outcome.patch fails predictably
- **subsystem**: schema-hf-publish  **mechanism**: pytest
- **scenario**: Under the 0.6.0 opentraces-schema package, attempt to build a TraceRecord passing outcome.patch and assert it raises a validation error (not silently accepted); build a valid record and assert to_jsonl_line fills schema_version 0.6.0 + content_hash. On a migrated 0.6.0 record assert outcome has no patch attribute/key and metadata.legacy.patch + patches[] are present (pin against the outcome-attribution.md doc drift).
- **proves**: Upgrading the standalone package surfaces the removed field at construction time and the removal is hard (no silent empty-string patch), surfacing the doc drift.

#### U-config-1 (P0, partial) — config_version migrated/stamped forward (idempotent); config set creates missing nested security subtree without clobbering legacy keys
- **subsystem**: config-state  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run `config show --json` and assert config_version is the 0.4 target (or an explicit migrated marker), not stale 0.2.0; re-run idempotent. Run `config set security.regex.enabled true` and assert the nested path is CREATED while pre-existing 0.3.3 keys (dataset_visibility, classifier_sensitivity) survive byte-for-byte and the file remains valid re-loadable JSON. `config set custom_redact_strings SECRET --append` and `excluded_projects /tmp/x --append` work without dropping keys.
- **proves**: The loader records migration occurred and dotted-path set creates missing subtrees without clobbering legacy keys — the highest-value direct-config upgrade check.

#### U-config-2 (P0, uncovered) — Legacy .opentraces.json marker (no bucket defaults / no post_processors) loads and defaults sanely
- **subsystem**: config-state  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 write a strictly-0.3.3-shaped marker (review/agents/remotes only), run status, config show --json, and a 0.4 capture; assert no validation error, bucket-default keys resolve to sensible values, post_processors treated as an empty chain, and review_policy=review from the marker still forces manual review.
- **proves**: The 0.4 marker loader is forward-tolerant of the pre-bucket marker shape and honors the legacy per-project review policy.

#### U-config-3 (P0, partial) — doctor --json reports all new subsystems as not-yet-configured (not errored) on a legacy world
- **subsystem**: config-state  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 run `doctor --json` and assert: context_tree.otel_receiver.enabled==false (no crash on missing receiver), bucket block reports zero/absent not error, security.tools all clean-disabled, configured post-processor probes resolve/non-resolve non-fatally, and no block has a hard-error/red status attributable to absent post-0.3.3 state.
- **proves**: doctor degrades gracefully on a project with only traces/*.jsonl + a 0.2.0 config, satisfying the documented pre-publish health-check contract block-by-block (S8 only checked rc=0).

#### U-config-4 (P1, partial) — status honestly distinguishes legacy on-disk traces from bucket captures; full --json agent-flow bundle returns honest envelopes
- **subsystem**: config-state  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033 (after first-touch index rebuild) run `status --json` (assert zero bucket captures, legacy trace surfaced via index not mislabeled as bucket), then each documented --json verb: status, config show (migrated version), trail track, trail blame commit <HEAD>, ctx tree, security tools list, trace query, trace get. Assert each rc=0 schema-valid; trail/ctx report empty-but-valid. Also assert capabilities/introspect --json list the new groups state-independently.
- **proves**: status does not fabricate bucket state and every verb in the documented agent-flow bundle produces structured honest output on a migrated project.

#### U-config-5 (P0, uncovered) — remove / remove --all cleans mixed legacy + 0.4 state consistently and never silently destroys legacy devtime JSONL
- **subsystem**: config-state  **mechanism**: otbox_journey
- **scenario**: On c-legacy-v033-upgraded (legacy 0.3.0 trace + new 0.4 capture + refs/opentraces/* + bucket) run `remove`; assert project config/marker/state removed AND document what happens to traces/*.jsonl and git refs. A second pass with `remove --all` on a fresh restore cleans bucket+event-log too; any deletion of legacy raw traces is intentional and reported, not silent.
- **proves**: remove handles mixed legacy+0.4 state without orphaning or silently destroying the upgrader's original on-disk diffs.

#### U-config-6 (P1, partial) — Real installed 0.4.0 CLI first-run migrates a 0.3.3 home config in place
- **subsystem**: config-state  **mechanism**: real-v033-venv
- **scenario**: In an isolated venv install the real 0.4.0 wheel, point HOME at a restored 0.3.3 ~/.opentraces (config_version 0.2.0), run `config show --json` as the literal first command; assert config_version migrated, existing keys survive, on-disk config remains valid. SKIP when the built wheel is absent (S7 Layer-B skip discipline).
- **proves**: First-run config migration works under the actually-shipped CLI, not just the editable dev tree — the true upgrade trigger.

#### U-config-7 (P2, uncovered) — Decommissioned TUI/viewer entrypoints fail honestly; dataset review is the documented replacement
- **subsystem**: config-state  **mechanism**: manual-uat
- **scenario**: On c-legacy-v033 confirm any old raw-trace TUI/viewer invocation is removed with a clear pointer to dataset review (or hidden), `dataset review` is the live documented path, and no broken half-wired viewer surface remains. Manual UAT; also covers completions install emitting scripts referencing the new groups.
- **proves**: The upgrader is honestly redirected from the removed review surface to dataset review and post-upgrade completions reflect the 0.4 surface.

## Recommended implementation workflow

PHASE 0 (P0 BUG FIX, do first, blocks the rest): Fix the live read-path migration drop. Wrap _read_trace_record_from_path and the parallel direct model_validate_json sites (cli/trace.py:1058, 1271, 1309, 1559, 1775) — and any equivalent in core/trace_index.py / search_projection / bucket_store — in migrate_record before validate, OR route all reads through a single migration-aware loader. Land U-trace-2 (pytest) and U-hf-5's read-surface assertion as the regression guard, then U-trace-1 (journey) flips from FAIL to PASS. Without this, the headline 'your old traces are readable with patches[]' is false, so every downstream read/dataset/security test would assert against a lossy view.

PHASE 1 (pytest layer, no checkpoints needed): Land the schema/contract pytests that need only the frozen legacy_world_v033 fixture: U-trace-2, U-ctx-2, U-trail-7, U-ds-2, U-ds-7, U-ds-8, U-sec-2, U-sec-3 (library half), U-sec-6, U-hf-3, U-hf-7, U-setup-9, U-setup-10, U-bucket-2, U-bucket-3, U-hf-1 (the publish-reachability probe), U-hf-6 (load_dataset half). These are fast, deterministic, default-CI-safe, and pin the breaking-change behavior across the workflow/security/post-processor/HF code paths in isolation. U-hf-1 is the cheapest way to convert the suspected live-publish-unreachability product gap into a documented fact and decide whether HF wiring work is in scope.

PHASE 2 (otbox checkpoints): Extend the c-legacy-v033 family so the journeys have credible state. (a) Enrich c-legacy-v033 with: a 0.3.3-format credential file + cfg.hf_token, a stale 0.3.3 skill/hook file, a 0.3.3 privacy_tier in config, a provisional-attribution trace, and a pre-existing populated ~/.claude/settings.json. (b) Add c-legacy-v033-otel-upgraded that drives a REAL claude session through the OTLP receiver so a context_* event actually lands (this is the only way to close the S12 empty-context gap and U-ctx-4/U-setup-7). (c) Add the declarative-precondition flags and per-step template vars the plan-077/078 journeys already need (otlp_receiver_running, otel_captures_present, {new_trace_id} variants). (d) Implement the pty_runner journey step type for outcome-grade captures.

PHASE 3 (otbox journeys, the bulk): Write the journey-mechanism cases against the enriched checkpoints. P0 first: U-setup-2, U-setup-3, U-setup-4, U-setup-5, U-setup-6, U-bucket-1, U-bucket-4, U-bucket-5, U-trail-1, U-trail-2, U-trail-3, U-trail-4, U-ctx-1, U-ctx-5, U-ds-1, U-config-1, U-config-2, U-config-3, U-config-5. Then P1: U-auth-1/2/3, U-bucket-6/7, U-trail-5/6/8, U-ds-5/6, U-sec-1/4/5, U-hf-4, U-config-4, U-trace-8/10/12/13. Keep these tier=silver/gold and tagged migration- so they slot into the existing matrix gate.

PHASE 4 (genuine two-venv real-v033 UAT — what otbox CANNOT fake): These require a real `pipx install opentraces==0.3.3` then `pipx upgrade` (or pip the working-tree wheel) because the actual binary swap, the shipped-CLI first-run config migration, real-agent context capture, and live HF egress are exactly what the synthetic fake-harness elides. Build a make target (mirror the S7 Layer-B isolated-venv + SKIP-if-absent discipline) that: (1) installs 0.3.3 into venvA, captures a real session, asserts --version/--help lack trail/ctx/bucket; (2) upgrades in place to the 0.4 wheel into the same HOME; (3) runs U-setup-1 (setup upgrade idempotent + non-destructive), U-setup-7 (fresh real capture across all four substrates), U-ctx-3/4 (real OTLP capture + bypass-safety with receiver down), U-config-6 (shipped-CLI first-run config migration), U-ds-4 + U-hf-2 (full headless spine to a published 0.6.0 dataset on the live-HF token lane at ~/.opentraces/otbox-live-hf-token, including the schema-ahead refusal observed through the REAL v0.3.3 CLI exit code, not inferred). Gate this behind OT_REAL_REPL-style env flags so default CI stays green.

PHASE 5 (manual-UAT, documentation-grade): U-bucket-14 (push mental-model redirect), U-config-7 (TUI/viewer decommission redirect + completions). Capture screenshots/transcripts; these protect against silent-egress and broken-automation surprises that no automated assertion fully covers.

Sequencing rationale: Phase 0 is load-bearing — skipping it means every read-based journey/security test validates a lossy view and gives false confidence. Phase 1 buys the most contract coverage per hour. Phases 2-3 convert the migration-core suite into a real onboarding UAT. Phase 4 is the only thing that makes the 'a 0.3.3 user can pip-upgrade and use 0.4' claim honest end-to-end, and it is explicitly what otbox's synthetic capture cannot stand in for.
