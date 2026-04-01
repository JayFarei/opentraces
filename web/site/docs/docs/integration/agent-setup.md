# Agent Setup

opentraces is designed to be driven by agents as well as by humans.

## What The CLI Emits

Most commands emit structured JSON with `next_steps` and `next_command`, so an agent can chain the workflow without parsing free-form text.

## Setup by Agent

`opentraces init` installs the session hook for your agent and copies the bundled skill into `.agents/skills/opentraces/`. Pass `--agent` to specify which harness to connect.

### Claude Code

```bash
opentraces login
opentraces init --agent claude-code --review-policy review --start-fresh
```

If the repo already has existing session logs and you want them in the inbox immediately, switch `--start-fresh` to `--import-existing`.

### Hook Enrichment

For richer trace metadata, install the Claude Code session hooks:

```bash
opentraces hooks install
```

This registers two hooks in `.claude/settings.json`:
- **`on_stop`** — runs at session end, captures context window state, token usage, and project metadata
- **`on_compact`** (PostCompact event) — captures context compaction events for long sessions

Hooks fire automatically on every session — no further action needed after `hooks install`.

### Hermes

```bash
opentraces login
opentraces init --agent hermes --review-policy review --start-fresh
```

After setup, the current surfaces are:

- `opentraces web` for the browser inbox
- `opentraces tui` for the terminal inbox
- `opentraces session list` for machine-readable review
- `opentraces status` for the current inbox summary
- `opentraces context` for a compact project snapshot

## Hidden Capture Command

The hook calls the hidden `_capture` command with a specific session directory:

```bash
opentraces _capture --session-dir /path/to/session --project-dir /path/to/project
```

That is the internal batch entry point for agent automation and tests.

## Machine Discovery

Hidden commands are still available for automation:

```bash
opentraces capabilities --json
opentraces introspect
```

They expose versioning, feature discovery, and the current `TraceRecord` schema.
