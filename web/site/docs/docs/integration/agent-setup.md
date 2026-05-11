# Agent Setup

opentraces is designed to be usable from both a shell and another coding agent.

## Project-Local Setup

Inside a repo, the normal path is:

```bash
opentraces auth login
opentraces init --agent claude-code
```

`init` writes `.opentraces.json`, registers the repo in the global config, and installs the per-agent capture hook. Dataset remotes and review policy are configured separately (`opentraces dataset remote ...`, `opentraces config set review_policy review --project`).

## Claude Code

Claude Code is the current live-capture adapter.

For a full setup:

```bash
opentraces setup claude-code
opentraces setup git
opentraces setup skill
opentraces setup bucket
opentraces setup watcher install
```

What each integration does:

- `setup claude-code` installs the `PreToolUse`, `PostToolUse`, `Stop`, and `PostCompact` hooks in `~/.claude/settings.json`
- `setup git` installs the post-commit correlator that powers `opentraces trail blame`
- `setup skill` installs the vendor-neutral skill under `~/.agents/skills/opentraces/` and symlinks it into supported harnesses (e.g. `~/.claude/skills/opentraces`)
- `setup bucket` configures the private bucket sync target (the workspace state that backs the trace index and Trace Trails)
- `setup watcher` installs the background attribution daemon

## Machine-Readable Agent Flows

Agents should prefer `--json` when they need structured output:

```bash
opentraces --json status
opentraces --json trace query --cwd --since 1d
opentraces --json trace get <trace-id>
opentraces --json config show
opentraces --json trail track <trace-id>
opentraces --json trail blame <sha>
```

That avoids scraping human-oriented terminal layouts. `trail track` returns the VCS-anchored evidence chain (Trace Patch, Git Anchor, Patch Trail) as structured JSON. To resolve an `ot://` resource directly, pass it to `opentraces trace get`.

## Review And Publish By Agent

A coding agent can drive the normal human workflow:

```bash
opentraces dataset review my-dataset approve --all
opentraces dataset publish my-dataset
```

For LLM-reviewed publication, run the workflow's review step before `publish`, then `dataset publish` will see clean Tier 2 verdicts on each row.

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
