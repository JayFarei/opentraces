---
name: opentraces
description: >
  Share agent traces to open datasets on HuggingFace Hub. Use this skill
  whenever the user mentions sharing, publishing, or uploading traces
  or agent activity to HuggingFace. Also use when user says
  "opentraces", "share this trace", "publish traces", "upload traces",
  "contribute traces", "donate traces", or asks about trace review,
  redaction, staging, or push workflows. Proactively suggest this skill
  after completing significant agent traces where valuable work was done.
---

# opentraces

Open protocol + CLI for repo-local agent trace capture, review, and upload.

After each captured agent session, opentraces parses the trace, runs security
scanning and redaction, stores it locally, and exposes it through the web UI,
TUI, and CLI. You review, stage, and push the traces you want to share.

## Current 0.3 Model

- Repo marker: `.opentraces.json`
- Machine-local state: `~/.opentraces/projects/<slug>/...`
- Public inbox commands: `list`, `show`, `add`, `reject`, `reset`, `redact`, `discard`
- Upload vocabulary: `inbox`, `staged`, `pushed`, `rejected`, `blocked`
- Live capture: Claude Code
- Dataset import: `opentraces pull --parser hermes`

## Quick Reference

### Setup

```bash
opentraces auth login
opentraces auth login --token
opentraces init
opentraces init --review-policy review
opentraces init --review-policy auto
opentraces init --import-existing
```

### Review And Publish

```bash
opentraces status
opentraces list
opentraces list --stage inbox
opentraces show <TRACE_ID>
opentraces show <TRACE_ID> --verbose
opentraces show <TRACE_ID> --markdown
opentraces add <TRACE_ID>
opentraces add --all
opentraces reject <TRACE_ID>
opentraces reset <TRACE_ID>
opentraces redact <TRACE_ID>
opentraces discard <TRACE_ID> --yes
opentraces web
opentraces tui
opentraces push
opentraces push --llm-review
opentraces llm-review --scope staged
opentraces assess
opentraces doctor
```

### Remotes, Import, And Export

```bash
opentraces remote list
opentraces remote add owner/dataset
opentraces remote create owner/dataset --private
opentraces remote visibility owner/dataset --public
opentraces pull owner/dataset --parser hermes
opentraces export --format agent-trace
opentraces blame abc1234
opentraces blame abc1234 src/auth.py
opentraces graph
opentraces backfill
```

## Onboarding

### Step 1: Check Whether The Repo Is Already Initialized

Look for `.opentraces.json` in the repo root.

If it exists, start with:

```bash
opentraces status
opentraces list --stage inbox
```

Do not look for `.opentraces/config.json`. That is old.

### Step 2: Check Authentication

```bash
opentraces auth whoami
```

If not authenticated:

- use `opentraces auth login` for the normal browser-based flow
- use `opentraces auth login --token` in headless or CI environments
- `HF_TOKEN` also works and takes precedence over stored credentials

### Step 3: Gather Preferences

Before running `init`, clarify:

1. Review policy: `review` or `auto`
2. Remote dataset: connect now or later
3. Existing traces: backfill with `--import-existing` or start fresh

### Step 4: Initialize Explicitly

Standard setup:

```bash
opentraces init --agent claude-code --review-policy review --import-existing
```

With an explicit remote:

```bash
opentraces init --agent claude-code --review-policy review --remote owner/dataset --private
```

`init` writes `.opentraces.json`, registers the repo in the global config,
installs the Claude Code hook unless `--no-hook` is used, and installs the
bundled skill into the project.

## Core Loop

### 1. Capture

After `init`, Claude Code sessions are captured automatically. The pipeline:

1. discovers the session transcript
2. parses it into `TraceRecord`
3. filters trivial traces
4. enriches with git, attribution, dependencies, and metrics
5. runs security scanning and redaction
6. places the trace into a visible stage

### 2. Review

Use any of these:

```bash
opentraces web
opentraces tui
opentraces list --stage inbox
opentraces show <TRACE_ID>
```

The visible stages are:

- `inbox`
- `staged`
- `pushed`
- `rejected`
- `blocked`

### 3. Stage

```bash
opentraces add <TRACE_ID>
opentraces add --all
```

`add` stages Inbox traces for the next push. It refuses `blocked` and
`rejected` traces.

### 4. Push

```bash
opentraces push
```

Each push uploads staged traces as a new Hugging Face shard and refreshes the
dataset card.

## Review Operations

### Inspecting Traces

```bash
opentraces list --stage inbox
opentraces list --by-commit
opentraces show <TRACE_ID>
opentraces show <TRACE_ID> --verbose
opentraces show <TRACE_ID> --markdown
```

