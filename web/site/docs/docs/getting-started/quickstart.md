# Quick Start

From local capture to a published Hugging Face dataset row stream.

## 1. Install

```bash
pipx install opentraces
```

## 2. Set Up The Machine

```bash
opentraces setup
```

`setup` is the machine-wide wizard. It can configure:

- **tracking mode** (`global` by default), so agent sessions can auto-enroll
  projects private + review-required the first time capture fires.
- **capture hooks** for Claude Code and Codex CLI.
- **git hook** and **watcher**, which mature Trace Trails after commits land.
- **bucket remote**, optional private HuggingFace sync for raw retained
  evidence.
- **HuggingFace login**, needed for bucket sync and dataset remotes.
- **optional security tools**, such as TruffleHog, privacy-filter, and
  LLM review. Per-record tools default off until a workflow or config enables
  them.

You can run specific setup commands non-interactively:

```bash
opentraces setup claude-code
opentraces setup codex-cli
opentraces setup git
opentraces setup bucket
opentraces setup capture-otlp
opentraces setup trufflehog
opentraces setup privacy-filter
opentraces setup llm-review
```

## 3. Enroll A Project

Under global tracking this is optional, but it is still useful when you want to
import existing sessions or be explicit about the connected agent.

```bash
opentraces init
opentraces init --agent claude-code --import-existing
opentraces init --agent codex-cli
```

`init` writes `.opentraces.json` and registers machine-local state under
`~/.opentraces/`.

## 4. Inspect The Portable Bucket

Captured traces land in the private bucket first. This is not a public dataset.

```bash
opentraces bucket status
opentraces bucket manifest --json
opentraces bucket verify --sample 100
```

To sync the raw bucket to a private remote:

```bash
opentraces setup bucket
opentraces bucket remote push
opentraces bucket remote status
```

## 5. Search, Map, And Slice Traces

```bash
opentraces trace query --since 7d --cwd
opentraces trace map <trace-id> --bursts
opentraces trace slice <trace-id> --template bursts
opentraces trace get <trace-id>
```

`trace query` returns bounded candidates. `trace map` exposes the trace's
deterministic evidence graph and edit bursts. `trace slice` creates bounded
packets that workflows can turn into rows.

For commit-level provenance:

```bash
opentraces trail blame commit <sha>
opentraces trail blame pr render --base main
opentraces trail graph
opentraces trail track <trace-id>
```

For model context at a decision point:

```bash
opentraces ctx tree <trace-id>
opentraces ctx step <trace-id> 7
opentraces ctx resume <context-node-id>
```

## 6. Create A Dataset Workflow

Datasets are projected rows, not raw trace uploads. Start from a template or a
custom workflow package.

```bash
opentraces workflow templates
opentraces workflow create my-workflow --template skill-command-trajectory-eval-v1
opentraces dataset new my-dataset --workflow ./workflows/my-workflow/
```

Ad-hoc seeding is also available:

```bash
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
```

## 7. Run And Review

```bash
opentraces dataset run my-dataset --dry-run --limit 5
opentraces dataset run my-dataset
opentraces dataset status my-dataset
opentraces dataset review my-dataset --json
opentraces dataset review approve my-dataset <row-id>
opentraces dataset review approve my-dataset --all
```

The legacy `--web` and `--tui` review clients currently return decommission
notices. Use the CLI row review surface until the dataset-scoped UI lands.

## 8. Publish Reviewed Rows

```bash
opentraces dataset remote create my-dataset owner/team-traces --private
opentraces dataset publish my-dataset --check-only
opentraces dataset publish my-dataset
```

`dataset publish` uploads approved rows and contract files to the bound remote
as new shards. It does not publish the raw bucket unless you separately run
`bucket remote push`.

## Next Steps

- [Portable Bucket](/docs/workflow/bucket), raw retained evidence and sync
- [Trace Discovery](/docs/workflow/trace-discovery), query/map/slice/get
- [Trace Trails](/docs/workflow/blame), Git anchors and survival
- [Context Tree](/docs/workflow/context-tree), what the agent saw
- [Dataset Workflows](/docs/workflow/workflow-templates), row projection packages
- [Dataset Rows](/docs/workflow/datasets), review states and schedules
- [Clients & Use Cases](/docs/workflow/consume), context warmup and manual trace capsules
- [Security Tools](/docs/security/tiers), optional default-off tools
