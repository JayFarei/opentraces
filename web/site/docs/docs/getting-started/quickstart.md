# Quick Start

From local inbox to published dataset.

## 1. Install

```bash
pip install opentraces
```

## 2. Authenticate

```bash
opentraces login
```

Use `HF_TOKEN` instead if you are running headless.

## 3. Initialize the Project

```bash
opentraces init --review-policy review --push-policy manual --start-fresh
```

This creates `.opentraces/config.json`, `.opentraces/staging/`, and the session hook for Claude Code. If you omit the flags, `opentraces init` will prompt for the same choices interactively.

If Claude Code already has session logs for this repo, pass `--import-existing` to pull that backlog into the inbox now. Use `--start-fresh` if you only want capture from your next connected session onward.

## 4. Open the Inbox

```bash
opentraces web
```

or

```bash
opentraces tui
```

The browser inbox is the default path for review. Use `session list`, `session approve`, `session reject`, and `session redact` if you prefer CLI control.

## 5. Commit and Push

```bash
opentraces commit --all
opentraces push
```

`commit` groups ready traces for upload. `push` uploads committed traces to `{username}/opentraces` on Hugging Face Hub as sharded JSONL and updates the dataset card.

## What Happens Next

Your traces are available as a Hugging Face dataset:

```python
from datasets import load_dataset

ds = load_dataset("your-name/opentraces")
```

## Next Steps

- [Security Modes](/docs/security/tiers) - Review policy, push policy, and security tiers
- [CLI Reference](/docs/cli/commands) - Full command reference
- [Schema Overview](/docs/schema/overview) - What is stored in a trace record
