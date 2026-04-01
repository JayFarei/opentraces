---
name: opentraces
description: >
  Share agent traces to open datasets on HuggingFace Hub. Use this skill
  whenever the user mentions sharing, publishing, or uploading traces,
  sessions, or agent activity to HuggingFace. Also use when user says
  "opentraces", "share this session", "publish traces", "upload traces",
  "contribute traces", "donate sessions", or asks about trace review,
  redaction, commit, or push workflows. Proactively suggest this skill
  after completing significant coding sessions where valuable work was done.
---

# opentraces - Share Agent Traces to HuggingFace Hub

Open protocol + CLI for repo-local agent trace capture, review, and upload.
After each coding session, opentraces automatically captures your trace,
runs security scanning, and stages it for review. You review, commit, and
push to a HuggingFace dataset.

## Quick Reference

### Getting Started
```
opentraces login                       # authenticate with HuggingFace (browser OAuth)
opentraces login --token               # authenticate via token paste (headless/CI)
opentraces init                        # initialize project (interactive)
opentraces status                      # show inbox tree with stage counts
```

### Review & Publish
```
opentraces session list                # list staged sessions (default: all stages)
opentraces session show <ID>           # full trace detail
opentraces session commit <ID>         # commit a trace for push
opentraces session reject <ID>         # mark as rejected (never pushed)
opentraces session reset <ID>          # undo commit/reject, back to inbox
opentraces session redact <ID> --step N  # scrub a specific step
opentraces session discard <ID> --yes  # permanently delete a trace
opentraces commit --all                # bulk commit all inbox traces
opentraces push                        # upload committed traces to HF Hub
opentraces push --assess               # upload + run quality assessment after push
opentraces assess                      # score committed traces (local quality.json)
opentraces assess --dataset owner/name # refresh quality.json on remote HF dataset
```

### Inspect
```
opentraces stats                       # aggregate statistics (traces, tokens, cost)
opentraces web                         # open browser inbox UI (port 5050)
opentraces tui                         # open terminal inbox UI
```

### Settings
```
opentraces whoami                      # print HF username
opentraces logout                      # clear stored credentials
opentraces config show                 # display current config
opentraces config set [OPTIONS]        # update config values
opentraces remote                      # show current dataset remote
opentraces remote set owner/name       # set HF dataset remote
opentraces remote remove               # remove configured remote
opentraces remove                      # remove opentraces from project
```

## Onboarding

When the user wants to set up opentraces, gather their preferences in
conversation rather than relying on interactive prompts.

### Step 1: Check if already initialized

Look for `.opentraces/config.json` in the project root. If it exists, run
`opentraces context` to see the current state and skip to the core loop.

### Step 2: Check authentication

```bash
opentraces whoami
```

If not authenticated: `opentraces login` opens a browser for OAuth device-code
flow. In headless or CI environments, use `opentraces login --token` to paste
an HF token with write scope. The `HF_TOKEN` environment variable also works
and takes highest priority.

### Step 3: Gather preferences

Ask the user for these three choices:

1. **Review policy**: `review` (you review each session before push) or `auto`
   (safe sessions skip the inbox and commit automatically, then auto-push)
2. **Remote**: a HuggingFace dataset repo in `owner/name` format, or skip
3. **Existing sessions**: if Claude Code sessions already exist for this repo,
   ask whether to import them now (`--import-existing`) or start fresh
   (`--start-fresh`)

### Step 4: Run init with explicit flags

Standard setup:
```bash
opentraces init --agent claude-code --review-policy review --start-fresh
```

With remote and existing session import:
```bash
opentraces init --agent claude-code --review-policy review --import-existing --remote owner/dataset-name --private
```

Additional init flags:
- `--public` / `--private`: dataset visibility (default: private)
- `--no-hook`: skip installing the Claude Code SessionEnd hook

Init creates `.opentraces/config.json`, `.opentraces/staging/`, installs the
SessionEnd hook in `.claude/settings.json`, and copies this skill into
`.agents/skills/opentraces/`.

## The Core Loop

### 1. Capture (automatic)

After `init`, a Claude Code `SessionEnd` hook runs `opentraces _capture`
automatically when each session ends. The capture pipeline parses the session,
runs enrichment (git signals, attribution, dependencies, metrics), applies
security scanning and redaction, and stages the result as JSONL. Sessions
with fewer than 2 steps or zero tool calls are silently filtered out.

### 2. Review

Check what landed in the inbox:
```bash
opentraces context                          # project state + suggested next action
opentraces session list --stage inbox       # list inbox sessions
opentraces session show <TRACE_ID>          # inspect a specific trace
```

For each trace, decide: commit (commit for push), reject (keep local), or
redact specific steps before committing.

