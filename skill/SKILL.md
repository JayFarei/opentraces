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

- Global setup: `opentraces setup`, `opentraces setup auth`, `opentraces setup bucket`, `opentraces setup skill`, `opentraces setup upgrade`, `opentraces auth`
- Project setup: `opentraces init`, `opentraces status`, `opentraces doctor`, `opentraces remove`
- Trace retrieval and search: `opentraces trace query`, `opentraces trace index`, `opentraces trace map`, `opentraces trace slice`, `opentraces trace get`, `opentraces trace teleport`
- Trace Trails (visible surface): `opentraces trail blame commit <sha>`, `opentraces trail blame pr render|create|update`, `opentraces trail graph`, `opentraces trail track`
- Context Tree: `opentraces ctx tree/show/step/reads/writes/diff/compactions/prune/resume/resolve/anchor-for-step`, plus `ctx list/info`
- Bucket (private capture store): `opentraces bucket status`, `opentraces bucket manifest`, `opentraces bucket verify`, `opentraces bucket repair`, `opentraces bucket rebuild`, `opentraces bucket prune`, `opentraces bucket prefetch`, `opentraces bucket remote push/pull/diff/status`, `opentraces bucket replay`
- Workflows: `opentraces workflow create`, `opentraces workflow list`, `opentraces workflow templates`, `opentraces workflow remove`
- Datasets: `opentraces dataset list/new/run/review/publish/remote/schedule/status/remove`. Review transitions are `opentraces dataset review approve|reject|reset <name> [row_id...]`.
- Security tools: `opentraces security tools list/info`, `opentraces security sanitize --tools <names>` or `--use-config`
- OTLP capture source: `opentraces setup capture-otlp`, `opentraces capture-otlp start|stop|status|restart|flush`

Old flat inbox commands such as `opentraces list`, `add`, `reject`, `push`,
`pull`, `web`, and `tui` are not part of the public command tree. Several
Trace Trails substrate commands (`trail explain`, `sync`, `timeline`,
`teleport`, `resolve`, `attach`, `rebuild`, `diff`, `resume`,
`snapshots`, `snapshot checkout`) remain callable for scripting and
debugging but are hidden from `--help` after the CLI spine simplification.

## Setup

```bash
opentraces setup
opentraces setup auth
opentraces setup bucket          # configure remote-by-default private bucket sync
opentraces setup skill           # install the opentraces skill into agent harnesses
opentraces setup upgrade         # upgrade CLI + refresh project skill file
opentraces config tracking-mode  # show; pass global|manual to set
opentraces auth whoami
opentraces init
opentraces status
opentraces doctor
```

`setup` is machine-global: tracking mode, hooks, auth, watcher, TruffleHog,
LLM review, and supporting binaries. Tracking mode (`opentraces config
tracking-mode`) controls enrollment: `global` (default) auto-enrolls every
project an agent touches — git or not — private + review-required the first
time a capture hook fires there, so `init` is optional; `manual` keeps the
explicit per-project `opentraces init` opt-in. `init` is project enrollment
only; dataset remotes and review policy belong under `opentraces dataset
...`. Private bucket configuration belongs under `opentraces setup bucket`
and `opentraces bucket remote`.

## Trace Retrieval

Use trace commands when an agent needs compact evidence before loading full
transcripts.

```bash
opentraces trace query --lex "bug fix failing test" --json
opentraces trace query --cwd --remote-bucket --json
opentraces trace query --skill grill-me --json
opentraces trace index rebuild --json
opentraces trace map <trace_id> --candidate <unit_id> --json
opentraces trace slice <trace_id> --template bursts --json
opentraces trace get <trace_id> --json
opentraces trace get <trace_id> --remote-bucket --json
opentraces trace teleport export <trace_id> --output <dir>
```

`trace query` returns bounded candidate packets over the local BM25 +
semantic Trace Index. `trace index` rebuilds and inspects that projection.
`trace map` returns a workflow-neutral evidence map or candidate slice.
`trace slice` materialises deterministic Trace Slice packets for dataset
workflows. `trace get` is the explicit full retrieval step. `trace
teleport` moves a trace and its retained Git evidence between workspaces.

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
where that change lives now. The visible top-level surface is `trail blame`
(now a group with `commit` and `pr` subcommands), `trail graph`, and
`trail track`.

```bash
# Visible surface
opentraces trail blame commit <sha>             # which traces authored this commit
opentraces trail blame commit t:<trace_id>      # which commits carry this trace
opentraces trail blame pr render --base main    # PR body for the current branch
opentraces trail blame pr create --base main    # gh pr create with the body
opentraces trail blame pr update --base main    # idempotent update of existing PR
opentraces trail graph
opentraces trail graph --trace <trace_id>
opentraces trail track <trace_id>
opentraces trail track --patch <trace_patch_id>
opentraces trail track --anchor <git_anchor_id>
opentraces trail track --since 12h --json
opentraces trail track --all --json --limit 50

# Hidden substrate commands (still callable from scripts and JSON automation)
opentraces trail explain --trace <id> --step <n>
opentraces trail explain <path>:<line>
opentraces trail sync --patch <trace_patch_id>
opentraces trail sync --anchor <git_anchor_id>
opentraces trail timeline <trace_id>
opentraces trail resume <trace_id>
opentraces trail teleport export <trace_id> --output <dir>
opentraces trail teleport open <bundle> --project <blank-dir>
opentraces trail resolve ot://trace/<id>/patches/<id>/trail --json
opentraces trail attach --trace <id> --commit <sha>
opentraces trail rebuild
opentraces trail search --commit <sha> --remote-bucket --json
```

