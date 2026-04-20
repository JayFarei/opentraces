# Changelog

All notable changes to the opentraces CLI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