### 3. Commit

```bash
opentraces session commit <TRACE_ID>        # commit one trace
opentraces commit --all                     # commit all inbox traces
opentraces commit --all -m "description"    # with custom commit message
```

### 4. Push

```bash
opentraces push                             # upload committed traces
```

Each push creates a new JSONL shard on the remote (never appends to existing
files). Content-hash deduplication skips traces already present on the remote.
A dataset card (README.md) is auto-generated with CC-BY-4.0 license.

## Session Review

### Listing and filtering

```bash
opentraces session list                                 # all stages
opentraces session list --stage inbox                   # inbox only
opentraces session list --stage committed               # committed only
opentraces session list --model opus                    # filter by model substring
opentraces session list --agent claude-code --limit 10  # filter by agent, cap results
```

Valid stages: `inbox`, `committed`, `pushed`, `rejected`.

### Inspecting a trace

```bash
opentraces session show <TRACE_ID>           # summary + truncated step content
opentraces session show <TRACE_ID> --verbose # full step content (can be large)
opentraces --json session show <TRACE_ID>    # full record as JSON (never truncated)
```

Human output truncates step content to 500 chars by default to protect context
windows. Use `--verbose` for the full human view, or `--json` if you need to
parse the complete record programmatically.

### Actions

```bash
opentraces session commit <ID>       # commit for push
opentraces session reject <ID>       # mark rejected, kept local only
opentraces session reset <ID>        # undo commit or reject, back to inbox
opentraces session redact <ID> --step 3   # clear step 3's content, reasoning,
                                          # tool_calls, observations, and snippets
opentraces session discard <ID> --yes     # permanently delete (--yes skips confirm)
```

`reset` works from committed or rejected states but cannot undo a pushed trace.

### What to look for during review

- Secrets that escaped automatic redaction (API keys, tokens, passwords)
- Internal hostnames (*.internal, *.corp, *.local)
- Customer data, PII, or identifiable information
- Collaboration URLs with embedded tokens (Slack, Jira, Confluence)
- Database connection strings
- Traces too short or trivial to be useful

## Commit & Push

### Committing

```bash
opentraces commit --all                     # commit all inbox traces
opentraces commit --all -m "batch of fixes" # with message
```

Commit creates a bundle of traces ready for upload. Auto-generates a message
from the first few task descriptions if `-m` is not provided.

### Pushing

```bash
opentraces push                        # upload to configured remote
opentraces push --private              # force private visibility
opentraces push --public               # force public visibility
opentraces push --gated                # enable gated access (auto-approve)
opentraces push --repo owner/name      # override remote for this push
opentraces push --publish              # flip existing private dataset to public
                                       # (no upload, visibility change only)
```

Remote resolution: `--repo` flag > project config remote > interactive selector >
`username/opentraces` fallback.

### Push behavior

- Creates a new `data/traces-NNNN.jsonl` shard per push (never appends)
- SHA-256 content-hash deduplication against existing remote shards
- Auto-generates or updates dataset README card
- Atomic: if upload fails, no partial data is left on the remote
- Retry: 3 attempts with exponential backoff on network failure
- File lock prevents concurrent pushes (exit code 7 if contention)

## Quality Assessment

`opentraces assess` scores committed (local) traces against quality rubrics and
writes a `quality.json` sidecar with per-trace scores and an aggregate summary.

```bash
opentraces assess                        # score committed traces
opentraces assess --judge                # use LLM judge for rubric scoring
opentraces assess --judge-model sonnet   # judge model: haiku, sonnet, or opus
opentraces assess --limit 20             # cap traces assessed in this run
opentraces assess --compare-remote       # fetch remote quality.json and show delta
opentraces assess --all-staged           # include inbox traces, not just committed
opentraces assess --dataset owner/name   # assess remote HF dataset, update its
                                         # README + quality.json without a new push
```

`push --assess` runs quality automatically after upload and includes scores in
the dataset card. `quality.json` is uploaded as a sidecar to the HF dataset repo
after `assess --dataset` or `push --assess`. Dataset cards include shields.io
quality scorecard badges when a `quality.json` is present.

## Security

Every captured trace passes through a security pipeline automatically.
There is no "unfiltered" mode.

### What happens on capture

1. **Secret scanning**: two-pass, context-aware. First pass scans each field
   with rules tuned to the field type (tool inputs get entropy analysis, tool
   results and reasoning do not, to reduce false positives). Second pass scans
   the serialized JSONL bytes as a final catch-all.
2. **Automatic redaction**: detected secrets are replaced with `[REDACTED]` in
   the staged JSONL. Raw session files on disk are never modified.
3. **Heuristic classification**: flags internal hostnames, AWS account IDs,
   database connection strings, dense UUID sequences, and deep file paths.