`trail track` walks a trace's lineage through Git history and reports
current `HEAD` survival across all anchors, with batch JSONL output via
`--since`, `--all`, and `--patches-from`. The substrate `trail sync`
synchronizes OpenTraces' current understanding of a Trace Patch or Git
Anchor with the latest Git history. `trail timeline` shows the observed
timeline of snapshots, patches, anchors, and survival observations.
`trail teleport` moves a trace plus the retained Git evidence needed to
inspect or resume it in a blank workspace.

## Bucket

The bucket is the private store of every captured trace. It keeps raw
capture-time evidence under `~/.opentraces/bucket/`: per-trace envelopes,
patch history, `trail.jsonl.gz`, `context.jsonl.gz`, `sources.jsonl.gz`,
content-addressed blobs, an event-log mirror, and `manifest.json`. It is
local-only until `opentraces setup bucket` configures a private HuggingFace
bucket remote. Bucket sync is separate from dataset publication.

```bash
opentraces bucket status --json
opentraces bucket manifest --json
opentraces bucket verify --json
opentraces bucket repair --json
opentraces bucket rebuild --json
opentraces bucket rebuild --substrate context-tree --json
opentraces bucket prune --dry-run --json
opentraces bucket prefetch <trace_id> --json
opentraces bucket remote status --json
opentraces bucket remote push --json
opentraces bucket remote pull --json
opentraces bucket remote diff --json
opentraces bucket replay --repo <repo-dir>
```

Buckets are distinct from datasets. A bucket holds raw captured traces; a
dataset holds workflow-projected rows. `bucket rebuild` refreshes derived
bucket projections from canonical state. `bucket replay` replays
bucket-exported Trace Trails into a Git repository (useful when a teammate
hands you a bucket and you need to materialise its evidence locally).

## Context Tree

The Context Tree answers "what did the agent see at this step?" It rides on
the same canonical event log as Trace Trails and is addressed by
`Step.context_node_id` in schema `0.6.0`.

```bash
opentraces ctx list --json
opentraces ctx info <trace_id> --json
opentraces ctx tree <trace_id> --json
opentraces ctx show <context_node_id> --json
opentraces ctx step <trace_id> <step_index> --json
opentraces ctx reads <trace_id> --json
opentraces ctx writes <trace_id> --json
opentraces ctx diff <node_a> <node_b> --json
opentraces ctx compactions <trace_id> --json
opentraces ctx resume <context_node_id> --json
opentraces ctx prune <context_node_id> --source-jsonl <session.jsonl>
opentraces ctx resolve ot://context-node/<id> --json
opentraces ctx anchor-for-step <trace_id> <step_index>
```

Claude/Codex JSONL capture gives a useful structural approximation. For
higher-fidelity Claude Code context capture, set up the OTLP source:

```bash
opentraces setup capture-otlp
opentraces capture-otlp start
opentraces capture-otlp status --json
opentraces capture-otlp flush --session <session_id> --project <repo> --trace-id <trace_id>
```

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
opentraces dataset run <name> --approve-new --publish-check-only
opentraces dataset run <name> --approve-new --publish
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
opentraces dataset schedule add <name> --every 1h --approve-new --publish-check-only
opentraces dataset remove <name> --yes
```

Manual review means rows remain local until approved. Automatic review policy
may mark rows publishable, but remote egress is still explicit: publish is a
separate user action. `dataset publish --min-retention` and `--exclude-state`
filter rows by survival quality before staging.

## Security Tools

Security tools are optional and default off. Workflows can run named tools
directly, or use the project/global config to select enabled tools.

```bash
opentraces security tools list --json
opentraces security tools info regex --json
printf '%s\n' '{"text":"OPENAI_API_KEY=sk-demo"}' | opentraces security sanitize --tools regex
printf '%s\n' '{"row":{"path":"/Users/alice/project"}}' | opentraces security sanitize --tools path_anonymizer
printf '%s\n' '{"record":{...}}' | opentraces security sanitize --use-config
opentraces setup trufflehog
opentraces setup privacy-filter
opentraces setup llm-review
```

Registered inline tools are `regex`, `entropy`, `trufflehog`,
`privacy_filter`, `llm_pii`, `path_anonymizer`, and `classifier`. Session-level
LLM review is configured by `setup llm-review` but is a dataset publication
reviewer, not part of the per-record sanitize registry.

## JSON Mode

Prefer `--json` for agent automation:

```bash
opentraces --json status
opentraces --json trace query --skill grill-me
opentraces --json trace map <trace_id>
opentraces --json trail track <trace_id>
opentraces --json bucket status
opentraces --json ctx tree <trace_id>
opentraces security tools list --json
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
