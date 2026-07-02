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

- **tracking mode** (`global` by default), so Claude/Codex sessions can
  auto-enroll projects private + review-required the first time capture fires.
  Pi remains explicit-consent per repo.
- **capture hooks** for Claude Code and Codex CLI, plus Pi package checks.
- **shared agent skill install**, which links the opentraces skill into
  supported harnesses such as Claude Code, Codex CLI, and Pi.
- **git hook** and **watcher**, which mature Trace Trails after commits land.
- **bucket remote**, optional private HuggingFace sync for raw retained
  evidence, configured with `bucket connect`. It requires `opentraces auth
  login` first and prompts for a bucket security policy before configuring
  remote sync.
- **HuggingFace login**, needed for bucket sync and dataset remotes.
- **optional security tools**, such as TruffleHog, privacy-filter, and
  LLM review. Per-record tools default off until a workflow or config enables
  them.

You can run specific setup commands non-interactively:

```bash
opentraces setup claude-code
opentraces setup codex-cli
opentraces setup pi
opentraces setup git
opentraces bucket connect
opentraces setup capture-otlp
opentraces setup skill
opentraces setup trufflehog
opentraces setup privacy-filter
```

For Codex, install and authenticate Codex CLI first. `setup codex-cli` wires
terminal Codex CLI hooks only; it does not cover Codex Desktop. For Pi, install
`opentraces-pi` with `pi install npm:opentraces-pi`; `setup pi --dry-run --json`
shows the local package/checklist plan without doing Python/service/auth setup.
Inside Pi, `/ot-capture-status` and `/ot-setup` expose the same local-first
checklist.

## 3. Enroll A Project

Under global tracking (the default) this is optional for every agent, including
Pi, which auto-enrolls each project on first capture. Running `init` explicitly
is still useful when you want to import existing sessions, be explicit about the
connected agent, or are running in `manual` tracking mode.

```bash
opentraces init
opentraces init --agent claude-code --import-existing
opentraces init --agent codex-cli
opentraces init --agent pi
```

`init` writes `.opentraces.json` and registers machine-local state under
`~/.opentraces/`.

`--import-existing` currently backfills Claude Code sessions for the current
repo. Codex CLI and Pi capture start with sessions run after their runtime setup
and project enrollment are in place.

## 4. Inspect The Portable Bucket

Captured traces land in the private bucket first. This is not a public dataset.

```bash
opentraces bucket status
opentraces bucket list --json
opentraces bucket verify --sample 100
```

To sync the raw bucket to a private remote, authenticate first. `bucket connect`
exits with a `run 'opentraces auth login'` hint until you do, and it prompts for
a bucket security policy that protects raw captured evidence before remote sync:

```bash
opentraces auth login
opentraces bucket connect
opentraces bucket security policy --policy recommended
opentraces bucket sync push
opentraces bucket sync status
```

## 5. Search, Map, And Slice Traces

```bash
opentraces trace query --since 7d --cwd
opentraces trace query --skill my-skill --json
opentraces trace map <trace-id> --bursts
opentraces trace slice <trace-id> --template bursts
opentraces trace get <trace-id>
```

`trace query` returns bounded candidates. `trace map` exposes the trace's
deterministic evidence graph and edit bursts. `trace slice` creates bounded
packets that workflows can turn into rows.

For deterministic signals about how a run went (no LLM, nothing persisted):

```bash
opentraces trace get <trace-id> --waste --json       # where context burned
opentraces trace get <trace-id> --run-intel --json   # resteer/recovery/loop signals
opentraces trace compare <trace-a> <trace-b> --json  # compare two runs
```

For commit-level provenance:

```bash
opentraces trail blame commit <sha>
opentraces trail pr render --base main
opentraces trail graph
opentraces trail track <trace-id>
```

For model context at a decision point:

```bash
opentraces ctx <trace-id>
opentraces ctx <trace-id>:7
opentraces ctx <trace-id>:7 --layer messages
opentraces ctx resume <context-node-id>
```

## 6. Create A Dataset Workflow

Datasets are projected rows, not raw trace uploads. Start from a template or a
custom workflow package.

```bash
opentraces workflow templates
opentraces workflow create my-workflow --template skill-command-trajectory-eval-v1
opentraces dataset new my-dataset --workflow ./workflows/my-workflow/
opentraces dataset new opentraces-episodes --from-skill opentraces
```

Ad-hoc seeding is also available:

```bash
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
```

## 7. Run And Review

```bash
opentraces dataset run my-dataset --dry-run --limit 5
opentraces dataset run my-dataset
opentraces dataset run opentraces-episodes --executor script --json
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
`bucket sync push`.

## Next Steps

- [Portable Bucket](/docs/workflow/bucket), raw retained evidence and sync
- [Trace Discovery](/docs/workflow/trace-discovery), query/map/slice/get
- [Trace Trails](/docs/workflow/blame), Git anchors and survival
- [Context Tree](/docs/workflow/context-tree), what the agent saw
- [Dataset Workflows](/docs/workflow/workflow-templates), row projection packages
- [Dataset Rows](/docs/workflow/datasets), review states and schedules
- [Clients](/docs/clients/overview), consumers and bucket evidence
- [Agent Workflows](/docs/clients/agent-workflows), context warmup and session-history search
- [Trace Capsule](/docs/clients/trace-capsule), privacy-bounded agent usage episodes
- [Security Tools](/docs/security/tiers), optional default-off tools
