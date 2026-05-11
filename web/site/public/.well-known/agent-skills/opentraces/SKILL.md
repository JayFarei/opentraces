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

OpenTraces captures local agent traces, links them to Git evidence with Trace
Trails, lets workflows turn one or more traces into local datasets, and then
publishes reviewed dataset rows to HuggingFace remotes.

## Current Command Model

- Global setup: `opentraces setup`, `opentraces setup auth`, `opentraces setup bucket`, `opentraces setup skill`, `opentraces setup upgrade`, `opentraces auth`
- Project setup: `opentraces init`, `opentraces status`, `opentraces doctor`, `opentraces remove`
- Trace retrieval and search: `opentraces trace query`, `opentraces trace index`, `opentraces trace map`, `opentraces trace slice`, `opentraces trace get`, `opentraces trace teleport`
- Trace Trails (visible surface): `opentraces trail blame`, `opentraces trail graph`, `opentraces trail track`
- Bucket (private capture store): `opentraces bucket status`, `opentraces bucket manifest`, `opentraces bucket remote push/pull/diff/status`, `opentraces bucket replay`
- Workflows: `opentraces workflow create`, `opentraces workflow list`, `opentraces workflow templates`, `opentraces workflow remove`
- Datasets: `opentraces dataset list/new/run/review/publish/remote/schedule/status/remove`. Review transitions are `opentraces dataset review approve|reject|reset <name> [row_id...]`.

Old flat inbox commands such as `opentraces list`, `add`, `reject`, `push`,
`pull`, `web`, and `tui` are not part of the public command tree. Several
Trace Trails substrate commands (`trail explain`, `sync`, `timeline`,
`teleport`, `resolve`, `attach`, `rebuild`, `diff`, `resume`, `follow`,
`snapshots`, `snapshot checkout`) remain callable for scripting and
debugging but are hidden from `--help` after the CLI spine simplification.

## Setup

```bash
opentraces setup
opentraces setup auth
opentraces setup bucket          # opt into remote-by-default private bucket sync
opentraces setup skill           # install the opentraces skill into agent harnesses
opentraces setup upgrade         # upgrade CLI + refresh project skill file
opentraces auth whoami
opentraces init
opentraces status
opentraces doctor
```

`setup` is machine-global: hooks, auth, watcher, TruffleHog, LLM review, and
supporting binaries. `init` is project enrollment only; dataset remotes and
review policy belong under `opentraces dataset ...` and `opentraces config
set review_policy <auto|review> --project`. Private bucket configuration
belongs under `opentraces setup bucket` and `opentraces bucket remote`.

## Trace Retrieval

Use trace commands when an agent needs compact evidence before loading full
transcripts.

```bash
opentraces trace query --lex "bug fix failing test" --json
opentraces trace query --skill grill-me --json
opentraces trace index rebuild --json
opentraces trace map <trace_id> --candidate <unit_id> --json
opentraces trace slice <trace_id> --template bursts --json
opentraces trace get <trace_id> --json
opentraces trace teleport export <trace_id> --output <dir>
```

`trace query` returns bounded candidate packets over the local BM25 +
semantic Trace Index. `trace index` rebuilds and inspects that projection.
`trace map` returns a workflow-neutral evidence map or candidate slice.
`trace slice` materialises deterministic Trace Slice packets for dataset
workflows. `trace get` is the explicit full retrieval step. `trace
teleport` moves a trace and its retained Git evidence between workspaces.

## Trace Trails

Trace Trails are the Git-anchored evidence chain for what a trace changed and
where that change lives now. The visible top-level surface is `trail blame`,
`trail graph`, and `trail track`.

```bash
# Visible surface
opentraces trail blame <sha>
opentraces trail blame t:<trace_id>
opentraces trail graph
opentraces trail graph --trace <trace_id>
opentraces trail track <trace_id>
opentraces trail track --patch <trace_patch_id>
opentraces trail track --anchor <git_anchor_id>
opentraces trail track --since 12h --json
opentraces trail track --all --json --limit 50
```

`trail track` walks a trace's lineage through Git history and reports
current `HEAD` survival across all anchors, with batch JSONL output via
`--since`, `--all`, and `--patches-from`.

## Bucket

