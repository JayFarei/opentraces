# Changelog

All notable changes to the opentraces CLI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.5] - 2026-06-15

### Changed

- **OTLP receiver auto-start now works with an unsigned binary (e.g. a Homebrew
  install on macOS Ventura+).** The launchd plist / systemd unit now points at a
  shim (`~/.opentraces/bin/ot-otlp-receiver`) that resolves the CLI at run time,
  instead of pointing directly at the unsigned binary — so the unit's program is
  the signed system shell running a script, which macOS loads (the watcher
  already works this way), and it survives a `brew upgrade` that replaces the
  binary. The over-cautious unsigned-binary refusal is removed; the shim bakes in
  the configured `--port` / `--bind` / `--raw-bodies-dir`. No code signing
  needed (ad-hoc signing adds no real trust and is wiped by every brew upgrade).
- **The git commit→trace correlator now auto-attaches to every opted-in repo.**
  The watcher tick installs the per-repo post-commit hook (idempotent,
  best-effort) for any enlisted repo, so a global opt-in attaches the correlator
  to new and existing repos automatically — no per-repo `opentraces setup git`.
  Steady state is a single filesystem stat (no subprocess), so quiet ticks stay
  fork-free. The proven per-repo installer is reused (it chains to existing
  `.git/hooks` and never blocks the commit); no global `core.hooksPath` rewrite.

### Fixed

- **`opentraces setup capture-otlp` reported autostart success when it had
  actually skipped.** Success was inferred from "no exception raised", so a
  graceful `install_autostart()` `ok=False` was misreported as "installed (OK)"
  with `autostart_installed: true`. The human render now shows
  `not installed (<reason>)` plus the manual-start hint, the `--json` payload
  sets `autostart_installed: false`, and `next_command` points at
  `opentraces capture-otlp start`.

## [0.4.4] - 2026-06-15

### Fixed

- **`opentraces setup capture-otlp` autostart crashed (`unexpected keyword
  argument 'port'`).** `install_autostart()` had drifted from its caller, which
  passes `port` / `bind` / `raw_bodies_dir`; the launchd/systemd unit install
  raised a `TypeError` and fell back to "run it manually". The signature now
  accepts those (keyword-only, optional) and threads them into the unit's argv
  so a non-default receiver is honored at boot; the no-knob path
  (integration-repair) is byte-identical to before. Found dogfooding the 0.4.3
  release on a real machine.
- **`opentraces setup upgrade` failed under Homebrew when more than one tap
  defines `opentraces`.** It ran a bare `brew upgrade opentraces`, which brew
  refuses ("Formulae found in multiple taps") when a legacy `jayfarei/tap` sits
  alongside the canonical `jayfarei/opentraces`. It now upgrades the
  fully-qualified `jayfarei/opentraces/opentraces`.

## [0.4.3] - 2026-06-15

### Changed

- **`ctx prune --to-session <stem>` now uses the stem verbatim (#42).**
  Previously every stem got a uuid4 suffix, which made the documented rc=4
  no-clobber contract unreachable: repeated `ctx prune --write` with the same
  stem silently minted a new file instead of erroring, contradicting the
  collision error's own hint ("use a different --to-session stem"). Repeats
  now exit rc=4 with the structured `destination_exists` envelope. Anonymous
  (stem-less) prunes still mint a fresh `sess-<uuid>` id. The prune packet
  also gains additive `uuid_set` / `uuid_set_sorted` keys (the pruned record
  uuids in active-path order), and `ctx show`'s text mode now renders each
  layer's `capture_method` alongside `completeness` (the honest-provenance
  labels were JSON-only).

### Fixed

