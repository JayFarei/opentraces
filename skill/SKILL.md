---
name: opentraces
description: >
  Share agent traces to open datasets on HuggingFace Hub. Use this skill when
  the user mentions OpenTraces, trace capture, Trace Trails, workflow-built
  datasets, dataset review, or publishing reviewed dataset rows.
---

# opentraces

OpenTraces captures local agent traces, links them to Git evidence with Trace
Trails, lets workflows turn one or more traces into local datasets, and then
publishes reviewed dataset rows to HuggingFace remotes.

## Current Command Model

- Global setup: `opentraces setup`, `opentraces setup auth`, `opentraces auth`
- Project setup: `opentraces init`, `opentraces status`, `opentraces doctor`
- Trace retrieval: `opentraces trace query`, `opentraces trace map`, `opentraces trace get`
- Trace Trails: `opentraces trail blame`, `graph`, `resume`, `sync`, `timeline`, `teleport`
- Workflows: `opentraces workflow list`, `show`, `create`, `edit`, `remove`
- Datasets: `opentraces dataset new`, `run`, `review`, `approve`, `reject`, `publish`, `pull`

Old flat inbox commands such as `opentraces list`, `add`, `reject`, `push`,
`pull`, `web`, and `tui` are not part of the public command tree on this
development branch.

## Setup

```bash
opentraces setup
opentraces setup auth
opentraces auth whoami
opentraces init
opentraces status
opentraces doctor
```

`setup` is machine-global: hooks, auth, watcher, TruffleHog, LLM review, and
supporting binaries. `init` is project enrollment only; dataset remotes and
review policy belong under `opentraces dataset ...`.

## Trace Retrieval

Use trace commands when an agent needs compact evidence before loading full
transcripts.

```bash
opentraces trace query --lex "bug fix failing test" --json
opentraces trace query --skill grill-me --json
opentraces trace map <trace_id> --candidate <unit_id> --json
opentraces trace get <trace_id> --json
```

`trace query` returns bounded candidate packets. `trace map` returns a
workflow-neutral evidence map or candidate slice. `trace get` is the explicit
full retrieval step.

### Bursts and intent

`trace map --bursts` (or `trace get <ref> --bursts`) projects the trace's
file_edit / patch_created nodes into one virtual `change_burst` node per
cluster of nearby edits. Each burst exposes:

- `step_range` — `[min_step, max_step]` of the underlying nodes
- `unique_files` — repo-relative path → hunk count (deduped: absolute and
  relative variants of the same file collapse onto one entry)
- `patches` — one entry per Edit/Write tool call (NOT one per file)
- `burst_commit_sha` — modal commit across the burst's patches, fallback to
  the first git commit seen via the post-tool hook trail
- `intent` — structured object: `{trigger, most_substantive_spec, spec_chain,
  burst_commit_sha, commit_subject, commit_body}`. The trigger is the short
  imperative authorising the action ("ok", "let's go ahead and commit");
  the spec is the most recent substantive user instruction before the
  burst. `intent_text` / `intent_user_step` remain as legacy aliases for
  `intent.most_substantive_spec.{text, step}`.

Pass `--no-commit-lookup` to skip the per-burst `git log` lookup when running
offline or in a hot CLI path. The burst commit's SHA is a separate concept
from the trace's `outcome.commit_sha` (which is the *last* commit of the
session).

## Trace Trails

Trace Trails are the Git-anchored evidence chain for what a trace changed and
where that change lives now.

```bash
opentraces trail explain --trace <id> --step <n>
opentraces trail explain <path>:<line>
opentraces trail sync --patch <trace_patch_id>
opentraces trail sync --anchor <git_anchor_id>
opentraces trail blame <sha>
opentraces trail graph
opentraces trail resume <trace_id>
opentraces trail timeline <trace_id>
opentraces trail teleport export <trace_id> --output <dir>
opentraces trail teleport open <bundle> --project <blank-dir>
```

`trail sync` is the renamed follow operation: it synchronizes OpenTraces'
current understanding of a Trace Patch or Git Anchor with the latest Git
history and reports the current survival state.

`trail timeline` shows the observed timeline of snapshots, patches, anchors,
and survival observations for a trace.

`trail teleport` moves a trace plus the retained Git evidence needed to inspect
or resume it in a blank workspace.

## Workflows

Workflows are local skill-format packages that know how to turn trace evidence
into dataset rows.

```bash
opentraces workflow list
opentraces workflow show <name>
opentraces workflow create <name>
opentraces workflow edit <name>
opentraces workflow remove <name> --yes
```

Create or edit workflows locally; avoid installing arbitrary workflow packages
unless the product explicitly adds a trusted install path.

## Datasets

A dataset is built by running a workflow over one or more traces. It can stay
local, or it can be bound to a HuggingFace dataset remote and published after
review/security gates pass.

```bash
opentraces dataset list
opentraces dataset new <name>
opentraces dataset run <name> --dry-run --limit 5 --verbose
opentraces dataset run <name>
opentraces dataset review <name>
opentraces dataset approve <name> <row_id>
opentraces dataset reject <name> <row_id>
opentraces dataset review reset <name> <row_id>
opentraces dataset remote create <name> <owner/name> --private
opentraces dataset publish <name> --check-only
opentraces dataset publish <name>
opentraces dataset pull <name> --data
opentraces dataset withdraw <name> <row_id> --reason <code>
```

Manual review means rows remain local until approved. Automatic review policy
may mark rows publishable, but remote egress is still explicit: publish is a
separate user action.

## JSON Mode

Prefer `--json` for agent automation:

```bash
opentraces --json status
opentraces --json trace query --skill grill-me
opentraces --json trail sync --patch <patch_id>
opentraces --json dataset status <name> --remote
```

## Troubleshooting

| Problem | Action |
|---|---|
| Not initialized | Run `opentraces init` |
| Auth missing | Run `opentraces setup auth` or `opentraces auth login` |
| No traces visible | Check `opentraces setup claude-code`, then `opentraces status` |
| Trace Trail event log invalid | Run `opentraces doctor`; rebuild support is an internal repair path |
| Publish blocked | Run `opentraces dataset status <name> --remote --json` and `opentraces dataset publish <name> --check-only` |