The bucket is the project-local private store of every captured trace, under
`~/.opentraces/projects/<slug>/bucket/`. It is local-only by default. Opt into
remote-by-default sync with `opentraces setup bucket`; sync is always
explicit.

```bash
opentraces bucket status --json
opentraces bucket manifest --json
opentraces bucket remote status --json
opentraces bucket remote push --json
opentraces bucket remote pull --json
opentraces bucket remote diff --json
opentraces bucket replay --repo <repo-dir>
```

Buckets are distinct from datasets. A bucket holds raw captured traces; a
dataset holds workflow-projected rows. `bucket replay` replays
bucket-exported Trace Trails into a Git repository (useful when a teammate
hands you a bucket and you need to materialise its evidence locally).

## Workflows

Workflows are skill-format packages (or Markdown files) that know how to turn
trace evidence into dataset rows. The main path is to scaffold one with
`opentraces workflow create` and then bind it to a dataset:

```bash
opentraces workflow templates --json
opentraces workflow create <name> --template skill-command-trajectory-eval-v1
opentraces workflow list --json
opentraces workflow remove <name> --yes
opentraces dataset new <name> --workflow ./workflows/<workflow>/WORKFLOW.md
opentraces dataset new <name> --workflow ./workflows/<workflow>/
```

The bundled `skill-command-trajectory-eval-v1` template materialises a ready
workflow that emits command-trajectory evaluation rows.

## Datasets

A dataset is built by running a workflow over one or more traces. It can stay
local, or it can be bound to a HuggingFace dataset remote and published after
review/security gates pass.

```bash
opentraces dataset list --json
opentraces dataset new <name> --workflow <workflow.md-or-package-dir>
opentraces dataset status <name> --json
opentraces dataset run <name> --dry-run --limit 5 --verbose
opentraces dataset run <name>
opentraces dataset review <name>
opentraces dataset review approve <name> <row_id>
opentraces dataset review reject <name> <row_id>
opentraces dataset review reset <name> <row_id>
opentraces dataset remote create <name> <owner/name> --private
opentraces dataset remote add <name> <owner/name>
opentraces dataset remote list <name>
opentraces dataset remote visibility <name> --public
opentraces dataset publish <name> --check-only
opentraces dataset publish <name>
opentraces dataset publish <name> --min-retention 0.5 --exclude-state lost
opentraces dataset schedule list
opentraces dataset remove <name> --yes
```

Manual review means rows remain local until approved. Automatic review policy
may mark rows publishable, but remote egress is still explicit: publish is a
separate user action.

## Onboarding Path

Step 1: install opentraces (`pipx install opentraces`) and verify with
`opentraces --version`. If already installed, run `opentraces setup upgrade`.

Step 2: authenticate with `opentraces auth login` (or `opentraces auth login
--token` on CI / headless).

Step 3: in the project repo, run `opentraces init --agent claude-code
--import-existing` to enroll the project and install the Claude Code capture
hooks.

Step 4: complete machine-global setup: `opentraces setup skill`,
`opentraces setup git`, optionally `opentraces setup trufflehog` and
`opentraces setup llm-review` for stronger security gates.

Step 5: when ready to publish, scaffold a dataset with
`opentraces dataset new <name>`, run it, review with
`opentraces dataset review <name> --web`, bind a remote, then
`opentraces dataset publish <name>`.

## JSON Mode

Prefer `--json` for agent automation:

```bash
opentraces --json status
opentraces --json trace query --skill grill-me
opentraces --json trace map <trace_id>
opentraces --json trail track <trace_id>
opentraces --json bucket status
opentraces --json dataset status <name>
```

## Troubleshooting

| Problem | Action |
|---|---|
| Not initialized | Run `opentraces init` |
| Auth missing | Run `opentraces setup auth` or `opentraces auth login` |
| No traces visible | Check `opentraces setup claude-code`, then `opentraces status` |
| Trace Trail event log invalid | Run `opentraces doctor`; `opentraces trail rebuild` re-derives advisory projections |
| Bucket not syncing | Run `opentraces setup bucket` to configure a remote, then `opentraces bucket remote status` |
| Publish blocked | Run `opentraces dataset status <name> --json` and `opentraces dataset publish <name> --check-only` |
