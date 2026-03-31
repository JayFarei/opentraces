# Quick Start

From local inbox to published dataset.

## 1. Install

```bash
pipx install opentraces
```

## 2. Authenticate

```bash
opentraces login --token
```

Paste a HuggingFace access token with **write** scope from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). Use `HF_TOKEN` instead if you are running headless.

## 3. Initialize the Project

```bash
opentraces init --review-policy review --start-fresh
```

This creates `.opentraces/config.json`, `.opentraces/staging/`, the session hook for Claude Code, and installs the opentraces skill into `.agents/skills/opentraces/`. If you omit the flags, `opentraces init` will prompt for the same choices interactively.

If Claude Code already has session logs for this repo, pass `--import-existing` to pull that backlog into the inbox now. Use `--start-fresh` if you only want capture from your next connected session onward.

## 4. Open the Inbox

### Web inbox

```bash
opentraces web
```

The browser inbox shows a timeline of each session's steps, tool calls, and reasoning. Switch to the review view to see context items grouped by source.

![Web inbox - timeline view](/docs/assets/web-timeline.png)

![Web inbox - review view](/docs/assets/web-review.png)

### Terminal inbox

```bash
opentraces tui
```

The TUI shows sessions, summary, and detail in a three-panel layout. Use keyboard shortcuts to navigate, commit, reject, or discard traces.

![Terminal inbox](/docs/assets/tui.png)

Use `session list`, `session commit`, `session reject`, and `session redact` if you prefer direct CLI control.

## 5. Commit and Push

```bash
opentraces commit --all
opentraces push
```

`commit` moves inbox traces to the committed stage. `push` uploads committed traces to `{username}/opentraces` on Hugging Face Hub as sharded JSONL and updates the dataset card.

## What Happens Next

Your traces are available as a Hugging Face dataset:

```python
from datasets import load_dataset

ds = load_dataset("your-name/opentraces")
```

## Next Steps

- [Security Modes](/docs/security/tiers) - Review policy and security pipeline
- [CLI Reference](/docs/cli/commands) - Full command reference
- [Schema Overview](/docs/schema/overview) - What is stored in a trace record
