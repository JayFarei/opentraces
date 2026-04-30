# Changelog

All notable changes to the opentraces CLI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## MERGED-A — Indexer Reliability

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

## [0.4.0] - 2026-04-26

This release ships **Trace Trails Phase 5**: a VCS-anchored evidence
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