Human `show` output truncates long step content by default. Use `--verbose`
for the full terminal view, or `--json` for structured output.

### Editing State

```bash
opentraces add <TRACE_ID>
opentraces reject <TRACE_ID>
opentraces reset <TRACE_ID>
opentraces redact <TRACE_ID>
opentraces discard <TRACE_ID> --yes
```

Use:

- `reject` to keep a trace local only
- `reset` to move it back to Inbox
- `redact` to rewrite sensitive text in place
- `discard` to delete the local trace permanently

## Push, Remotes, And Visibility

### Push Options

```bash
opentraces push --private
opentraces push --public
opentraces push --publish
opentraces push --gated
opentraces push --repo owner/dataset
opentraces push --no-assess
opentraces push --no-trufflehog
opentraces push --llm-review
```

Important behavior:

- `push` uploads `staged` traces, not “committed” traces
- assessment runs by default during push
- `push --llm-review` requires a clean Tier 2 verdict on every staged trace
- `push --no-trufflehog` skips Tier 1.5 for one push only

### Remote Management

```bash
opentraces remote list
opentraces remote add owner/dataset
opentraces remote create owner/dataset --private
opentraces remote visibility owner/dataset --public
opentraces remote remove owner/dataset
opentraces remote delete owner/dataset
```

Use `push --repo owner/dataset` as a one-shot override when you do not want to
change the active remote permanently.

## Security And Quality

### Security Tiers

Current user-facing security layers:

1. Regex patterns, always on
2. Shannon entropy, always on
3. TruffleHog, optional
4. LLM trace review, optional and on demand
5. Human review

`SECURITY_VERSION` is currently `0.3.0`.

### TruffleHog

```bash
opentraces setup trufflehog
opentraces setup trufflehog --enable
opentraces setup trufflehog --disable
```

Current behavior:

- findings are redacted in place
- findings force review before upload
- `verify_secrets` stays off by default

### LLM Review

Configure once:

```bash
opentraces setup llm-review
```

Run it:

```bash
opentraces llm-review
opentraces llm-review --scope inbox
opentraces llm-review --scope staged
opentraces llm-review --trace 8a3f1c
opentraces llm-review --dry-run
```

Gate a push on it:

```bash
opentraces push --llm-review
```

### Review Policy

```bash
opentraces setup review-policy --review
opentraces setup review-policy --auto
opentraces setup review-policy --print
```

`--auto` means safe traces are auto-approved into `staged`. It does not push
automatically.

### Assessment

```bash
opentraces assess
opentraces assess --judge
opentraces assess --dataset owner/dataset
opentraces assess --explain
```

Local assess prefers staged traces first. `push` already runs assessment by
default unless `--no-assess` is passed.

## Git Correlation And Attribution

Install the git hook:

```bash
opentraces setup git
```

Then use:

```bash
opentraces list --by-commit
opentraces blame abc1234
opentraces blame abc1234 src/auth.py
opentraces blame abc1234 src/auth.py --lines
opentraces graph
opentraces graph --trace abc12
opentraces backfill
```

`blame` takes a commit SHA (bare or `c:<sha>`) and an optional path. Add
`--lines` for git-blame-style per-line output. `graph` is commit-primary
by default; pivot to a single trace with `--trace <id>`. Both require a
populated attribution cache — run `opentraces backfill` when empty.

## Import And Export

### Import

```bash
opentraces pull owner/dataset --parser hermes
opentraces pull owner/dataset --parser hermes --auto
opentraces pull owner/dataset --parser hermes --limit 10 --dry-run
```

Hermes is currently an import path, not a live-capture harness.

### Export

```bash
opentraces export --format agent-trace
opentraces export --format atif
```

## JSON Mode

Prefer `--json` whenever another agent needs structured output:

```bash
opentraces --json status
opentraces --json list --stage inbox
opentraces --json show <TRACE_ID>
opentraces --json config show
opentraces --json blame abc1234
opentraces --json backfill
```

## Troubleshooting

| Problem | Action |
|---------|--------|
| Not initialized | Run `opentraces init` |
| No traces visible | Check `opentraces setup claude-code`, then `opentraces status` |
| Traces blocked | Run `opentraces list --stage blocked` and inspect with `show` |
| Push failing | Check `auth whoami`, `remote list`, and `doctor` |
| TruffleHog enabled but missing | Run `opentraces setup trufflehog` or `--disable` |
| llm-review unreachable | Run `opentraces setup llm-review --test` |

When removing opentraces from a repo, use:

```bash
opentraces remove
opentraces remove --all
```