4. **Path anonymization**: replaces usernames in file paths with hashed
   prefixes across macOS, Linux, Windows, and WSL path formats.

### Tunables

```bash
opentraces config set --classifier-sensitivity high    # low, medium, or high
opentraces config set --redact "MY_INTERNAL_DOMAIN"    # add custom redaction string
opentraces config set --exclude /path/to/sensitive-repo # exclude a project entirely
```

Custom redaction strings are appended (not replaced). Use for company-specific
values that the generic scanner might miss.

### Review policies and security

- `--review-policy review`: all traces land in inbox for manual review, commit and push manually
- `--review-policy auto`: clean traces (no scan matches) are committed and pushed automatically.
  Traces with any scan hits or redactions still land in inbox.

Even with `auto` review policy, traces with detected issues require review.

## Agent-Native Patterns

### Machine-readable output

Add `--json` to any command to suppress human-readable text and get only
structured JSON:

```bash
opentraces --json context
opentraces --json session list --stage inbox
opentraces --json push
```

JSON is emitted after the sentinel line `---OPENTRACES_JSON---`. When parsing
programmatically, split on this sentinel and parse the text that follows.

### Response shape

Every JSON response includes:
- `status`: `"ok"`, `"error"`, or `"needs_action"`
- `next_steps`: array of suggested next actions (human-readable)
- `next_command`: the single most likely next command to run

### The context command

`opentraces context` is the agent's "what should I do next?" command. It
returns project config, auth status, counts per stage, and a `suggested_next`
command. Start here when resuming work or uncertain about state.

### Discovery commands (hidden, for automation)

```bash
opentraces capabilities --json    # feature flags, supported agents, versions
opentraces introspect             # full API schema + TraceRecord JSON schema
```

### Machine/CI mode

Set `OPENTRACES_NO_TUI=1` to suppress TUI launch on bare `opentraces` invocation.
Bare `opentraces` on a non-TTY stdout already falls back to help text automatically.

```bash
OPENTRACES_NO_TUI=1 opentraces         # prints help, never opens TUI
opentraces --json context              # machine-readable project state
opentraces capabilities --json         # feature flags + supported env vars
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 2 | Usage error (bad flags, conflicting options) |
| 3 | Auth/config error (not authenticated, not initialized) |
| 4 | Network or upload error |
| 5 | Data corruption or invalid state |
| 6 | Not found (trace ID, project, or resource) |
| 7 | Lock contention (another process is pushing) |

## Configuration

### Viewing config

```bash
opentraces config show    # displays current config (tokens masked)
```

### Setting values

```bash
opentraces config set --classifier-sensitivity medium
opentraces config set --redact "SENSITIVE_VALUE"
opentraces config set --exclude /path/to/project
opentraces config set --pricing-file /path/to/pricing.json
```

### Managing the remote

```bash
opentraces remote                          # show current remote and visibility
opentraces remote set owner/dataset-name   # set remote
opentraces remote set owner/name --public  # set with visibility
opentraces remote remove                   # remove remote from config
```

If no `/` in the name, the authenticated username is prepended automatically.

## Troubleshooting

| Error | Fix |
|-------|-----|
| "Not authenticated" / "No HF token found" | `opentraces login` |
| "Not an opentraces project" / "Not initialized" | `opentraces init` in the project directory |
| "No sessions found" | Check that `~/.claude/projects/` has session files |
| Push fails with 403 | HF token lacks write scope, regenerate at huggingface.co/settings/tokens |
| Lock contention (exit 7) | Another process is pushing, wait and retry |
| "No traces ready for upload" | Run `opentraces commit --all` first |
| "All traces already exist on remote" | Content-hash dedup, nothing new to push |
| Traces not appearing after session | Hook may not be installed, run `opentraces init` again |

Start debugging with `opentraces context` for a full project state snapshot.

## Teardown

```bash
opentraces remove
```

Deletes the `.opentraces/` directory and removes the SessionEnd hook from
`.claude/settings.json`. Does not touch remote datasets or uploaded data.
To reinitialize: `opentraces init`.

## Prerequisites

- Python 3.10+
- `pipx install opentraces`
- HuggingFace account with write-scope token

## Keeping Up To Date

```bash
opentraces upgrade              # upgrade CLI + refresh skill and hook
opentraces upgrade --skill-only # just refresh the skill file and hook
```

`upgrade` detects how opentraces was installed (pipx, brew, pip, source)
and runs the appropriate upgrade command, then refreshes the skill file
and session hook in the current project.

## Further Context

For full documentation, schema details, and design rationale beyond this
skill file, fetch: https://www.opentraces.ai/llms.txt
