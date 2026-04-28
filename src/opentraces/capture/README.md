# capture/

The inbound boundary of opentraces. Everything that ingests data *from* an external system — agent session parsers, runtime hooks, and one-off installers that wire us into that system — lives here.

This module collapses the former top-level `agents/`, `parsers/`, and `installers/` trees (plus `enrichment/git/post_commit.py`) into a single place.

## What lives here

- `_base.py` — cross-agent protocols: `SessionParser`, `FormatImporter`, `ParseOutcome`.
- `claude_code/` — Claude Code adapter.
  - `parse.py` — `ClaudeCodeParser` (live session parser).
  - `hooks/` — `on_stop`, `on_compact`, `on_tool_use`. Copied to `~/.claude/hooks/` by `opentraces setup claude-code`.
  - `install.py` — `ClaudeCodeHookInstaller` (HookInstaller protocol adapter).
- `hermes.py` — Hermes (Lambda) file importer: `HermesParser`.
- `git/` — VCS integration.
  - `install.py` — post-commit hook installer (owned-hook + chain semantics).
  - `post_commit.py` — tier-assignment orchestration that runs from the installed hook.

## Registry

`capture/__init__.py` exposes:

- `PARSERS` — live session parsers keyed by agent name (e.g. `claude-code`).
- `IMPORTERS` — file-based importers keyed by format name (e.g. `hermes`).
- `get_parsers()`, `get_importers()`, `resolve_import_format()` — lazy accessors.

Defaults register on first call to keep import cost low.

## Adding a new adapter

The full contributor-facing contract, with worked examples for Tiers 1 to 4 (file importer through Trace Trails capture), lives at [`docs/integration/capture-integration.md`](../../../web/site/docs/docs/integration/capture-integration.md). Quick checklist:

1. Create `capture/<name>/` (or `capture/<name>.py` for a single-file adapter).
2. Implement the relevant protocol from `_base.py`:
   - `SessionParser` for live agent sessions.
   - `FormatImporter` for file-based imports.
   - `HookInstaller` if you wire scripts into the agent's settings.
3. Add hooks under `capture/<name>/hooks/` if the external tool supports them. For Trace Trails participation, hooks must call `core.trails.write_worktree_tree(cwd)` synchronously at tool boundaries and emit `opentraces_hook` lines into the transcript with `metadata["hook_pre_tool_use"]` / `["hook_post_tool_use"]` keys.
4. Register in `_register_defaults()` in `capture/__init__.py`. Generalize the hardcoded Claude-Code-only call sites listed in the integration spec under "Known coupling" before shipping a second live agent.
5. Add tests under `tests/capture/test_parser_<name>.py` and any hook/install tests, following the recipes in the integration spec's "Test pattern catalog."

## See also

- [`docs/integration/capture-integration.md`](../../../web/site/docs/docs/integration/capture-integration.md) — full contributor spec with worked Codex example.
- Root `CLAUDE.md` — full project structure and Trace Trails decisions.
- `src/opentraces/publish/README.md` — the outbound boundary (symmetric to this one).
