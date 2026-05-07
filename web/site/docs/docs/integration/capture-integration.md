# Capture Integration Spec

This is the contract for adding support for a new agent (Codex, Cursor, Aider, OpenAI's coding CLI, a custom in-house agent) to opentraces. Follow this spec end-to-end and your agent's traces flow through the same parse, redaction, review, push, and Trace Trails pipeline that Claude Code uses today.

The spec is layered: each tier adds capability. You can ship Tier 1 in an afternoon and add Tier 4 once the basics work.

## What "capture integration" means

The opentraces pipeline is symmetric: `capture/` is the inbound boundary (turn external systems into `TraceRecord`), `publish/` is the outbound boundary (turn `TraceRecord` into HuggingFace shards or ATIF). Everything between, security scanning, redaction, review, attribution, Trace Trails, is agent-agnostic and reused.

A "capture integration" provides one or more of:

- A **session parser** that reads the agent's on-disk transcripts and yields `TraceRecord`.
- A **format importer** that reads a static dataset (JSONL, ShareGPT) and maps rows to `TraceRecord`.
- **Runtime hooks** the agent invokes during a session, which write boundary state into the transcript.
- A **hook installer** that wires those hooks into the agent's settings file idempotently.
- **Trace Trails participation**: the hooks call `write_worktree_tree()` at tool boundaries so the substrate can build verifiable patch lineage.

## The four tiers

| Tier | What you ship | When you stop here |
|------|---------------|--------------------|
| 1. File importer | `FormatImporter` for an existing dataset format | The agent does not run live, you only ingest archives |
| 2. Live session parser | `SessionParser` over the agent's on-disk session files | The agent runs but exposes no hook system |
| 3. Hooks + installer | Hook scripts that record git state on Stop, plus the `HookInstaller` that registers them | The agent has hooks but no per-tool-call file-edit metadata |
| 4. Trace Trails capture | Hooks emit pre-tool and post-tool worktree tree IDs via `write_worktree_tree()`, with a stable `tool_call_id` linking pre and post | Full parity with Claude Code's plan-54 integration |

Hermes is a Tier 1 example. Claude Code is a Tier 4 example. Pick the highest tier the external system will support and target that.

## The protocols

All three protocols live in `src/opentraces/capture/_base.py`. They are `@runtime_checkable` `Protocol` classes, no inheritance is required, structural typing is enough.

### `SessionParser`

For agents whose live session state lives in files on disk.

```python
@runtime_checkable
class SessionParser(Protocol):
    agent_name: str

    def discover_sessions(self, projects_path: Path) -> Iterator[Path]: ...
    def parse_session(self, session_path: Path, byte_offset: int = 0) -> TraceRecord | None: ...
```

- `agent_name` (class attribute): stable string used as the registry key. Use kebab-case (`claude-code`, `codex-cli`).
- `discover_sessions(projects_path)`: yield paths to every session file for this agent under a project root. The watcher passes the project's `cwd` as `projects_path` and you map it to the agent's storage convention.
- `parse_session(session_path, byte_offset)`: read one file and return a fully-populated `TraceRecord`, or `None` if the session does not meet the quality threshold (use `quality.engine.meets_quality_threshold(record)`). The `byte_offset` argument supports incremental re-reads after partial parses, parsers without resume support may ignore it but must accept it.

The parser must populate `self.step_anchors: dict[int, dict]` during `parse_session` if you want resume support. Each entry maps `step_index` to whatever locator the agent uses to seek into the transcript (file relpath, line number, internal entry id). Without anchors, `opentraces trace resume --at-step` will not work for the agent.

### Skills and command invocations

Harnesses often record user-facing commands and skill execution as several adjacent transcript events: a slash-command wrapper, the user's command arguments, an injected skill body or system prompt, and then the real tool calls that follow. Parser authors must keep those surfaces distinct.

- Treat explicit skill tool calls, or harness-specific high-confidence command wrappers, as structured invocation evidence. Store that evidence under `TraceRecord.metadata["skill_invocations"]` with enough raw locator data to debug it later: skill name, command name, command args, timestamp, source event ids or line numbers, and the harness-specific source label.
- Do not treat injected skill body text as a user step, task description, or task intent. If the harness gives a slash command plus arguments, the arguments are the user's intent seed; the injected body is provenance for the command implementation.
- Do not infer skill usage from arbitrary text mentions. A skill invocation needs an explicit tool call (`Skill`, `skill`, or the harness equivalent) or a paired command wrapper and injected skill-body marker, such as Claude Code's `<command-name>...</command-name>` event followed by `Base directory for this skill: ...`.
- Keep built-in harness commands separate from skill invocations. Built-ins such as help, status, reset, or non-skill slash commands may be useful as command metadata, but they must not populate `skill_invocations` or pollute the trace task.
- Preserve the original command/tool surface in metadata. Later query and dataset workflows need to know whether the trajectory came from a slash command, a named skill tool, a shell command, or another harness-specific command family.

This distinction is required for command-attributed datasets: the index can only build reliable `skill_invocation` units when the parser exposes high-confidence command evidence and excludes injected implementation text from the user trajectory.

### `FormatImporter`

For static dataset rows, no live session, no hooks.

```python
@runtime_checkable
class FormatImporter(Protocol):
    format_name: str
    file_extensions: list[str]

    def import_traces(self, input_path: Path, max_records: int = 0) -> list[TraceRecord]: ...
    def map_record(self, row: dict, index: int, source_info: dict | None = None) -> TraceRecord | None: ...
```

- `format_name`: registry key for `opentraces pull --parser <format_name>`.
- `file_extensions`: list of accepted suffixes (`[".jsonl"]`, `[".jsonl", ".json"]`).
- `import_traces`: walk a local file and return all valid records. `max_records=0` means unlimited.
- `map_record`: convert one row dict to a `TraceRecord`, or return `None` to skip. This is also called by the streaming HF importer in `cli/import_hf.py`, so any per-row logic must live here, not in `import_traces`.

`HermesParser` (`src/opentraces/capture/hermes.py`) is a 600-line worked example: ShareGPT row to `TraceRecord`, XML tag-call extraction, outcome inference, no hooks, no live session, no Trace Trails.

### `HookInstaller`

For wiring scripts into an external system idempotently.

```python
@runtime_checkable
class HookInstaller(Protocol):
    installer_name: str

    def plan(self) -> list[dict]: ...
    def install(self) -> HookInstallResult: ...
    def remove(self) -> HookInstallResult: ...
    def status(self) -> dict: ...
```

- `plan()`: return `[{event, source, dest}, ...]` describing every action `install()` will take. Used by `--dry-run` and `opentraces doctor`.
- `install()`: validate the target settings file before writing, then atomically apply all changes. Must be safe to re-run, no duplicate entries, no partial state.
- `remove()`: reverse `install()` cleanly. Must be safe to run on a non-installed system.
- `status()`: machine-readable health for `opentraces doctor`. Conventional keys: `installed`, `agent_dir_exists`, `script_paths`, `settings_path`, `entries_present`, plus anything agent-specific.

Failures must raise `HookInstallError(code, message, hint)` with a user-actionable hint. Never partially write, validate first, then commit.

### `ParseOutcome`

Parsers that produce partial results when they hit recoverable errors should return a `ParseOutcome`, not raise. Empty `errors` means clean. Non-empty `errors` is a hard upload block, the trace lands in `BLOCKED` state and the user must re-trigger.

```python
@dataclass
class ParseOutcome:
    record: object | None = None
    errors: list[str] = field(default_factory=list)
```

## Registration

Adding an agent registers it in three places, all in one file. After this, `opentraces` discovers the agent via the registry, no other module imports your code by name (with the exceptions listed under "Known coupling" below).

### `src/opentraces/capture/__init__.py`

Edit `_register_defaults()`:

```python
def _register_defaults() -> None:
    PARSERS["claude-code"] = ClaudeCodeParser
    PARSERS["codex-cli"] = CodexCliParser              # NEW

    IMPORTERS["hermes"] = HermesParser
    IMPORTERS["my-format"] = MyFormatParser            # NEW (if you add a format)

    HOOK_INSTALLERS["claude-code"] = ClaudeCodeHookInstaller
    HOOK_INSTALLERS["git"] = GitHookInstaller
    HOOK_INSTALLERS["skill"] = SkillInstaller
    HOOK_INSTALLERS["codex-cli"] = CodexCliHookInstaller  # NEW (if Tier 3+)
```

Also add the module to the `REGISTRY` dict at the top of the file, that's what makes the subpackage importable through `opentraces.capture.codex_cli`.

### Skill harness symlinks (optional, only if your agent reads agent-skills from a known dir)

`src/opentraces/capture/skill/install.py`:

```python
HARNESS_DIRS: dict[str, Path] = {
    "claude-code": Path.home() / ".claude" / "skills" / "opentraces",
    "codex-cli": Path.home() / ".codex" / "skills" / "opentraces",   # NEW
}
```

This makes `opentraces setup skill --harness codex-cli` symlink the bundled skill into Codex's skills directory.

### Known coupling that must be generalized for live agents

Three call sites in the existing code path import `ClaudeCodeParser` directly instead of going through the registry. Adding a second live-agent parser requires generalizing them:

| File:line | Current state | What to do |
|-----------|---------------|------------|
| `src/opentraces/core/ingest.py:46` | `from ..capture.claude_code.parse import ClaudeCodeParser` | Look up parser via `get_parsers()[agent_name]` based on the project's `agents` config |
| `src/opentraces/quality/engine.py:575` | `from ..capture.claude_code.parse import ClaudeCodeParser` | Same |
| `src/opentraces/cli/__init__.py:2123-2125` | `if "claude-code" not in agents: return False` (in `_install_capture_hook`) | Iterate over the project's `agents` and dispatch to the matching `HookInstaller` |
| `src/opentraces/cli/__init__.py:4181` | `"agents": ["claude-code"]` (capabilities endpoint) | Return `list(get_parsers().keys())` |
| `src/opentraces/clients/web/server.py:480`, `src/opentraces/cli/trace.py:612` | `from ..capture.claude_code.resume import ...` | Add a `resume` registry, or accept that resume is per-agent until then |

These are pre-existing TODOs from when the codebase was Claude-Code-only. They are not your spec's fault, but you will hit them. Generalizing the registry dispatch is part of "adding the second live agent."

## Tier 1: File importer

Smallest possible integration. You implement `FormatImporter`, register it, and write a test.

```python
# src/opentraces/capture/my_format.py
from pathlib import Path
from opentraces_schema import TraceRecord
from ._base import FormatImporter

class MyFormatParser:
    format_name = "my-format"
    file_extensions = [".jsonl"]

    def import_traces(self, input_path: Path, max_records: int = 0) -> list[TraceRecord]:
        ...

    def map_record(self, row: dict, index: int, source_info: dict | None = None) -> TraceRecord | None:
        ...
```

Register in `_register_defaults()`. Write `tests/capture/test_parser_my_format.py` modeled on `tests/capture/test_parser_hermes.py`. Done.

Users invoke it with `opentraces pull <dataset> --parser my-format` for HF datasets or `opentraces import <file>` for local files.

## Tier 2: Live session parser

Implement `SessionParser`. The watcher will call `discover_sessions(project_cwd)` on every tick, then `parse_session(path)` for each new or grown session file.

Storage discovery is the agent-specific bit. Examples:

| Agent | Session storage | Encoding |
|-------|-----------------|----------|
| Claude Code | `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` | non-alnum chars in `cwd` replaced with `-` |
| Codex CLI | `~/.codex/sessions/<project>/<session>.jsonl` (example) | per the agent's convention |

Quality gate: call `from opentraces.quality.engine import meets_quality_threshold` and return `None` from `parse_session` when it fails. The parser is responsible for filtering, the ingest pipeline trusts you.

`step_anchors`: if you want `opentraces trace resume --at-step <id>` to work, populate `self.step_anchors[step_index] = {<locator dict>}` during parse. Each parser defines its own locator schema, the resume module is per-agent (`capture/<name>/resume.py`).

## Tier 3: Hooks and installer

The agent must support some form of lifecycle callback (a settings entry that runs a shell command on event X). Each hook is a standalone Python script that:

1. Reads a JSON payload from stdin.
2. Appends one line to the active transcript file.
3. Exits 0 always (never propagate failures, hook failure must not break the agent).

The line shape opentraces expects is:

```json
{
  "type": "opentraces_hook",
  "event": "<HookName>",
  "timestamp": "<utc-iso>",
  "data": { ... }
}
```

The parser picks these up during `parse_session` and merges them into `record.metadata`.

### Recommended hook events

| Event | Payload | What it enables |
|-------|---------|-----------------|
| Session start | `{session_id, agent_type}` | Session linkage and provenance |
| Tool call begin | `{tool, tool_call_id, tool_input}` | The "before" boundary |
| Tool call end | `{tool, tool_call_id, file_path?, start_line?, end_line?, content_hash?, capture_status}` | Per-edit attribution metadata |
| Session stop | `{session_id, git: {sha, dirty, files_changed, changed_paths}}` | Final state and trigger for fast-path ingest |
| Compaction | `{messages_removed, messages_kept, summary}` | Boundary marker so the parser knows about context loss |

You do not need every event. Stop alone gives you fast-path ingest. Tool call end gives you attribution.

### Stop hook fast-path ingest

Spawn a detached subprocess from the Stop hook so the new turn lands in the inbox in seconds rather than waiting on the watcher's 5-minute tick:

```python
subprocess.Popen(
    [sys.executable, "-m", "opentraces", "_ingest-session", str(transcript_path), "--project", str(cwd)],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    start_new_session=True,
)
```

Reference: `src/opentraces/capture/claude_code/hooks/on_stop.py:136-154`.

### The installer

Implement `HookInstaller`. Idempotency is the load-bearing requirement. Steps:

1. Resolve the agent's settings file (`~/.codex/settings.json`, `~/.cursor/config.json`, etc.).
2. Validate the file as JSON before touching it. Abort cleanly on parse error.
3. Copy your hook scripts to `~/<agent>/hooks/opentraces_<name>.py`, `chmod +x`.
4. Register them in the settings under whatever schema the agent uses.
5. Prune stale earlier-version opentraces entries (older script paths, `python3` fallbacks).
6. Atomic write: stage to `<settings>.tmp`, then `os.replace`.

Reference implementation: `src/opentraces/capture/claude_code/install.py:36-310`. The `EVENT_SCRIPTS` constant maps event names to script files, the rest is plumbing.

## Tier 4: Trace Trails capture

This is the deepest layer, plan-54 integration. It gives you VCS-anchored patch lineage, git anchor correlation on commit, and `trail explain / search / blame / graph` participation.

### What the agent must provide

A stable `tool_call_id` that links a pre-tool hook to the matching post-tool hook. If the agent does not give you one, you cannot pair pre/post boundaries and the substrate falls back to `mark_skipped("missing_pre_or_post_hook")` for every step.

### What the hook scripts must do

Inside both the pre-tool and post-tool hooks, before exiting, call:

```python
from opentraces.core.trails import write_worktree_tree

trail = {
    "worktree_root": str(cwd),
    "tree_id": write_worktree_tree(cwd),     # {"algo": "sha1", "hex": "<40-char>"}
    "git_head": <current HEAD oid dict>,
}
```

Embed `trail` in the event's `data` dict. `write_worktree_tree` is a synchronous in-process `git add -A && git write-tree` over a scratch GIT_INDEX_FILE, it does not touch the user's index. Reference: `src/opentraces/core/trails/snapshots.py:251-263`.

The synchronous-at-boundary call is load-bearing. If the agent's hook is async or out-of-process, the worktree could change between the tool finishing and the tree SHA being captured, producing `hook_payload_state_mismatch` capture limitations.

### What the parser must do

Index the captured hook events into `record.metadata` under exactly these keys (the substrate reads them by name):

- `metadata["hook_pre_tool_use"]`: dict keyed by `tool_call_id` to `{timestamp, tool, tool_input, trail}`
- `metadata["hook_post_tool_use"]`: dict keyed by `tool_call_id` to `{timestamp, tool, file_path, start_line, end_line, content_hash, confidence, capture_status, limitations, trail}`
- `metadata["hook_stop"]`: list of stop event dicts

If you use other key names, the existing bridge function `emit_step_window_events_from_record()` (in `src/opentraces/core/trails/snapshots.py:487-801`) will not find them. Two options:

1. Normalize your hook output into the expected keys at parse time (recommended, cheaper).
2. Add a parallel `emit_step_window_events_from_<agent>_record()` that reads from your custom keys.

### Capture method vocabulary

The Trail substrate tags every event with a `capture_method` array (multiple methods can stack). Existing values: `hook_pretooluse`, `hook_posttooluse`, `hook_stop`, `post_commit_correlator`, `watcher_backstop`, `manual_attach`, `doctor_probe`, `reference_transaction_observer` (reserved).

The vocabulary is additive. New agents may introduce new tags (`hook_codex_pre_tool_use`) but must coordinate with the schema package, the closed vocabulary lives in `src/opentraces/core/trails/capture_limitations.py`.

### What the substrate gives you for free

Once the parser produces the correct `metadata` shape, ingest automatically:

1. Emits `trace_step_window_opened` and `trace_snapshot_created` (before role) per pre-tool hook.
2. Emits `trace_snapshot_created` (after role) and `trace_step_window_closed` per post-tool hook.
3. Computes `trace_patch_created` events from the pre/post tree diff, one per hunk per file.
4. Emits `trace_session_closed` from the stop hook.
5. The git post-commit hook (agent-agnostic, install separately via `opentraces setup git`) emits `git_anchor_created` events that correlate the commit to the patches.
6. The watcher backstop catches mutations outside any tool call and emits `filesystem_mutation_observed` events.

All of this lands in `refs/opentraces/local/events/v1` as an append-only Git ref, hash-chained, gc-safe. `trail explain`, `trail search`, `trail follow`, `blame`, and `graph` all read from it.

## Watcher integration

The watcher daemon (`src/opentraces/watcher/daemon.py`) is mostly agent-agnostic. It polls per-project, runs an mtime probe over agent session files, and calls `core.ingest.scan_project` on activity.

What is agent-specific: `_claude_jsonl_dir(project_cwd)` (daemon.py:97) is the only Claude-Code-specific path probe. To add a new live agent's storage path:

1. Add a per-agent dir resolver, e.g. `_codex_session_dir(project_cwd)`.
2. Extend `_jsonl_activity_since` to check both directories, or refactor it to iterate over a list returned by `[parser.session_dir(cwd) for parser in get_parsers().values()]` (cleanest, requires adding `session_dir` to `SessionParser`).

What is shared: nothing in `watcher/installer.py` changes per agent, the macOS launchd plist and Linux systemd timer install one shared `ot-watcher` shim that polls all enlisted projects regardless of which agent they use.

## Test coverage requirements

This section is what a contributor needs to ship safely. It has five parts: a compliance matrix (what to test by tier), a free-vs-add table (inherited vs new work), a hardcoded-coupling refactor risk table (where existing tests will silently pass a wrong refactor), apparatus you may need to extend, and the recipe catalog with file:line references.

### Coverage matrix

Each row is a behavior; each column is an integration tier. MUST = required to merge, SHOULD = strongly recommended, N/A = not applicable at that tier.

| Behavior | T1 importer | T2 live parser | T3a hook scripts | T3b installer | T4 trails | Watcher |
|----------|:-:|:-:|:-:|:-:|:-:|:-:|
| Happy-path parse → `TraceRecord` correct shape | MUST | MUST | N/A | N/A | N/A | N/A |
| Returns `None` / exits 0 on malformed input | MUST | MUST | MUST | N/A | N/A | N/A |
| Tool call extraction with correct name/args/id | MUST | MUST | N/A | N/A | N/A | N/A |
| Tool name normalization (known + unknown) | MUST | N/A | N/A | N/A | N/A | N/A |
| Outcome inference | MUST | MUST | N/A | N/A | N/A | N/A |
| Token metrics, all 4 buckets, no double-count | MUST | MUST | N/A | N/A | N/A | N/A |
| Session-id stability under same input | MUST | SHOULD | N/A | N/A | N/A | N/A |
| Registry presence test (`get_parsers()[name]`) | MUST | MUST | N/A | N/A | N/A | N/A |
| Registry presence test (`get_hook_installers()[name]`) | N/A | N/A | N/A | MUST | N/A | N/A |
| `process_imported_trace()` round-trip | MUST | N/A | N/A | N/A | N/A | N/A |
| `discover_sessions()` recurses correctly, excludes nested wrong files | N/A | MUST | N/A | N/A | N/A | N/A |
| `step_anchors` populated with locator dict | N/A | MUST | N/A | N/A | N/A | N/A |
| Hook lines indexed into `metadata[hook_pre/post/stop]` | N/A | MUST | N/A | N/A | N/A | N/A |
| `ParseOutcome` BLOCKED → excluded from upload | N/A | MUST | N/A | N/A | N/A | N/A |
| `content_hash` in serialized output | N/A | MUST | N/A | N/A | N/A | N/A |
| Quality gate: trivial session → `None` | N/A | MUST | N/A | N/A | N/A | N/A |
| Subagent inlining + `parent_step` integrity | N/A | SHOULD | N/A | N/A | N/A | N/A |
| Multi-fragment turn coalescing | N/A | SHOULD | N/A | N/A | N/A | N/A |
| Incremental parse (`byte_offset`) preserves first new line | N/A | SHOULD | N/A | N/A | N/A | N/A |
| Appends exactly one valid JSON line | N/A | N/A | MUST | N/A | N/A | N/A |
| Exits 0 on missing fields, malformed JSON, git failure | N/A | N/A | MUST | N/A | N/A | N/A |
| Append-only: pre-existing transcript content preserved | N/A | N/A | MUST | N/A | N/A | N/A |
| `trail.tree_id` matches independent `write_worktree_tree()` call | N/A | N/A | MUST | N/A | MUST | N/A |
| `capture_status` / `limitations` tagging for non-edit tools | N/A | N/A | MUST | N/A | N/A | N/A |
| Detached subprocess spawn for fast-path ingest, failure swallowed | N/A | N/A | MUST | N/A | N/A | N/A |
| Confidence under multi-match disambiguation | N/A | N/A | SHOULD | N/A | N/A | N/A |
| Latency budget per hook (e.g. <50ms on 500-line file) | N/A | N/A | SHOULD | N/A | N/A | N/A |
| Scripts written, executable bit set | N/A | N/A | N/A | MUST | N/A | N/A |
| Settings file updated with correct schema envelope | N/A | N/A | N/A | MUST | N/A | N/A |
| Idempotency: second `install()` does not duplicate | N/A | N/A | N/A | MUST | N/A | N/A |
| `remove()` reverses `install()`, `status()` reflects state | N/A | N/A | N/A | MUST | N/A | N/A |
| Corrupt settings file aborts, original untouched | N/A | N/A | N/A | MUST | N/A | N/A |
| Stale interpreter (`python3` hardcoded) replaced on re-install | N/A | N/A | N/A | MUST | N/A | N/A |
| Path quoting (paths with spaces) | N/A | N/A | N/A | MUST | N/A | N/A |
| `sys.executable` used, not hardcoded `python3` | N/A | N/A | N/A | MUST | N/A | N/A |
| Pre-existing hooks preserved (chain semantics) | N/A | N/A | N/A | SHOULD | N/A | N/A |
| `emit_step_window_events_from_record()` produces expected events from synthetic record | N/A | N/A | N/A | N/A | MUST | N/A |
| `tool_call_id` pairing across pre/post hooks | N/A | N/A | N/A | N/A | MUST | N/A |
| `mark_skipped("missing_pre_or_post_hook")` negative case | N/A | N/A | N/A | N/A | MUST | N/A |
| `capture_method` array contains expected hook tier tags | N/A | N/A | N/A | N/A | MUST | N/A |
| Phase-7 UAT: `trail explain`, `trail search`, `blame`, `graph` work via `append_exact_patch_trail()` with your `writer` and `capture_method` | N/A | N/A | N/A | N/A | SHOULD | N/A |
| Per-agent session-dir resolver returns correct path | N/A | N/A | N/A | N/A | N/A | MUST |
| Active tick → `scan_project()` invoked; quiet tick → not invoked | N/A | N/A | N/A | N/A | N/A | MUST |
| Sweep failure swallowed, does not break backfill | N/A | N/A | N/A | N/A | N/A | SHOULD |

This is the bar. A new agent at Tier 4 with full Trace Trails participation needs everything in T1-or-T2, T3a, T3b, T4, and Watcher columns marked MUST.

### What you inherit, what you must add

The substrate, security pipeline, and quality engine all operate on the `TraceRecord` schema, not on agent-specific objects. Anything that runs after the parser is yours for free.

| Coverage area | FREE (inherited) | MUST ADD (new work) |
|---------------|------------------|---------------------|
| Trail substrate invariants | Linear fast-forward, hash chain, GC-safety, CAS retry, anchor reconciliation, rebuild idempotence, watcher reconciliation, survival states. All proved against synthetic events in `tests/core/test_trail_*.py` | Nothing |
| Phase-7 lineage consumers | `trail search`, `blame`, `graph` participate via `append_exact_patch_trail()` with your `writer` + `capture_method`; the existing fixtures cover commit-by-commit, line-by-line, and trace-by-trace lookups | One Phase-7 fixture using `append_exact_patch_trail()` with your agent's tags, asserting the same lineage-consumer agreement as `tests/cli/test_trail_search_phase7.py` |
| Security pipeline | `scan_trace_record`, `classify_trace_record`, `two_pass_scan`, `apply_redactions`, TruffleHog, PII, LLM review. All in `tests/security/*` operate on synthetic `TraceRecord` inputs | Nothing, unless you add a novel field type not exercised by any `TestScanTraceRecord` test |
| Persona quality rubrics | All 34 deterministic checks (training/RL/analytics/domain) tested against synthetic records in `tests/quality/test_persona_rubrics.py` | Nothing |
| Quality gate (`meets_quality_threshold`) | The gate logic itself is schema-driven | Your parser must call `meets_quality_threshold(record)` before returning, and test: trivial session rejected, empty-tool-calls rejected, minimum-valid passes |
| Schema stability | Round-trip + required-field-creep guards in `tests/integration/test_trace_record_stability.py` | Contribute one sample `TraceRecord` from your agent to `tests/fixtures/trace_record_stability/v02_sample.jsonl` |
| Registry consistency | None: this is a **gap** today. There is no test that two agents register without collision, no idempotency test for `_register_defaults`, no uniqueness assertion. | Add a `tests/capture/test_registry.py` covering: agent_name uniqueness, two-parser dispatch, `_register_defaults` idempotency. The contributor adding the second live agent backfills this debt |
| CLI: `init --agent <name>` | `SUPPORTED_AGENTS` auto-derives from the registry, no code change needed | One CLI integration test asserting `init --agent <yours>` writes config with `agents` containing your name |
| CLI: `setup <agent>` | Pattern from `tests/cli/test_cli_commands.py:838-1040` is reusable | `setup <yours> --help`, `setup <yours> --dry-run`, full install, three CliRunner tests |
| CLI: `capabilities` endpoint | None: no test currently asserts capabilities content | Add a test asserting your agent appears in `capabilities.agents`. The hardcoded list at `cli/__init__.py:4181` must be derived from `get_parsers()` first |
| Dogfood / harness E2E | `process_trace`, security, classifier, and persona scoring are all reusable as building blocks | A parallel `tests/e2e/test_e2e_dogfood_<agent>.py` with its own `OPENTRACES_TEST_<AGENT>_PROJECT_DIR` env var, your parser hardcoded. Do not merge into the existing Claude Code dogfood, they share nothing useful |

### Hardcoded coupling: refactor risk

The "Known coupling" section above lists five sites where `ClaudeCodeParser` is imported by name and must be generalized through the registry before a second live agent works. **Some of these refactors will pass silently with the current test suite.** Treat that as a real risk, not a footnote.

| Coupling site | Existing test that would catch a wrong refactor | Risk |
|---------------|--------------------------------------------------|------|
| `core/ingest.py:46` | `tests/core/test_ingest.py`, calls `ingest_one_session` with synthetic Claude Code JSONL. Catches dispatch-failure (returns `None`, raises) but not silent-wrong-parser (test fixture is parseable by either) | Medium |
| `quality/engine.py:575` (`assess_multi_project`) | None. `tests/quality/test_multi_project_eval.py` uses synthetic sessions and never reaches this dispatch path | **High, passes silently** |
| `cli/__init__.py:1049` | None. Inside `_capture_sessions_into_project` dead-code path (comment at 3599 confirms) | Low, unreachable |
| `cli/__init__.py:3602` | None. Legacy `parse` CLI command, no CliRunner tests for this verb | **High, passes silently** |
| `cli/__init__.py:4181` (capabilities hardcoded list) | None | **High, passes silently** |

**Required tests to add before refactoring** (without these, the second live agent's PR is unsafe to merge):

1. A `tests/quality/test_multi_project_dispatch.py` test that calls `assess_multi_project` with two agents' sessions present and asserts each is parsed by the correct parser. Otherwise the registry lookup at this site is unverified.
2. A CliRunner test invoking `opentraces parse <session>` against a non-Claude-Code session, asserting it dispatches to the right parser.
3. A CliRunner test asserting `opentraces capabilities` returns `agents` derived from `get_parsers().keys()`, not a static list.

### Apparatus you may need to extend

Most of the test apparatus is agent-agnostic. The HOME redirect at `tests/conftest.py:36` covers `~/.codex`, `~/.cursor`, etc. transitively because the autouse fixture monkeypatches `HOME`, and any code that does `Path.home() / ".codex"` resolves into the tmp HOME on every test.

What you must extend:

- **Module-level path constants**: if your agent module computes a constant from `Path.home()` at import time (e.g. `CODEX_DIR = Path.home() / ".codex"` at module scope, not inside a function), the conftest's import-order hack at lines 25-33 will cause `monkeypatch` teardown to "restore" the wrong value into the next test. Add an eager import of your module to `tests/conftest.py` alongside `_paths` and `_config`, and add `monkeypatch.setattr` calls for those constants inside `_isolate_opentraces_global_state`. Compute paths at call time when possible to avoid this entirely.
- **E2E env var**: add `OPENTRACES_TEST_<AGENT>_PROJECT_DIR` for your dogfood test, parallel to `OPENTRACES_TEST_PROJECT_DIR`. Do **not** parametrize the existing one, that would force every developer running the Claude tests to also have a project for your agent.
- **Real-REPL gate**: if you contribute scenario tests that drive a live agent REPL (cost, slow), reuse the `real_repl` pytest marker and the `OT_REAL_REPL=1` opt-in from `tests/integration/conftest.py`, or add a parallel `OT_REAL_<AGENT>=1` guard there. Reusing the existing one is simpler.
- **Schema stability fixture**: add one serialized `TraceRecord` line from your parser to `tests/fixtures/trace_record_stability/v02_sample.jsonl`. The stability test will then guard backward compatibility for your agent's record shape too.
- **Perf scenarios** (optional): if your parser is on a hot path (watcher tick, scan), add `tests/perf/scenarios/<agent>-parse-smoke.toml` and register it in `tests/perf/journeys.toml`. `test_journey_coverage.py:73` will fail collection if a scenario file is unmapped.
- **CI**: nothing automatic to update. CI runs a single `pytest tests/perf --perf-lane smoke` invocation in `.github/workflows/perf.yml` and the main test suite via `publish.yml`. New scenarios are picked up automatically. Only add a CI matrix entry if you want CI to actually exercise your dogfood test, which requires the env var as a CI secret.

### Test pattern recipes

Every tier maps to a recipe under `tests/`. Reuse the existing fixtures, do not invent new ones.

#### Tier 1: format importer

Pattern: helper that builds row dicts, instantiate parser, assert on `TraceRecord` fields. No file I/O, no subprocess, pure unit.

Reference: `tests/capture/test_parser_hermes.py` (40+ test functions across `TestMapRecord`, `TestParseToolCalls`, `TestParseToolResponses`, `TestPipelineIntegration`, `TestRegressions`).

Registry test recipe (5-liner, copy verbatim with your name swapped):

```python
def test_importers_registry(self):
    from opentraces.capture import get_importers
    importers = get_importers()
    assert "your-format" in importers
    instance = importers["your-format"]()
    assert instance.format_name == "your-format"
```

#### Tier 2: session parser

Pattern: helper builds list-of-dicts representing the agent's transcript, write to `tmp_path`, instantiate parser, call `parse_session(file)`, assert on `record.steps`, `record.metrics`, `record.metadata`.

Reference: `tests/capture/test_parser_claude_code.py` (`_make_minimal_session()`, `_write_session()`), plus the focused files `test_parse_away_summary.py`, `test_parse_compact_summary.py`, `test_parse_error_blocking.py`, `test_parser_fragment_merge.py`, `test_token_accounting.py`. Cover at minimum: clean turn, multi-step turn, tool call with observation, malformed line skipped, quality threshold rejection, hook lines indexed into metadata, `step_anchors` populated, `ParseOutcome` BLOCKED on errors.

#### Tier 3a: hook scripts

If hooks are Python: monkeypatch `sys.stdin` with a JSON payload, call `main()` in-process, read transcript with `json.loads`. Reference: `tests/capture/test_hooks.py:21` (`_invoke_hook` helper), `test_on_pre_tool_use_hook.py`, `test_on_tool_use_hook.py`.

If hooks are non-Python (Node, Go, shell): use the subprocess pattern. Reference: `tests/capture/test_hook_ingest_spawn.py:29-37`.

```python
def _run_node_hook(payload: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(HOOK_PATH)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=10,
        env={**os.environ, "HOME": str(tmp_path)},
    )
```

For verifying a detached subprocess spawn (the fast-path ingest), use `importlib.util.spec_from_file_location` + monkeypatched `subprocess.Popen` from `tests/capture/test_hook_ingest_spawn.py:47-108`. For non-Python hooks, set an env var like `OPENTRACES_DRY_RUN_INGEST=1` to suppress the spawn and assert via stderr instead.

#### Tier 3b: hook installer

Pattern: `CliRunner()` with `--hooks-dir <tmp>` and `--settings-file <tmp>` flags so the installer never touches the user's real settings. Assert: scripts exist and are executable, settings file is valid JSON with the expected entries, second invocation does not duplicate, corrupt settings aborts cleanly, paths with spaces shell-quote correctly, `sys.executable` is used.

References: `tests/cli/test_cli_commands.py:838-1040` (`TestHooksCommands`), `tests/capture/test_installers_git_hook.py`.

#### Tier 4: Trace Trails event capture

Pattern: real git repo via subprocess, emit synthesized hook lines into a JSONL, parse with your `SessionParser`, assert that `record.metadata["hook_pre_tool_use"]` and `["hook_post_tool_use"]` are populated with valid `tree_id` blobs. Then call `emit_step_window_events_from_record` directly and verify with `read_events()` that `trace_step_window_opened`, `trace_snapshot_created`, `trace_step_window_closed`, and `trace_patch_created` events landed in `refs/opentraces/local/events/v1`.

Negative case: emit a `TraceRecord` with one tool call having only the pre-hook (or only the post-hook) and assert `StepTrailEmissionResult.skipped_tool_calls == 1` with `mark_skipped("missing_pre_or_post_hook")`.

References: `tests/capture/test_on_pre_tool_use_hook.py`, `tests/capture/test_on_tool_use_hook.py`, `tests/core/test_trail_event_log.py`. The Phase-7 UAT participation pattern lives in `tests/cli/test_trail_search_phase7.py:54` (`_append_anchored_patch`); copy that with your `writer` and `capture_method` to inherit the lineage-consumer test coverage.

#### Watcher

Agent-agnostic. Real git repo, `.opentraces.json` marker, call `_wd.run_once(project_path)`. The sweep test (`tests/capture/test_watcher_sweep.py`) monkeypatches `_wd.scan_project` so you do not need to wire your real parser through the daemon, just verify the spy is called on active ticks. If you add a `_<agent>_session_dir()` resolver, replace or extend `test_jsonl_activity_probe_recurses_into_nested_subagent_files` (`tests/capture/test_watcher_daemon.py`) which currently hardcodes Claude's `main-session/subagents/` layout.

### Shared fixtures to reuse, not reinvent

| Fixture / helper | Where | Use it for |
|------------------|-------|------------|
| `_isolate_opentraces_global_state` (autouse) | `tests/conftest.py:36` | Redirects HOME and `~/.opentraces` into `tmp_path`. Covers `~/.codex`, `~/.cursor`, etc. transitively if your parser resolves paths from HOME at call time |
| `_init_repo(tmp_path)` | many test files | Standard 5-command git init pattern |
| `_invoke_hook(main, payload, monkeypatch)` | `tests/capture/test_hooks.py:21` | Patches stdin, calls Python hook `main()` in-process |
| `_run_hook_with_payload(payload)` | `tests/capture/test_hook_ingest_spawn.py:29` | Subprocess invocation pattern, copy and adapt for non-Python hooks |
| `_append_anchored_patch(tmp_path)` | `tests/cli/test_trail_search_phase7.py:54` | One-call setup for Phase-7 UAT participation: writes a file, commits, calls `append_exact_patch_trail()`, returns the anchor |
| `CliRunner()` from `click.testing` | CLI tests | Run CLI commands without spawning subprocesses |
| `tests/fixtures/watcher/*.expected` | golden files | Watcher install renderers, only relevant if you change the daemon shim |
| `tests/fixtures/trace_record_stability/v02_sample.jsonl` | sample records | Add one line from your agent here to gain round-trip stability coverage |
| `OT_REAL_REPL=1` env var | `tests/integration/conftest.py:19-31` | Opt-in gate for live REPL scenarios. Mark your tests with `@pytest.mark.real_repl` to inherit the same skip behavior |

## Worked example: adding Codex CLI

Concrete walkthrough so you can map the abstract spec to a real change. Codex CLI (hypothetical) stores sessions at `~/.codex/sessions/<project>/<session>.jsonl` and supports lifecycle hooks via `~/.codex/config.toml`.

1. **Create the package**: `src/opentraces/capture/codex_cli/{__init__.py, parse.py, install.py, hooks/{on_session_start.py, on_tool_call_begin.py, on_tool_call_end.py, on_session_end.py}}`.

2. **Implement `CodexCliParser`** in `parse.py` with `agent_name = "codex-cli"`, `discover_sessions` walking `~/.codex/sessions/<encoded-cwd>/*.jsonl`, `parse_session` doing the JSONL-to-`TraceRecord` mapping. If Codex hooks emit `opentraces_hook` lines, index them into `metadata["hook_pre_tool_use"]` and friends so plan-54 just works.

3. **Implement the four hook scripts**. Each one reads JSON from stdin, computes the trail blob (`write_worktree_tree(cwd)`), appends one `opentraces_hook` line to the transcript, and exits 0. The Stop hook also fire-and-forgets `python -m opentraces _ingest-session <transcript> --project <cwd>`.

4. **Implement `CodexCliHookInstaller`** in `install.py` with `installer_name = "codex-cli"`, copying the four scripts to `~/.codex/hooks/opentraces_*.py` and registering them in `~/.codex/config.toml` under whatever Codex's hooks schema is. Validate-before-write, atomic-replace, idempotent.

5. **Register** in `src/opentraces/capture/__init__.py` `_register_defaults()`:
   ```python
   PARSERS["codex-cli"] = CodexCliParser
   HOOK_INSTALLERS["codex-cli"] = CodexCliHookInstaller
   ```

6. **Generalize the hardcoded Claude Code references** listed under "Known coupling" above. This is unavoidable for the second live agent.

7. **Wire the watcher** by adding `_codex_session_dir(project_cwd)` and extending `_jsonl_activity_since` in `watcher/daemon.py` to check both Claude and Codex directories.

8. **Tests** (consult the coverage matrix above for the full bar). At minimum:
   - `tests/capture/test_parser_codex_cli.py` (Tier 2 pattern, including the registry-presence smoke test)
   - `tests/capture/test_codex_hooks.py` (Tier 3a pattern; subprocess pattern if hooks are non-Python)
   - `tests/cli/test_codex_installer.py` (Tier 3b pattern, including `get_hook_installers()` registry test)
   - `tests/capture/test_codex_trail_capture.py` if shipping Tier 4 (parser indexes hook metadata, `emit_step_window_events_from_record` produces expected events, missing-pre-or-post negative case)
   - `tests/cli/test_codex_phase7_uat.py` adapting `_append_anchored_patch` with your `writer` and `capture_method` to inherit lineage-consumer coverage
   - `tests/cli/test_codex_cli_surface.py` (`init --agent codex-cli`, `setup codex-cli` happy path, `capabilities` lists codex-cli)
   - `tests/e2e/test_e2e_dogfood_codex.py` with its own `OPENTRACES_TEST_CODEX_PROJECT_DIR` env-var skip guard
   - One sample `TraceRecord` line appended to `tests/fixtures/trace_record_stability/v02_sample.jsonl`
   - **Coupling refactor tests** (mandatory before refactoring the hardcoded `ClaudeCodeParser` imports): `tests/quality/test_multi_project_dispatch.py`, a CliRunner test for `opentraces parse` against a non-Claude session, and a CliRunner test asserting `capabilities.agents` is registry-derived
   - **Registry consistency tests** (one-time backfill before second live agent lands): `tests/capture/test_registry.py` with agent-name uniqueness, two-parser dispatch, `_register_defaults` idempotency

9. **Docs**: add a row to `docs/cli/supported-agents.md`, update `src/opentraces/capture/README.md`, update `CLAUDE.md` Stack section if needed. The `docs-update` skill catches the rest.

10. **CLI surface**: `opentraces init --agent codex-cli`, `opentraces setup codex-cli`, `opentraces watcher` will pick the new agent up automatically once registered.

## See also

- `src/opentraces/capture/README.md`: source-tree reference, code-side authority for what lives where.
- `src/opentraces/capture/_base.py`: protocol definitions, the literal contract.
- `src/opentraces/capture/claude_code/`: the canonical Tier-4 reference implementation.
- `src/opentraces/capture/hermes.py`: the canonical Tier-1 reference implementation.
- [Supported Agents](../cli/supported-agents.md): user-facing agent list, gets updated when this spec is satisfied for a new agent.
- [CLAUDE.md](https://github.com/JayFarei/opentraces/blob/main/CLAUDE.md): top-level project structure and key decisions, including the Trace Trails substrate description.
