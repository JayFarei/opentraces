# Agent Setup

opentraces is designed to be usable from both a shell and another coding agent.

## Project-Local Setup

Inside a repo, the normal path is:

```bash
opentraces auth login
opentraces init --agent claude-code
opentraces init --agent codex-cli
```

`init` writes `.opentraces.json`, registers the repo in the global config, and installs the per-agent capture hook. Dataset remotes and review policy are configured separately (`opentraces dataset remote ...`, `opentraces config set review_policy review --project`).

## Codex CLI Setup

Install and authenticate Codex CLI itself before wiring opentraces into it.
opentraces does not install Codex, does not manage Codex auth, and does not
cover Codex Desktop.

Machine setup:

```bash
opentraces setup codex-cli
```

Project enrollment:

```bash
cd /path/to/repo
opentraces init --agent codex-cli
```

`setup codex-cli` copies hook entrypoints to
`~/.codex/hooks/opentraces/` and registers native Codex hook commands in
`~/.codex/hooks.json`. Each future Codex CLI session in an enrolled repo can
then write project-local sidecar JSONL under
`.opentraces/codex-cli/hooks/<session-id>.jsonl`; the Stop hook triggers a
bounded ingest pass through the Codex parser.

The install is idempotent and preserves unrelated Codex hooks. Useful flags:

```bash
opentraces setup codex-cli --dry-run
opentraces setup codex-cli --remove
opentraces setup codex-cli --hooks-file ~/.codex/hooks.json
opentraces setup codex-cli --hooks-dir ~/.codex/hooks/opentraces
```

There is no `setup codex-cli --status` flag. Use `opentraces doctor` for
installation health and `opentraces capabilities --json` to confirm that
`codex-cli` is registered.

If `~/.codex/hooks.json` is malformed, setup exits with a `CORRUPT_HOOKS`
installation error instead of partially rewriting the file. Fix or remove that
file, then rerun `opentraces setup codex-cli`.

Codex hooks are observational. They record lifecycle, permission-request, tool
boundary, compaction, git, and ingest signals, but they should not approve,
deny, or mutate Codex permission prompts. Hook failures are swallowed so capture
does not block the agent session.

`opentraces init --agent codex-cli` connects the current repo for future Codex
sessions. `--import-existing` is currently a Claude Code import path; do not
expect it to backfill old Codex CLI sessions. Existing Codex rollout files are
handled only by supported Codex capture/ingest paths when they carry enough
project metadata.

Codex native resume is available through `opentraces trace get <trace> --resume`
or the hidden compatibility alias `opentraces trail resume <trace>`. Snapshot
forking with `--at-step` remains Claude Code only. Context continuation packets
are produced separately with `opentraces ctx resume <context-node-id> --json`.

## Claude Code And Codex CLI

Claude Code and Codex CLI are live-capture adapters.

For a full setup:

```bash
opentraces setup claude-code
opentraces setup codex-cli
opentraces setup git
opentraces setup skill
opentraces setup bucket
opentraces setup capture-otlp
opentraces setup watcher install
```

What each integration does:

- `setup claude-code` installs the `PreToolUse`, `PostToolUse`, `Stop`, and `PostCompact` hooks in `~/.claude/settings.json`
- `setup codex-cli` installs native Codex CLI hook commands in `~/.codex/hooks.json` and copies hook scripts to `~/.codex/hooks/opentraces/`
- `setup git` installs the post-commit correlator that powers `opentraces trail blame`
- `setup skill` installs the vendor-neutral skill under `~/.agents/skills/opentraces/` and symlinks it into supported harnesses (e.g. `~/.claude/skills/opentraces`)
- `setup bucket` configures the private bucket sync target (the workspace state that backs the trace index and Trace Trails)
- `setup capture-otlp` enables the higher-fidelity Claude Code Context Tree capture source
- `setup watcher` installs the background attribution daemon

## Machine-Readable Agent Flows

Agents should prefer `--json` when they need structured output:

```bash
opentraces --json status
opentraces --json trace query --cwd --since 1d
opentraces --json trace get <trace-id>
opentraces --json config show
opentraces --json trail track <trace-id>
opentraces --json trail blame commit <sha>
opentraces --json ctx tree <trace-id>
opentraces security tools list --json
```

That avoids scraping human-oriented terminal layouts. `trail track` returns the VCS-anchored evidence chain (Trace Patch, Git Anchor, Patch Trail) as structured JSON. To resolve an `ot://` resource directly, pass it to `opentraces trace get`.

## Review And Publish By Agent

A coding agent can drive the normal human workflow:

```bash
opentraces dataset review approve my-dataset --all
opentraces dataset publish my-dataset
```

For LLM-reviewed publication, run the workflow's review step before `publish`,
then `dataset publish` will see clean row verdicts.

## Discovering Agent Capabilities

The hidden `opentraces _capture` surface is what hooks call after a Claude Code session ends:

```bash
opentraces _capture --project-dir <path> --session-dir <path>
```

It is not intended for direct human use. The Claude Code stop hook spawns it as a detached subprocess so the new turn lands in the inbox in seconds rather than waiting on the watcher tick.

## Dataset Import

The legacy `opentraces pull` verb was removed in 0.4. To seed a dataset from an existing JSONL file, use the ad-hoc dataset path:

```bash
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
```

The `hermes` `FormatImporter` is still registered for use by dataset workflows. See [Supported Agents](/docs/cli/supported-agents).
