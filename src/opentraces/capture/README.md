# capture/

The inbound boundary of opentraces. Everything that ingests data *from* an external system — agent session parsers, runtime hooks, and one-off installers that wire us into that system — lives here.

This module collapses the former top-level `agents/`, `parsers/`, and `installers/` trees (plus `enrichment/git/post_commit.py`) into a single place.

## What lives here

- `_base.py` — cross-agent protocols: `SessionParser`, `ProjectSessionDiscoverer`, `SessionPathIdentifier`, `AgentResumer`, `FormatImporter`, `HookInstaller`, and `ParseOutcome`.
- `tool_boundary.py` — adapter-facing Trace Trails worktree observation at tool lifecycle boundaries. Agent hooks call this interface; `fs_watcher/` owns only path/blob observation.
- `claude_code/` — Claude Code adapter.
  - `parse.py` — `ClaudeCodeParser` (live session parser).
  - `hooks/` — `on_stop`, `on_compact`, `on_tool_use`. Copied to `~/.claude/hooks/` by `opentraces setup claude-code`.
  - `install.py` — `ClaudeCodeHookInstaller` (HookInstaller protocol adapter).
- `codex_cli/` — Codex CLI adapter.
  - `parse.py` / `sessions.py` — dated rollout JSONL discovery and `CodexCliParser`.
  - `hooks/` — Codex lifecycle hook commands. Copied to `~/.codex/hooks/opentraces/` by `opentraces setup codex-cli`; write sidecars under `.opentraces/codex-cli/hooks/`.
  - `install.py` — `CodexCliHookInstaller`, which registers hooks in `~/.codex/hooks.json`.
  - `context_tree_capture.py`, `resume.py` — Context Tree reconstruction and native Codex resume handoff.
- `pi/` — Pi adapter and Python bridge.
  - `parse.py` / `sessions.py` — native `~/.pi/agent/sessions/--<cwd>--/*.jsonl` discovery and `PiSessionParser`.
  - `bridge.py` — validates extension sidecars under `.opentraces/pi/events/`, enforces raw-provider-body default-off, enriches Trail boundary metadata, and triggers fail-open ingest.
  - `install.py` — `PiHookInstaller`, which verifies/repairs `opentraces-pi` package entries in Pi settings.
  - `context_tree_capture.py`, `resume.py` — provider-context-backed Context Tree projection and native `pi --session` handoff.
- `hermes.py` — Hermes (Lambda) file importer: `HermesParser`.
- `git/` — VCS integration.
  - `install.py` — post-commit hook installer (owned-hook + chain semantics).
  - `post_commit.py` — tier-assignment orchestration that runs from the installed hook.

## Registry

`capture/__init__.py` exposes:

- `PARSERS` — live session parsers keyed by agent name (e.g. `claude-code`, `codex-cli`, `pi`).
- `IMPORTERS` — file-based importers keyed by format name (e.g. `hermes`).
- `HOOK_INSTALLERS` — setup installers keyed by integration name (including `claude-code`, `codex-cli`, `pi`, `git`, `skill`).
- `RESUMERS` — native resume adapters keyed by agent name.
- `get_parsers()`, `get_importers()`, `get_hook_installers()`, `get_resumers()`, `resolve_import_format()` — lazy accessors.

Defaults register on first call to keep import cost low.

## Adding a new adapter

The full contributor-facing contract, with worked examples for Tiers 1 to 4 (file importer through Trace Trails capture), lives at [`docs/integration/capture-integration.md`](../../../web/site/docs/docs/integration/capture-integration.md). Quick checklist:

1. Create `capture/<name>/` (or `capture/<name>.py` for a single-file adapter).
2. Implement the relevant protocol from `_base.py`:
   - `SessionParser` for live agent sessions.
   - `FormatImporter` for file-based imports.
   - `HookInstaller` if you wire scripts, package resources, or settings entries into the agent.
   - `AgentResumer` if `trace get --resume` can hand back to the native runtime.
3. Add hooks under `capture/<name>/hooks/` if the external tool supports them. For Trace Trails participation, hooks must call `core.trails.write_worktree_tree(cwd)` synchronously at tool boundaries, call `capture.tool_boundary.observe_tool_boundary(...)` for mutating tools, and emit `opentraces_hook` lines into the transcript with `metadata["hook_pre_tool_use"]` / `["hook_post_tool_use"]` keys. If the agent uses non-Claude tool names, pass `may_mutate=True` after applying the adapter's own tool policy instead of modifying `fs_watcher/`.
4. Register in `_register_defaults()` in `capture/__init__.py`. Check the integration spec's "Known coupling" section before advertising a new agent; remaining narrow surfaces must either be generalized or documented as unsupported for that harness.
5. Add tests under `tests/capture/test_parser_<name>.py` and any hook/install tests, following the recipes in the integration spec's "Test pattern catalog."

Parser specs for new harnesses must also define how command and skill surfaces are separated. A parser should preserve explicit skill or slash-command invocation evidence in `TraceRecord.metadata["skill_invocations"]`, keep built-in commands out of that list, and avoid turning injected skill bodies into user steps or task intent. See the integration spec's "Skills and command invocations" section before adding a new live parser.

## See also

- [`docs/integration/capture-integration.md`](../../../web/site/docs/docs/integration/capture-integration.md) — full contributor spec with the Codex CLI reference implementation.
- Root `CLAUDE.md` — full project structure and Trace Trails decisions.
- `src/opentraces/publish/README.md` — the outbound boundary (symmetric to this one).
