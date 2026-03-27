# Getting Started

## Installation

```bash
pip install opentraces
```

## Authentication

open traces publishes to Hugging Face Hub. You need a HF token:

```bash
opentraces auth
# or set the environment variable
export HF_TOKEN=hf_...
```

## Your First Publish

```bash
opentraces publish --tier automated
```

This will:

1. Scan `~/.claude/projects` for Claude Code sessions
2. Parse each session into the open traces schema
3. Run automated security screening (Tier 2)
4. Upload approved traces to your HF dataset repo

## Configuration

### Per-Project Security Tier

```bash
# In your project directory
opentraces config --tier danger     # Tier 1: regex scan only
opentraces config --tier automated  # Tier 2: classifier + escalation (default)
opentraces config --tier manual     # Tier 3: full human review
```

### Per-Session Override

```bash
opentraces publish --tier manual  # override for this run only
```

## Review Interface

For Tier 2 (flagged traces) and Tier 3 (all traces):

```bash
# CLI review
opentraces review

# Web review (local)
opentraces review --web
```

The review interface lets you:

- Approve individual traces or the full session
- Redact specific turns, tool outputs, or file contents
- Annotate traces with quality signals
- Skip the upload entirely

## Status

```bash
opentraces status
```

Shows pending traces, published counts, and security tier configuration.

## Agent Integration

open traces ships with a `SKILL.md` for Claude Code integration:

```bash
# As a git post-commit hook
opentraces publish --tier automated --json

# As a Claude Code skill
opentraces publish --tier automated --json
```

Every command emits structured JSON with `next_steps` and `next_command` fields so agents can chain operations.
