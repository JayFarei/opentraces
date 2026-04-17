# Agent Setup

opentraces is designed to be usable from both a shell and another coding agent.

## Project-Local Setup

Inside a repo, the normal path is:

```bash
opentraces auth login
opentraces init --agent claude-code --review-policy review
```

`init` writes `.opentraces.json`, registers the repo in the global config, installs the capture hook unless you opt out, and installs the bundled opentraces skill into the project.

## Claude Code

Claude Code is the current live-capture adapter.

For a full setup:

```bash
opentraces setup claude-code
opentraces setup git
opentraces setup skill
```

What each integration does:

- `setup claude-code` installs the `Stop` and `PostCompact` hooks in `~/.claude/settings.json`
- `setup git` installs the post-commit correlator that powers `opentraces blame`
- `setup skill` installs the vendor-neutral skill under `~/.agents/skills/opentraces/` and links it into supported harnesses

## Machine-Readable Agent Flows

Agents should prefer `--json` when they need structured output:

```bash
opentraces --json status
opentraces --json list --stage inbox
opentraces --json show <trace-id>
opentraces --json config show
```

That avoids scraping human-oriented terminal layouts.

## Review And Push By Agent

A coding agent can drive the normal human workflow:

```bash
opentraces web
opentraces add --all
opentraces push
```

Or the stricter security path:

```bash
opentraces llm-review --scope staged
opentraces push --llm-review
```

## Dataset Import

Hermes support is currently an import path, not a live-capture harness:

```bash
opentraces pull owner/dataset --parser hermes
```
