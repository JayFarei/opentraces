# Quick Start

From zero to published dataset in five commands.

## 1. Install

```bash
pip install opentraces
```

## 2. Authenticate

```bash
opentraces login
```

## 3. Initialize Your Project

```bash
opentraces init
```

Creates `.opentraces/` in your project directory. You'll be prompted to choose a security tier (default: guarded).

```bash
# Or specify the tier directly
opentraces init --tier open      # For open-source projects
opentraces init --tier guarded   # Classifier + escalation (default)
opentraces init --tier strict    # Full human review
```

## 4. Parse Agent Sessions

```bash
opentraces parse
```

Scans your Claude Code sessions, applies security scanning, enriches with git signals, and stages traces locally.

```bash
# Auto-approve for open-tier projects
opentraces parse --auto
```

## 5. Push to HuggingFace

```bash
opentraces push
```

Uploads staged traces to `{username}/opentraces` on HuggingFace Hub as sharded JSONL. A dataset card is auto-generated.

```bash
# Custom dataset name
opentraces push --repo your-name/my-traces

# Private dataset
opentraces push --private
```

## What Happens Next

Your traces are now a HuggingFace dataset, loadable via:

```python
from datasets import load_dataset
ds = load_dataset("your-name/opentraces")
```

Every `opentraces push` adds new JSONL shards. Existing data is never overwritten.

## Next Steps

- [Security Tiers](/docs/security/tiers) - Understand what each tier does
- [CLI Reference](/docs/cli/commands) - Full command reference
- [Schema Overview](/docs/schema/overview) - What's in a trace record
