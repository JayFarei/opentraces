# Changelog

## [0.4.0] - 2026-04-14

### CLI restructure (git-style flat verbs + resource nouns)

The command surface now mirrors `git`'s discipline: workflow verbs are
flat at the root, only true configurable resources (remote, auth,
config, setup, completions) are nouns. Anyone who knows git knows
this CLI in 30 seconds. See `kb/plans/buzzing-dreaming-forest.md` for
the full design rationale.

#### New commands

- `ot add <ids>... | --all` — stage trace(s) for push (mirrors `git add`).
  Variadic. Refuses BLOCKED + REJECTED traces with a pointer to the
  unblocking verbs (`ot redact`, `ot reject`, `ot reset`).
- `ot show <id>` — show one trace (flat, was `ot trace show`).
- `ot list [--projects] [--remote <name>]` — list traces; with
  `--projects`, list every directory that ran `ot init`; with
  `--remote <name>`, filter to traces missing on that remote.
- `ot reject <ids>...` / `ot reset <ids>...` / `ot discard <id>` —
  per-trace curation, flat (was under `ot trace`).
- `ot redact <id> <pattern> [--regex] [--field] [--step]` — content-
  targeted find-and-replace. Replaces the old `--step <n>`-only
  blank-the-whole-step shape (which was an outlier vs pi-share-hf
  and pi-trace-sanitizer). Permanent, no undo.
- `ot llm-review` — Tier-2 LLM semantic review (renamed from
  `review-llm`; the `llm-` prefix keeps the machine-pass nature
  explicit vs the human review handled by `review_policy`).
- `ot remote add/set-url/rename/remove/use/list` — git-parity verbs
  on the remote noun. URL accepts `hf://user/repo` or short form
  `user/repo` (auto-expanded; HF is the default backend).
- `ot auth login/logout/whoami` — group equivalent of the flat
  `ot login`/`logout`/`whoami` (which still work for back-compat).
- `ot completions install | uninstall | [shell]` — cf-style shell
  completions with dynamic delegation. Bare `ot completions` prints
  the script for `$SHELL`. Supports bash, zsh, fish.
- `ot setup upgrade` — `ot upgrade` is now a setup subcommand.
- `ot config set <key> <value> [--append] [--project|--global]` —
  proper key/value setter, replacing the previous append-only-with-
  flags shape. Legacy flags preserved for back-compat.
- `--project` flag added to `setup trufflehog`, `setup review-llm`,
  `setup review-policy` for explicit per-project scope (mirrors
  `git config --local`).

#### Behavior changes

- **`BLOCKED` is now a real status.** The TraceStatus enum value
  existed pre-restructure but was never written by the parse pipeline;
  TruffleHog findings now move traces to BLOCKED with a `block_reason`,
  surfacing in `ot status` and the inbox. `ot add` refuses BLOCKED
  traces, making staging a true approval gate.
- **Per-remote upload tracking.** Switching remotes and running
  `ot push` now replays the full local history to the new remote
  (mirrors `git push <new-remote>`). Driven by a new
  `TraceStagingEntry.uploaded_to: dict[str, str]` field.
- **gh-style help.** `ot --help` is sectioned (CORE / INBOX / PROJECT /
  RESOURCE) with one-liner descriptions, mirroring `gh --help`.

#### Schema migrations (auto, on first command after upgrade)

- `TraceStagingEntry` gains `uploaded_to: dict[str, str]`. Existing
  `status=UPLOADED` traces are backfilled with
  `uploaded_to = {"origin": <existing uploaded_at>}`.
  `STATE_SCHEMA_VERSION` bumped to "2".
- `ProjectConfig` gains `remotes: dict[str, RemoteConfig]` +
  `active_remote: str | None` + `default_visibility: str`. Legacy
  single `remote: str` + `visibility: str` are migrated to
  `remotes = {"origin": RemoteConfig(...)}` and `active_remote =
  "origin"`. Marker version bumped 1 → 2.

#### Migration table

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

The legacy flat verbs (`ot login`, `ot upgrade`, `ot commit`, `ot
trace ...`, etc.) still work for back-compat in 0.4.0; a future
release will remove them per a clean-break deprecation cycle.

### Tests

+143 new tests covering schema migrations, per-remote tracking,
git-parity remote verbs, BLOCKED wiring, content-targeted redaction,
auth group, completions noun, gh-style help renderer, flat workflow
verbs, the `ot add` approval gate, config set scope semantics, and
setup `--project` flag. Test suite went from 1047 → 1198 passing,
2 pre-existing baseline failures unchanged.

## [0.3.0] - 2026-04-12

### Changed
- Major code reorganization: 15 top-level items collapsed to 7 top-level folders.
  - `agents/` + `parsers/` + `installers/` + `enrichment/git/post_commit.py` -> `capture/`
  - `exporters/` + `upload/` -> `publish/`
  - Top-level glue modules (config, paths, state, workflow, inbox, pipeline, processors) -> `core/`
  - Business logic extracted from `clients/` and `cli.py` into `core/review.py` + `core/publish_flow.py`
  - `cli.py` split into `cli/` package
- Deprecated import paths removed. See upgrade guide below.

### Removed (breaking imports)
- `opentraces.agents.*` -> use `opentraces.capture.*`
- `opentraces.parsers.*` -> use `opentraces.capture._base` or `opentraces.quality.parse_gate`
- `opentraces.installers.*` -> use `opentraces.capture.*`
- `opentraces.exporters.*` -> use `opentraces.publish.*`
- `opentraces.upload.*` -> use `opentraces.publish.huggingface.*`
- `opentraces.state`, `opentraces.config`, `opentraces.paths`, `opentraces.workflow`,
  `opentraces.inbox`, `opentraces.pipeline`, `opentraces.processors`
  -> use `opentraces.core.*`
- `opentraces.enrichment.git.post_commit` -> use `opentraces.capture.git.post_commit`