- **Watcher maturation livelock cured (#65, follow-up to the RSS bounding).** A
  maturation tick that hit its wall-clock budget discarded every commit it had
  already searched — `_flush_maturation_scratch` was gated on a non-truncated
  tick — and the per-commit loop re-walked already-searched commits each tick,
  so a cold backlog made zero forward progress and recomputed the same prefix
  indefinitely. Truncated ticks now flush the work they completed (the appended
  per-`(patch, commit)` search/anchor events dedup-skip on the next tick, so the
  backlog drains monotonically; the watermark still only stamps on a full,
  untruncated sweep), `_mature_patch_chunk` skips commits whose every chunk
  patch is already searched/anchored before re-running `git show`, and
  `reconcile_commit_anchors` checks the deadline AFTER the cheap dedup-skips so
  an already-searched prefix can't consume the budget and starve a partially
  searched commit's unsearched tail. Pinned by two tests that each fail without
  their corresponding fix.

- **Context Tree projection dropped every trace after the first on
  multi-session projects (#42).** `build_context_tree_projection` rebound its
  `trace_id` filter parameter while handling compaction/reconciled events, so
  `ctx tree`/`ctx compactions` on any trace ingested after the first returned
  the `context_tree_not_captured` empty state despite the events being on the
  log. Also, `orphan_branch_roots` now lists only true orphan roots (matching
  the emitter's documented semantics) instead of every rewind node, removing
  duplicate subtrees from `ctx tree --show-orphans`.

- **Watcher daemon unbounded RSS, root-caused and bounded by construction
  (#65, third recurrence of #23/#45).** Profiling one tick on a real 872K-event
  log named four full-log materialisation paths, each now incremental or
  streaming: the watcher reconciler persists a bounded projection at
  `<git-dir>/opentraces/reconciler_projection.json` and reads only the appended
  batch suffix per run (cold rebuilds stream through a retention sink; legacy
  behavior via `OT_RECONCILER_FULL_READ=1`); the events-mirror sync and the
  incremental event-log verify read the suffix instead of re-materialising
  history; the per-trace bucket export uses a trace-scoped read; the watcher's
  Context Tree projection is watermark-gated and fed from a scoped
  context-type read. Measured on the #65 repro: an active tick dropped from
  315s / 12.1GB peak RSS to 64s / 2.3GB, quiet ticks hold flat at ~280MB, and
  the multi-GB `event_log_snapshot.pkl` is no longer rebuilt per tick.
- **Maturation backlog amortised.** `mature_trails` accepts a wall-clock
  `deadline` (watcher default 120s/tick via `OT_MATURATION_TICK_BUDGET_S`);
  truncated sweeps skip the watermark stamp and resume next tick instead of
  doing a 710K-search / 14GB cold drain in one tick. The batched scan also
  streams anchor/search events down to dedup keys instead of retaining the
  plan-090 summary events (GBs on drained-backlog repos).
- **Leaked test enlistments pruned (#23 finished).** `run_sweep` quarantines
  tmp-rooted/stale and missing-target/stale enlistments to
  `~/.opentraces/pruned_projects/` (deleted after 30 days; `OT_WATCHER_PRUNE=0`
  to disable), and the pytest session now FAILS if any test leaks an
  enlistment into the real `~/.opentraces/projects/`.

### Added

- **One-shot `opentraces setup watcher sweep` + budgeted child ticks (#65).**
  The installed watcher shim now execs a one-shot sweep under launchd/systemd
  interval supervision, so a process that exits after each sweep cannot
  accumulate memory across sweeps. Each project ticks in a child process with
  an RSS budget (`OT_WATCHER_TICK_MAX_RSS_MB`, default 4096) and wall budget
  (`OT_WATCHER_TICK_TIMEOUT_S`, default 900); a pathological project is killed
  at its budget and the sweep continues. The legacy `run-forever` loop remains
  for older shims, with its #45 RSS backstop now checked between projects
  (mid-sweep) so it can actually fire.
- **Watcher provenance (#65).** The shim resolves the `opentraces` CLI at RUN
  time (probing brew/local bins) instead of freezing `sys.executable` at
  install time, since the frozen-interpreter shim broke silently when the
  install moved (pipx to brew). The daemon self-declares
  `~/.opentraces/watcher.status.json` `{version, executable, pid, verb}` on
  start, and `opentraces doctor` cross-checks shim/daemon/CLI and reports
  drift (`shim-interpreter-missing`, `shim-legacy-verb`,
  `daemon-version-drift`, `daemon-executable-missing`) under
  `watcher.provenance`. Re-run `opentraces setup watcher install` to adopt
  the new shim.

### Changed

- **Trace Intelligence `--waste` envelope flattened + bumped to
  `opentraces.context_waste.v2`.** `opentraces trace get|map --waste --json` now
  emits a single flat envelope with `schema_version`, `status`, `trace_id`,
  `fidelity`, `thresholds`, `findings`, `summary`, and `limitations` at the top
  level — matching the `--run-intel` and `trace compare` envelopes. The old
  shape nested the report under a `waste` key with `schema_version` inside it.
  **Breaking for consumers** parsing `.waste.schema_version` from the v0.4.0
  (v1) shape; read `.schema_version` / `.status` from the top level instead. The
  `opentraces.run_intel.v1` and `opentraces.trace_compare.v1` envelopes are
  unchanged.

## [0.4.0] - 2026-04-26

This release consolidates everything that accumulated on the 0.4.0 line since
the initial cut: the Trace Intelligence layer and substrate work grouped below,
a release-readiness simplification pass, the parallel Pi-capture opt-out work,
and otbox terminal-control journey footage.

### Release-readiness simplification + terminal-control footage (2026-06-04)

- **Codebase simplification (~ -4,857 LOC).** Removed dead modules (the
  `capture/http_proxy/` prototype, archived scripts, the broken `otc` shim,
  noop `hatch_build.py`), never-registered `trace_*` commands, and ~34 `_cli`
  forwarding shims; consolidated `core/` time + query helpers; fixed the live
  CI red-bar (`SECURITY_VERSION` doc drift). Split the largest modules
  (`trace_index.py`, `bucket_store.py`, `cli/trail.py`, `installers.py`,
  `cli/__init__.py`) into well-interfaced submodules and renamed
  `core/workflow.py` to `core/trace_stage.py`, all behavior-preserving.
- **Pi capture is opt-out under global tracking** (merged from the parallel
  Pi-capture line): Claude/Codex/Pi projects auto-enroll on first capture; opt
  out via manual mode or a per-project marker. `opentraces setup pi` manages the
  Pi package entry.
- **otbox terminal-control journey footage.** New `make otbox-footage` /
  `otbox-footage-all` record MP4 footage of every otbox user journey across the
  supported harnesses (claude, codex, pi, synthetic echo) via
  kitlangton/terminal-control, rendered into a reviewable gallery. Graceful
  degrades and stays default-CI-safe when termctrl/ffmpeg is absent.

### MERGED-I — Trace Intelligence

- **Three deterministic, derive-on-demand detectors over a single trace.**
  All read-side, no LLM, nothing persisted, no schema-package change. Each
  is computed on read and emitted as a frozen JSON envelope in the
  `opentraces.*.v1` family (a field change requires a version bump).
- **Context waste (`trace map|get --waste`).** New `core/context_waste.py`
  emits `opentraces.context_waste.v1`: `large_output` (a single tool output
  >= 12000 chars), `repeated_file_read` (same file 3+ times within 20 min),
  and `repeated_search` (`rg|grep|find|ag|ack` 5+ times within 10 min)
  findings plus a `summary` count block. Thresholds are overridable per call
  with `--large-output-chars`, `--file-read-window-min`, and
  `--search-window-min` on both `trace map` and `trace get`.
- **Run signals (`trace map|get --run-intel`).** New `core/run_intel.py`
  emits `opentraces.run_intel.v1` (`schema_version`, `status`, `trace_id`,
  `fidelity`, `signals`, `counts`) with deterministic `resteer` / `recovery`
  / `loop` / `failure` annotations. Recovery only fires after an uncleared
  prior failure; failure prefers structured `Observation.error` over
  substring matches; a repeated command is ONE `loop` signal carrying
  `evidence.repeat_count` (true sliding window); a one-word approval never
  reads as a resteer.
- **Run compare (`trace compare <a> <b>`).** New `core/trace_compare.py`
  emits `opentraces.trace_compare.v1`: per-side `fidelity` plus `{a, b,
  delta}` triples over token/cost metrics, deterministic quality persona
  scores (skip with `--no-quality`), and burst/error/security signals. Both
  traces are pinned to the same burst gap (`--burst-gap`, default 35) so the
  deltas are comparable. Degrades to `available: false` (never crashes) when
  a trace lacks a Trace Map.
- **Fidelity tier.** Every detector derives from the `TraceRecord` and
  reports `fidelity: "otel"` when `context_tree_summary.capture_methods`
  includes `otel` (plan 078 OTLP capture), otherwise `fidelity: "record"`.
- **CLI surface.** `--waste` / `--run-intel` are mutually exclusive with
  `--bursts` and each other (and with `--resume` on `trace get`); misuse
  exits 2 and an unresolved trace ref exits 6. The `trace get` and `trace
  map` surfaces emit byte-identical payloads for matching flags. See plan 086.

### MERGED-H — Trajectory Slicing

- **Adaptive burst gap (T2).** `core/bursts.py::detect_bursts` now
  picks `gap` per-trace from the median step-distance between
  consecutive `file_edit` / `patch_created` nodes, multiplied by 4
  and clamped to `[DEFAULT_BURST_GAP, ADAPTIVE_GAP_MAX]` (i.e. 35,
  100). Sparse traces (median delta 50+) widen up to 100; dense
  traces (median delta 1-2) stay at the 35-step default. The default
  floor is the central design choice — without it, dense iteration
  loops fragment at the first mid-burst pause longer than ~8 steps,
  which the entry #6 labeled regression confirmed is the wrong call.
  Explicit `gap=N` from callers (the `--burst-gap N` CLI flag) still
  wins unconditionally.
- **`tool_call_density` lifted to a top-level burst field (T6).**
  Cluster F's `quality_signals.tool_call_density` is now also
  exposed as `Burst.tool_call_density` and surfaces in
  `to_metadata()` so jq consumers can read it without descending into
  `quality_signals`. Pure aliasing; the underlying compute is
  unchanged.
- **`blast_radius` per burst (T7).** Each burst carries
  `blast_radius` with `lines_added`, `lines_removed`,
  `files_touched`, `test_files_touched`, `src_files_touched`,
  `docs_files_touched`. Test/src/docs are derived via path-pattern
  classification on the burst's `unique_files`; lines added/removed
  sum the `new_string`/`old_string` content from each patch's source
  Edit/Write tool call. Pure aggregation — no new git ops. On
  entry #6's `[32, 289]` burst the metric reports 9 files (7 test, 2
  src, 0 docs) and 257 lines added — matching the manual labels.
- **Hard split on user-instruction pivot (T9).** A new
  `hard_split_on_user_pivot=False` flag asks `detect_bursts` to
  split a burst at any non-trigger `user_instruction` strictly
  between two adjacent edits. Triggers (`yes`, `go ahead`, `ship
  it`) authorise in-flight work and never split. Default-OFF after
  a research finding: the entry #6 trace has 19 mid-burst
  redirections that all converge on commit `68d6723db`; default-on
  T9 fragmented that single labeled burst into 8 sub-bursts. The
  flag is opt-in for calibration corpora and downstream consumers
  who want the strict semantics.
- **Burst calibration corpus v1 (T1).** A small hand-labeled corpus
  under `tests/integration/fixtures/burst_calibration_corpus/v1/`
  with 5 traces (clean burst, two distinct bursts, mid-burst pivot,
  sparse session, dense loop) plus `labels.json`. The new
  `tests/integration/test_burst_calibration.py` validator runs the
  detector against the corpus and asserts ≥ 80 % accuracy at the
  labeled burst boundaries. Current accuracy: 100 % (6/6 labeled
  bursts across the two label modes).

### MERGED-G — Performance

- **Defer A4 survival enrichment from refresh-time to query-time (P1).**
  `_build_trail_units` no longer calls `sync_patch` per Git Anchor. The
  previous refresh-time enrichment ran 6+ git ops per anchor, turning a
  ~6 second refresh into a 10+ minute one on a project with 315 anchors.
  Refresh is now O(units) instead of O(units × git ops) and a 24-trace
  × 13-anchor synthetic build completes in well under 60 seconds.
  At query time the survival facet is patched lazily by reading the
  per-project survival cache event log: cache hits resolve in O(1),
  cache misses leave the facet as `"unknown"` and the caller can run
  `trail track --patch <id>` for fresh data.
- **`patch_survival_cached` event-log cache (P2).** A new TrailEvent
  type keyed by `(trace_patch_id, observed_head_id)` persists each
  `current_survival` row so subsequent calls against the same HEAD
  short-circuit the expensive compute path. The cache lives in the
  same append-only event log everything else does, so it survives
  between sessions, is correct under concurrent writers, and
  invalidates automatically when HEAD moves (the new HEAD's lookup
  misses the old key). Bare `*_sha` hex fields inside the survival
  row are wrapped into `{algo, hex}` GitObjectIDs at write-time and
  unwrapped at read-time so the on-disk shape is schema-valid.
  Within a batch the cache writes are queued on the
  `BatchSyncContext` and flushed in one `append_event_batch` at
  close — coalescing 100 ref-update transactions into one.
- **`BatchSyncContext` amortizes git operations across syncs (P3).**
  Two batchings inside `sync_patch`:
  - **Ancestry-from-HEAD set.** One `git rev-list HEAD` resolves
    "is X reachable from HEAD" for every anchor in O(n) memory
    instead of one `git merge-base --is-ancestor` per anchor.
  - **`git cat-file --batch` pipe.** A long-lived
    `git cat-file --batch` pipe serves every blob read with a
    framed read-exactly loop (handles short-read pipe semantics)
    instead of spawning `git show` per call. `stderr` is routed
    to `/dev/null` so cat-file diagnostic output never deadlocks
    the pipe.
  - **Skip per-observation ancestry probe inside the descendant
    walk.** `_commits_from_anchor_to_head` already proves the
    anchor is reachable from HEAD before returning; re-running
    `merge-base --is-ancestor` per descendant is pure overhead.
    `_compute_survival` accepts a `skip_ancestry_check` flag for
    that case while keeping the orphan / unreachable path fully
    probed.
  - **Lazy `_find_revert_commit`.** The revert search is one of
    the costliest calls in the survival pipeline. We now defer it
    behind a per-observation closure that fires only when the
    file-deleted or preserved-zero branches are reached.
  - **`BatchSyncContext` is shared across calls automatically.**
    `sync_patch(events=...)` looks up a per-`(id(events), repo,
    fingerprint)` context so the CLI batch path
    (`cli/trail.py::_emit_batch_track`) gets P3 amortization
    without having to import `BatchSyncContext`.
- **Process-level `read_events` memo.** `read_events(repo)` caches
  the result keyed by `(repo, event_log_ref_head, verify)` so a
  CLI invocation that calls `read_events` more than once per
  process (the batch path calls it twice — once to enumerate
  patch ids, once to thread into `sync_patch`) shares one read.
  The cache invalidates automatically when the ref head advances.
- **Telemetry results.** `trail track --since 24h --limit 600`
  drops from a 248-303s baseline to ~8s warm and ~175s on a fully
  cold cache (target 60s; remaining cold-time is dominated by the
  per-anchor descendant walk over real-project history). Warm
  cache hits are at 13/13 labeled regression assertions.

### MERGED-F — Survivorship Hygiene

- **`trace_id` in batch `trail track` output (D1).** Every JSONL row
  emitted by `trail track --since` / `--all` / `--patches-from` now
  carries a top-level `trace_id` looked up from the
  `trace_patch_created` event payload. JSONL consumers can group rows
  by trace in one `jq` expression without a sidecar projection.
- **`lost_at_commit_sha` on lost patches (D2).** When a patch's
  survival_state is `lost` because the file was deleted, the row now
  carries the killer commit's SHA. Resolution walks
  `git log --diff-filter=D` once per `(file_path, anchor_commit, head)`
  triple — a per-batch cache shared across all patches keeps a
  27-patch survey at a handful of git calls. The cache is exposed as
  the new `lost_attribution_cache` parameter on `sync_patch`,
  `sync_anchor`, and the internal `_compute_survival`.
  `lost_attribution_failed` is appended to `limitations` when the
  resolver runs but cannot identify a single deletion commit.
- **`lost_kind` discriminator (D3).** Every `lost` survival row now
  also carries `lost_kind: "file_deleted" | "hunk_removed"`. File-alive
  cases (the hunk's authored lines vanished while the file lives on)
  intentionally leave `lost_at_commit_sha` as `None` — line-level
  attribution via `git log -L` is too expensive and ambiguous for the
  cheap path. Reviewers can now sort lost patches by recoverability.
- **Retention fraction split (D4).** `_compute_survival` now emits
  three retention fields on every alive observation:
  `retention_fraction_at_anchor` (1.0 when the anchor's range still
  resolves, measures lineage strength), `retention_fraction_at_original_range`
  (the existing literal preserved/authored ratio), and
  `retention_fraction` (alias preferring `_at_anchor`). For
  `alive_transformed` patches the alias is now 1.0 instead of 0.0,
  truthfully signaling "the anchor lives, lines drifted." For
  `partially_preserved` and `alive_moved` the literal fraction is
  preserved.
- **`dataset publish --min-retention X --exclude-state STATE` (D8).**
  Two new filter flags drop low-quality rows in flight before staging.
  `--min-retention` (0.0-1.0) drops rows whose mean
  `retention_fraction` across `patches_with_survival` is below the
  threshold; `--exclude-state STATE` (repeatable) drops rows that have
  any patch with `survival_state == STATE`. Both compose; under
  `--check-only` the drop counts surface in `publish.filter` JSON
  without uploading.
- **`dataset list / status` row_quality summary (D9).**
  `--json` output now carries a `row_quality` block:
  `{total_rows, rows_with_anchored_patches, rows_with_lost_patches,
   rows_fully_alive, mean_row_retention, survival_distribution}`.
  Computed once per call from the dataset's row JSONL, catches
  low-quality datasets before publish.
- **Burst commit-message quality tier (D11).** `intent.commit_message_quality`
  on every `change_burst` carries `{tier, subject_length, body_length,
  has_conventional_prefix, paragraph_count}`. Tiers: `bare` (subject
  only) / `terse` (≤140 char body, single paragraph) / `descriptive`
  (140-500 char body) / `detailed` (>500 chars or ≥2 paragraphs).
- **Burst error / tool-call signals (D12).** `change_burst.quality_signals`
  carries `{error_signal_count, test_run_count, tool_call_count,
  tool_call_density}` — counted from TraceMap nodes whose step_index
  falls within the burst's range. Pure count-as-you-go pass with no
  extra trace traversal.
- **`patches_with_survival` on bursts.** `detect_bursts` now enriches
  each burst with a `patches_with_survival` list when the project's
  TrailEvent log carries matching `trace_patch_created` events for the
  burst's trace_id and step window. Each row joins the patch's anchor
  identity (`commit_sha`, `evidence_firmness`, `evidence_tier`) with
  its current survival observation (`survival_state`,
  `retention_fraction*`, `lost_kind`, `lost_at_commit_sha`). All
  patches share one `lost_attribution_cache`.

### MERGED-E — Intent Richness

- **Structured `intent` object on `change_burst` nodes (I7).**
  `trace map --bursts` and `trace get --bursts` now expose an `intent`
  dict on each burst node's metadata:
  `{trigger, most_substantive_spec, spec_chain, burst_commit_sha,
  commit_subject, commit_body}`. The legacy `intent_text` and
  `intent_user_step` fields remain as aliases for
  `intent.most_substantive_spec.{text, step}` (or trigger when no spec
  exists). `intent_text` is now considered deprecated.
- **Trigger detection (I1).** New `core/intent.py::is_trigger`
  recognises short imperatives that authorise a discussed action
  ("yes", "ok", "go ahead", "let's go ahead and commit", "ship it",
  "why don't we...") and refuses to flag long messages that merely
  *start* with a trigger phrase ("yes implement the redis migration
  with TLS..."). Image placeholders (`[Image: ...]`, `[Image #N]`) are
  stripped before pattern matching so a hybrid screenshot+question
  message still surfaces the question as a spec.
- **Spec walkback / spec_chain (I2).**
  `core/intent.py::derive_intent_chain` walks every `user_instruction`
  node up to the burst's first step, splits triggers from specs, and
  returns `most_substantive_spec` (latest non-trigger before the burst)
  plus `spec_chain` (every non-trigger user instruction from the start
  of the trace up to and including most_substantive_spec, ordered by
  step_index). Trigger-only bursts have `most_substantive_spec=None`
  and an empty chain.
- **Commit body lookup (I3).** When a burst has any patch carrying
  `metadata.commit_sha`, `detect_bursts` computes the modal commit
  across patches and runs `git log -1 --format=%s/%b <sha>` to populate
  `intent.commit_subject` (one line) and `intent.commit_body`
  (multi-line, capped at 5,000 chars). Subprocess failures (missing
  `git`, missing repo, unknown SHA) are absorbed into
  `intent.commit_lookup_error` rather than raised. New
  `--no-commit-lookup` flag on `trace map` and `trace get` skips the
  lookup entirely for offline / hot-path runs. When the trace map has
  no `patch_created` nodes (the integration-regression path) the
  burst commit falls back to the first git commit observed in the
  burst's step range via the trace's post-tool hook trail.
- **`burst_commit_sha` first-class (D5).** The modal commit_sha is
  surfaced at the top level of the burst node's metadata as
  `burst_commit_sha`, not buried inside `intent`. Critically this is
  *not* the trace's `outcome.commit_sha`: a single trace can ship
  multiple commits, and the burst's commit is the one corresponding to
  the burst's edits (often the *first* commit in the burst's step
  range, not the last commit of the session).
- **`unique_files` dedup (D6).** Foreign-agent absolute prefixes
  (e.g. `/Users/06506792/...`) and the resolved repo root are stripped
  before incrementing the per-file counter, so absolute and
  repo-relative variants of the same file collapse onto one entry.
  When the burst has a known commit, the file set is filtered to
  files in that commit, edits past the commit step are clipped (so a
  burst spanning multiple commits represents only its own commit's
  work), and a final reconcile pass caps each file's count at the
  Git diff's hunk count when the gap is small (≤ 2) so per-file
  counts match the canonical hunk-per-file shape downstream consumers
  reason about. Larger gaps preserve the Edit count (the authoring
  trail tells us more than the merged diff in those cases).
- **CLAUDE.md / SKILL.md clarifications (D7).** New paragraph in
  CLAUDE.md and a "Bursts and intent" subsection in `skill/SKILL.md`
  document that one trace patch = one Edit/Write tool call (not one
  file), `unique_files` is per-file (deduped) while `patches` is
  per-hunk, and `burst_commit_sha` is distinct from the trace's
  `outcome.commit_sha`.

Acceptance: Cluster E pushes
`tests/integration/test_entry6_labeled_regression.py` from 4/13 hard
labels passing to 13/13. The structured intent object is visible on
`./otd --json trace map 185b0a55-... --bursts`:

    .map.nodes[] | select(.action_type == "change_burst") | .metadata.intent

surfaces the trigger (step 26 — "lets go ahead and make a commit about
this fix"), the most substantive spec (step 19 — "How do I reference a
given dataset? ... I'm interested in your apply command ... how is that
different from pull?"), the spec chain (steps 1, 12, 19), the burst's
commit SHA `68d6723dbb`, its subject "refactor(dataset): rename `apply`
to `clone`, add `--data` for one-step setup", and its body explaining
why `apply` was opaque.

### MERGED-A — Indexer Reliability

- **Trail-projection cache self-heals after staleness.** `_build_trail_units`
  no longer swallows `OSError | RuntimeError | ValueError | ValidationError |
  SubprocessError` and silently returns `[]`; `_rebuild_trail_projection`
  catches build failures explicitly and skips recording `trail_sources` so the
  next refresh retries instead of marking a stale-but-empty cache as fresh.
- **Ref-advance during rebuild is captured.** `_rebuild_trail_projection`
  now reads the event-log ref a second time after the rebuild and records the
  post-rebuild head; if the ref advanced mid-rebuild the row is tagged with a
  structured `trail_event_ref_advanced_during_rebuild` limitation in the new
  `trail_sources.limitations_json` column. Schema bumped to
  `plan056-m1-v4`.
- **Index DB uses WAL + busy-timeout.** Every connection runs through
  `_connect`, which sets `PRAGMA journal_mode=WAL` and `PRAGMA
  busy_timeout=5000`. Top-level `rebuild_index` / `refresh_index` now retry
  transient `sqlite3.OperationalError: database is locked` errors and surface
  a typed `IndexLockedError` if the retry budget is exhausted, so concurrent
  `trace query` invocations no longer race a raw `OperationalError`
  traceback.
- **Patches and git_anchors are queryable candidates.** Each `TraceUnit` of
  `unit_type ∈ {patch, git_anchor}` now produces a `CandidatePacket` with
  `candidate_kind ∈ {patch, git_anchor}` and matching facets. `git_anchor`
  candidates carry a `trail.survival_state` facet sourced from the projection's
  `current_survival.survival_state` (falling back to `unknown`). The trail
  projection rows now expose `event_time` (and `trace_patch_event_time`) so
  `--since` filters can age patch / git_anchor candidates.
- **`candidate_kind` is never null.** Every candidate now reports a
  `candidate_kind` — `bug_fix` when the signal-derived label fires, otherwise
  the unit type (e.g. `trace`, `patch`, `git_anchor`, `skill_invocation`,
  `tool_sequence`, `test_or_error_signal`). Tool-less prompt traces get
  `candidate_kind=trace` instead of `null`.
### MERGED-B — Trace Map Projections & Bursts

Cluster B / Plan 54 trace-map projection work. Targets the "1.6 MB
trace map for one heavy trace" pain point: ships a compactness filter,
a first-class burst projection, intent propagation, and path
normalization so consumers can pull "all bursts in the last 12h with
intent + patches + git anchor" with a one-line jq.

### Added

- `opentraces trace map <id> --actions <comma-list>` projection filter.
  Keeps only nodes whose `action_type` is in the comma-separated list,
  drops edges that cross removed nodes, preserves the structural
  skeleton. Default behavior unchanged. Canonical lineage subset:
  `user_instruction,file_edit,patch_created,git_anchor,test_run,error_signal,final_response`
  (typically ~50% size reduction on heavy traces).
- `opentraces trace map <id> --bursts [--burst-gap N]` projection.
  Detects change bursts deterministically by clustering `file_edit`
  and `patch_created` nodes by `step_index` proximity (default
  gap=35), emits one virtual `change_burst` node per burst carrying
  `step_range`, `intent_user_step`, `intent_text`, `unique_files`,
  `patches[{patch_id, git_anchor_id, commit_sha, evidence_firmness, evidence_tier}]`,
  `unique_git_anchors`, and `has_git_anchor`. Edges between
  consecutive bursts are emitted as `previous_next` for ordering.
- `opentraces trace get <id> --bursts [--burst-gap N]` convenience.
  Returns only the burst summary list (no map skeleton) for one-shot
  consumers. Same algorithm as `trace map --bursts`.
- `TraceMapNode.active_user_step: int | None` — populated on every
  node by a single forward pass during `build_trace_map`. Points back
  to the most recent preceding `user_instruction` (or `None` if none
  precedes). Eliminates the "walk back to find intent" pattern.
- `TraceMapNode` action_type Literal extended with `change_burst`.

### Changed

- Path normalization in `files_modified` / `files_read`. The trace
  map builder now resolves the project's repo root from
  `metadata.cwd` or `metadata.hook_pre_tool_use[*].trail.worktree_root`
  and strips that prefix from absolute tool-call paths. Absolute
  siblings are preserved in
  `node.metadata.files_modified_absolute` /
  `node.metadata.files_read_absolute` for downstream needs. Paths
  outside the determined root remain absolute. When no root can be
  determined, traces with absolute paths surface a
  `path_normalization_failed` limitation on the map.
- `_files_for_tool` (read mode) now also reads the `file_path` key
  alongside `file`/`path` so Claude Code Read invocations populate
  `files_read` correctly.

### Internal

- New module `opentraces.core.bursts` with `detect_bursts`,
  `bursts_to_trace_map`, and a `Burst` dataclass.
- New tests: `tests/core/test_bursts.py`,
  `tests/core/test_trace_map_actions_filter.py`,
  `tests/cli/test_trace_map_cli.py`.
### MERGED-C — Survivorship surface & batch track

### Added

- `opentraces trail track --since <duration>` / `--patches-from <file>` /
  `--all` — batch survival surveys that emit one JSONL row per Trace
  Patch. Uses an in-process loop over `core.trails.sync.sync_patch` with
  a single shared `read_events` snapshot, so 100 patches take under 5s
  in-process (vs. ~5min via per-patch subprocess). `--limit N` caps
  output. The original input id is preserved at the row level so
  callers can group rows back to their request even after sync_patch
  normalizes the canonical id.
- `opentraces trail track <TRACE_ID> --warn-missing-patches` — surface
  a `trail_capture_incomplete` limitation plus structured counts when a
  trace has `file_edit` events but zero `trace_patch_created` events
  (the indexer-bug fingerprint).
- `opentraces doctor --json` now includes a `trail_capture_audit`
  panel that scans the last 7 days of TrailEvents for traces with the
  same `file_edit > 0 AND patch_created == 0` shape.
- `retention_fraction: float | None` on every survival observation row.
  Computed as `surviving_authored_lines / total_authored_lines`,
  rounded to 3 decimal places. `1.0` for `alive_on_path`, computed for
  `alive_transformed` / `alive_moved` / `partially_preserved` /
  `repaired`, `None` for `lost` / `unknown` / `orphan_branch` /
  `missing_authored_text` / `reverted`.

### Changed

- The overloaded `survival_state="unknown"` is split into four specific
  causes:
  - `orphan_branch` — anchor commit not in HEAD's ancestry (was
    `unknown` + `anchor_commit_not_reachable_from_head`).
  - `missing_authored_text` — patch lacks original line content (was
    `unknown` + `missing_authored_text`).
  - `never_committed` — patch authored but no Git Anchor matured AND
    the file currently exists in HEAD (the patch was superseded
    intra-trace before any commit captured it).
  - `unknown` — kept as the fallback for genuinely indeterminate cases
    (anchor commit / path / observed_commit_id missing).
- `core.trails.sync.sync_patch` and `sync_anchor` now accept an
  optional pre-loaded `events: list[TrailEvent]` so batch callers can
  amortize the one-time read cost across many patches.

### MERGED-D — CLI ergonomics & ad-hoc datasets

### Added

- Top-level `--json` flag now propagates to every subcommand. When
  set on the root group (e.g. `opentraces --json trace query`), the
  resolved leaf command receives `--json` automatically if it
  exposes the option, so an agent-level "JSON mode" toggle no
  longer needs to be repeated at the verb. Implemented as a hook on
  `click.Command.make_context` that injects `--json` into
  sub-command args when the root context has `json_mode` set and
  the resolved command has a `--json` option. Idempotent and a
  no-op for commands that don't expose `--json`. The `status`
  command's human prelude (Rich header, table, footer rule, hint
  block) is also gated on `_json_mode` so its JSON tail is
  reachable via the documented `---OPENTRACES_JSON---` sentinel
  without table noise on stdout.
- Ad-hoc dataset path: `opentraces dataset new <name> --rows-file
  <jsonl> --schema <json-schema>` synthesizes a manual dataset
  without requiring a workflow skill. Manifest is marked with
  `workflow.skill = "manual"`, schema is copied to
  `schemas/row.schema.json`, and rows are validated against the
  supplied JSON Schema and appended via the standard
  `core.datasets.append_rows` path so they participate in the same
  identity / deduplication / publication-state machinery as
  workflow-driven datasets. `--rows-file` and `--schema` must be
  provided together; either alone is rejected with a usage error
  before the dataset directory is created.
- `dataset list` and the new `dataset status <name>` command expose
  `manual: true` plus a `row_count` field for ad-hoc datasets so
  agents can detect them without re-reading the manifest skill
  string.
- `dataset run <name>` short-circuits on a manual dataset and emits
  `{"status": "manual_dataset_no_run_action", ...}` instead of
  invoking the workflow runner; `review` / `approve` / `reject` /
  `publish` work unchanged on manual datasets.
- New `opentraces dataset status <name>` command emits row count and
  publication-state counts (e.g. `needs_review`, `publishable`) as
  JSON or a one-line human summary.

### Tests

- `tests/cli/test_global_json_propagation.py` (7 cases) pins the
  propagation contract for `status`, `trace query`, `trace map`,
  `trail track`, `dataset list`, `doctor`, and the
  subcommand-scoped `--json` regression.
- `tests/cli/test_dataset_rows_file.py` (6 cases) covers manifest
  shape, dataset-list visibility, status row count, JSONL
  validation, mutual `--rows-file` / `--schema` requirement, and
  `dataset run` no-op behavior.
- `tests/integration/test_dataset_adhoc_roundtrip.py` exercises the
  full create / list / status / review / approve / run loop on a
  manual dataset.

### Trace Trails Phase 5: initial 0.4.0 cut (2026-04-26)

This subsection ships **Trace Trails Phase 5**: a VCS-anchored evidence
substrate that links agent trace steps to the Git history that accepted
their patches. Trace Trails are exposed via a new `opentraces trail`
command group, an append-only `TrailEvent` log, and stable `ot://`
resource identifiers. The substrate is designed to survive
format-then-commit pipelines, hook failures, and Git history rewrites
without losing replayability.

### Added

- New `opentraces trail` command group with six subcommands:
  - `trail explain` — explain the evidence chain for a trace step,
    commit, or `path:line` target. Reports Trace Snapshot refs, Trace
    Patch identity, Git Anchor (when present), evidence tier, firmness,
    source events, and limitations.
  - `trail diff --trace <id> --from-step <a> --to-step <b>` — emit the
    Trace Patch between two captured step snapshots.
  - `trail follow --patch <id>` / `--anchor <id>` — follow an anchored
    Trace Patch through later Git history. Reports `current_observations`
    (one per anchor) and `current_survival`. Bound by `--history-limit N`
    (default 500, min 2).
  - `trail rebuild` — re-derive advisory snapshot refs under
    `refs/opentraces/local/traces/...` from the canonical event log.
    Idempotent.
  - `trail attach --trace <id> --commit <sha>` — retroactively connect a
    trace's evidence to a Git commit after a hook failure. New events
    carry `capture_method=["manual_attach"]`. Append-only and idempotent;
    source events are byte-identical after attach.
  - `trail resolve <ot://...>` — resolve stable resource IDs:
    `ot://trace/<id>/patches/<patch_id>/trail`,
    `ot://git-anchor/<id>`, `ot://file/<path>/line/<n>/origin`.
- Append-only `TrailEvent` batch log under
  `refs/opentraces/local/events/v1`. Batch commits embed snapshot trees
  as subtrees so the log survives `git gc --prune=now --aggressive`.
- Post-commit Trace Trail anchors with two-tier identity: exact
  whitespace-collapsed range hash first, structural-match fallback (line
  similarity ≥ 0.85). Firmness drops `firm` → `provisional` on fallback
  so consumers can filter by confidence.
- Watcher reconciler that consumes `filesystem_mutation_observed` events
  alongside `trace_step_window_opened` / `trace_step_window_closed`
  events and emits or upgrades `trace_patch_created` events with
  `capture_method=["...", "watcher_backstop"]` only when the mutation
  interval is fully inside exactly one writer's *firm* step window.
  Idempotent: re-running on the same event set produces identical
  attributions, keyed by `observation_event_id`.
- Format-then-commit handling and `git_anchor_superseded` events tagged
  `capture_method=["post_rewrite_hook"]` for `git commit --amend`,
  `rebase`, and `reset`-then-recommit. Cherry-pick is not treated as a
  rewrite — both commits coexist and both receive anchors.
- Survival states extended beyond the Phase 4 set with `alive_moved`
  (rename detection via `git log -M --name-status`),
  `partially_preserved` (subset of authored lines survives elsewhere in
  the file), and `repaired` (a non-anchor committer touched the anchored
  range, detected via `git blame --line-porcelain`).
- Closed `capture_limitations` vocabulary on TrailEvents:
  `concurrent_writer_overlap`, `unbounded_mutation_window`,
  `background_process_overlap`, `hook_only`,
  `hook_payload_state_mismatch`, `session_terminated_unexpectedly`,
  `watcher_buffer_overflow`, `incomplete_step_window_capture`.
  Trail-construction limitations such as
  `patch_trail_history_truncated` are reported separately under
  `trail_limitations` at the response root.
- Slice resource resolution: `trail resolve` returns normalized slice
  fields (`containing_segment_id` plus ID-only Trace Slice metadata)
  that can be navigated through `ot://` references. Deeper
  prompt/tool/observation/file content materialization is slated for
  the Trace Dataset projection.

### Changed

- Capture hook installer now uses `sys.executable` instead of a
  hard-coded `python3` and prunes stale opentraces hook entries during
  reinstall, so virtualenv installs and pyenv shims work without PATH
  gymnastics.
- `otd` development shim now reports `prog_name="otd"` so help and
  error output render correctly when used in place of the installed
  `opentraces` console script.

### Fixed

- `opentraces trail explain` text output for steps without a captured
  patch now reports `patch status: no_patch` / `relation: no_patch`
  instead of an empty/ambiguous block.
- `opentraces trail explain` slice fields are normalized between text
  and JSON output paths.
- Phase 5 reconciler hardening: firm-window enforcement, project-local
  lock to serialize concurrent reconciliations, and stricter validation
  of TrailEvent batches.

`SCHEMA_VERSION` unchanged (`opentraces-schema` remains `0.3.0`).
`SECURITY_VERSION` unchanged at `0.3.0`.

## [0.3.3] - 2026-04-20

### Fixed

- Hugging Face dataset schema drift. The hand-maintained `dataset_infos.json`
  features map in `publish/huggingface/schema.py` lagged behind `TraceRecord`,
  so rows with `task.repository_url`, `metrics.total_cache_read_tokens`,
  `metrics.total_cache_creation_tokens`, `generation_index`, or richer
  `attribution.*` were rejected by HF's datasets-server with `CastError` /
  `StreamingRowsError`. The features map now generates directly from the
  Pydantic model on every push, so the declared schema tracks the shipped
  rows automatically.

### Added

- **Push safety against remote version drift.** `ot push` now fetches the
  remote `dataset_infos.json` during `ensure_repo_exists` and compares
  versions. Remote schema newer than local fails with exit 3 and an
  `ot setup upgrade` hint (never overwrite a newer declared schema).
  Remote equal skips the re-upload (no more no-op commits per push).
  Remote older / missing / malformed falls back to uploading the local
  schema.
- **Additive-evolution contract** documented in
  `packages/opentraces-schema/VERSION-POLICY.md`. MINOR / PATCH bumps must
  be additive; breaking changes require MAJOR plus a registered migration
  in `opentraces_schema.migrations`. This is the invariant that makes
  silent shard migration safe when a newer client pushes to an older
  dataset.
- **Migration guard** in `detect_outdated_shards` / `migrate_outdated_shards`.
  Only rows strictly older than the local schema are rewritten; rows at or
  above the local schema are preserved byte-identically so a brief client
  downgrade cannot drop future fields.
- **Capture: away_summary recaps** from Claude Code sessions are preserved
  as mid-session intent snapshots instead of being dropped.
- **Agent discovery surface on the marketing site**: `/sitemap.xml`, Agent
  Skills discovery index under `/.well-known/agent-skills/`, Web Bot Auth
  signing-directory stub, Content-Signal AI-usage preferences in
  `robots.txt`, Link headers for discovery on the homepage, markdown
  content negotiation on `/` and `/docs`, a WebMCP tool surface, and
  explorer deep-links via `?u=<username>`.
- **Performance harness** with regression smokes across CLI, TUI, viewer,
  watcher, web, and push paths (internal; budgets tracked under
  `tests/perf/`).

### Internal

- `[tool.hatch.build.targets.sdist.force-include]` now carries
  `web/viewer/dist` into the sdist. The unanchored `dist/` gitignore rule
  previously excluded the viewer bundle from sdists, so `python -m build`
  (sdist → wheel) failed with "Forced include not found". CI's wheel-only
  build path still worked, but `make build` and local test-PyPI dry-runs
  now work end-to-end.
- `datasets>=2.16.0` moved into the `[dev]` extra so the new HF Features
  regression tests run under the documented `pip install -e ".[dev]"` setup.
- `.gitignore` ignores `.venv-*/` and `.DS_Store` to keep stray artifacts
  out of future sdists.

No `SCHEMA_VERSION` change (`opentraces-schema` remains `0.3.0`).
`SECURITY_VERSION` unchanged at `0.3.0`.

## [0.3.2] - 2026-04-17

### Fixed

- `ot init` surfaces all user datasets during first-time setup and adds a
  manual repo-entry path, so users with many datasets or unusual names can
  still pick or type their target instead of being stuck behind the
  auto-detect list.
- Web review UI no longer falls back to sample data, so an empty inbox
  renders as empty instead of showing placeholder traces.
- Release-note fenced code blocks render correctly on the marketing site
  and in the bundled agent skill.

No `SCHEMA_VERSION` or `SECURITY_VERSION` changes.

## [0.3.1] - 2026-04-17

- `flask` and `textual` promoted from the `[web]` / `[tui]` optional
  extras into the base dependencies so `opentraces web` and
  `opentraces tui` work immediately after `pip install opentraces` with
  no extras required. The extras remain in place for backward
  compatibility with existing install commands.
- Site: home terminal on the landing page now defaults to the `init`
  tab instead of `review`.

No schema or security-pipeline changes (`SCHEMA_VERSION` and
`SECURITY_VERSION` remain `0.3.0`).

## [0.3.0] - 2026-04-16

First public release of the CLI since `0.2.1`. A coherent single bump that
folds together the internal development iterations from the past week:
a full git-style command restructure on the user-facing side, an internal
code reorganization on the maintainer-facing side, plus new attribution /
commit-correlation surfaces, BLOCKED-status enforcement, per-remote upload
tracking, and the security pipeline refresh described in `security/version.py`
(`SECURITY_VERSION` 0.2.0 → 0.3.0).

### Added

**Attribution and commit correlation**

- `ot blame <file>:<line>` resolves a file line to the trace + conversation
  that produced it, using murmur3 content hashes + per-range change_type
  metadata. Cross-refactor tracking survives formatter / linter rewrites.
- `ot notes <ref>` inspects the `refs/notes/opentraces` store where the
  post-commit hook pins traces to revisions.
- `ot setup git` installs the post-commit correlator + PostToolUse diff
  capture hooks for the current repo.
- `ot list --by-commit` groups trace listings by their correlated revision.
- `ot export --format agent-trace` emits Agent Trace-compatible JSON for
  traces that carry attribution blocks.
- `ot show --markdown` renders a prompt-injection-safe markdown transcript.

**Command surface (git-style flat verbs + resource nouns)**

- `ot add <ids>... | --all` stages traces for push (mirrors `git add`).
  Variadic. Refuses `BLOCKED` and `REJECTED` traces with a pointer to the
  unblocking verbs (`ot redact`, `ot reject`, `ot reset`).
- `ot show <id>` shows one trace (flat; replaces `ot trace show`).
- `ot list [--projects] [--remote <name>] [--by-commit]` lists traces;
  with `--projects`, lists every directory that ran `ot init`; with
  `--remote <name>`, filters to traces missing on that remote.
- `ot reject <ids>...`, `ot reset <ids>...`, `ot discard <id>` — per-trace
  curation, flat (replaces `ot trace ...`).
- `ot redact <id> <pattern> [--regex] [--field] [--step]` — content-targeted
  find-and-replace. Replaces the old `--step <n>`-only blank-the-whole-step
  shape. Permanent, no undo.
- `ot llm-review` — Tier-2 LLM semantic review (renamed from `review-llm`;
  the `llm-` prefix keeps the machine-pass nature explicit vs the human
  review handled by `review_policy`).
- `ot remote add/set-url/rename/remove/use/list` — git-parity verbs on the
  remote noun. URL accepts `hf://user/repo` or short form `user/repo`
  (auto-expanded; HF is the default backend).
- `ot auth login/logout/whoami` — group equivalent of the flat `ot login`
  / `logout` / `whoami` (which still work for back-compat).
- `ot completions install | uninstall | [shell]` — cf-style shell
  completions with dynamic delegation. Bare `ot completions` prints the
  script for `$SHELL`. Supports bash, zsh, fish.
- `ot setup upgrade` — `ot upgrade` is now a setup subcommand.
- `ot config set <key> <value> [--append] [--project|--global]` — proper
  key/value setter, replacing the previous append-only-with-flags shape.
  Legacy flags preserved for back-compat.
- `--project` flag added to `setup trufflehog`, `setup review-llm`,
  `setup review-policy` for explicit per-project scope (mirrors
  `git config --local`).
- gh-style help: `ot --help` is sectioned (CORE / INBOX / PROJECT /
  RESOURCE) with one-liner descriptions.

**Pipeline behaviour**

- `BLOCKED` is now a real, written status. TruffleHog findings move traces
  to `BLOCKED` with a `block_reason`, surfacing in `ot status` and the
  inbox. `ot add` refuses `BLOCKED` traces, making staging a true approval
  gate.
- Per-remote upload tracking. Switching remotes and running `ot push`
  replays the full local history to the new remote (mirrors
  `git push <new-remote>`). Driven by a new
  `TraceStagingEntry.uploaded_to: dict[str, str]` field.

### Changed

**Internal code layout (maintainer-facing; breaking imports)**

Top-level package reorganization from 15 top-level items to 7:

- `agents/` + `parsers/` + `installers/` + `enrichment/git/post_commit.py`
  → `capture/`
- `exporters/` + `upload/` → `publish/`
- Top-level glue modules (`config`, `paths`, `state`, `workflow`, `inbox`,
  `pipeline`, `processors`) → `core/`
- Business logic extracted from `clients/` and `cli.py` into
  `core/review.py` + `core/publish_flow.py`
- `cli.py` split into a `cli/` package

### Removed

**Breaking import paths (maintainer-facing)**

- `opentraces.agents.*` → `opentraces.capture.*`
- `opentraces.parsers.*` → `opentraces.capture._base` or
  `opentraces.quality.parse_gate`
- `opentraces.installers.*` → `opentraces.capture.*`
- `opentraces.exporters.*` → `opentraces.publish.*`
- `opentraces.upload.*` → `opentraces.publish.huggingface.*`
- `opentraces.state`, `opentraces.config`, `opentraces.paths`,
  `opentraces.workflow`, `opentraces.inbox`, `opentraces.pipeline`,
  `opentraces.processors` → `opentraces.core.*`
- `opentraces.enrichment.git.post_commit` →
  `opentraces.capture.git.post_commit`

### Security

- `SECURITY_VERSION` bumped `0.2.0` → `0.3.0` to reflect detection-rule
  changes made this cycle (regex patterns, entropy thresholds,
  classifier heuristics, anonymization rules). Traces emitted by this
  CLI carry the new version string in `SecurityMetadata.classifier_version`.

### Schema migrations (auto, on first command after upgrade)

- `TraceStagingEntry` gains `uploaded_to: dict[str, str]`. Existing
  `status=UPLOADED` traces are backfilled with
  `uploaded_to = {"origin": <existing uploaded_at>}`.
  `STATE_SCHEMA_VERSION` bumped to `"2"`.
- `ProjectConfig` gains `remotes: dict[str, RemoteConfig]` +
  `active_remote: str | None` + `default_visibility: str`. Legacy single
  `remote: str` + `visibility: str` are migrated to
  `remotes = {"origin": RemoteConfig(...)}` and `active_remote = "origin"`.
  Marker version bumped 1 → 2.

### Migration table (user-facing commands)

| Old | New |
|---|---|
| `ot commit` | `ot add` |
| `ot trace commit` | `ot add` |
| `ot trace list` | `ot list` |
| `ot trace show <id>` | `ot show <id>` |
| `ot trace reject <id>` | `ot reject <id>` |
| `ot trace reset <id>` | `ot reset <id>` |
| `ot trace redact <id> --step <n>` | `ot redact <id> <pattern>` (new content-targeted shape) |
| `ot trace discard <id>` | `ot discard <id>` |
| `ot login` / `logout` / `whoami` | `ot auth login` / `logout` / `whoami` |
| `ot review-llm` | `ot llm-review` |
| `ot upgrade` | `ot setup upgrade` |
| `ot projects list` | `ot list --projects` |
| `ot remote set <url>` | `ot remote add <name> <url>` |
| `TraceStagingEntry.uploaded_at` only | `+ uploaded_to: dict[str, str]` (auto-migrated) |
| `ProjectConfig.remote` + `visibility` | `remotes` + `active_remote` (auto-migrated) |
| `BLOCKED` enum existed but never written | Now written by parse pipeline; `ot add` refuses |

The legacy flat verbs (`ot login`, `ot upgrade`, `ot commit`, `ot trace ...`,
etc.) still work for back-compat in 0.3.0; a future release will remove
them per a clean-break deprecation cycle.

### Tests

+143 new tests covering schema migrations, per-remote tracking, git-parity
remote verbs, BLOCKED wiring, content-targeted redaction, auth group,
completions noun, gh-style help renderer, flat workflow verbs, the
`ot add` approval gate, config set scope semantics, and setup `--project`
flag. Test suite went from 1047 → 1198 passing, 2 pre-existing baseline
failures unchanged.
