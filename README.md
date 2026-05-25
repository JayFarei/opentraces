```
  █▀▀█ █▀▀█ █▀▀█ █▀▀▄ ▀█▀ █▀▀▄ █▀▀█ █▀▀▀ █▀▀█ █▀▀▀
  █  █ █  █ █▀▀▀ █  █  █  █▀▀▄ █▀▀█ █    █▀▀▀ ▀▀▀█
  ▀▀▀▀ █▀▀▀ ▀▀▀▀ ▀  ▀  ▀  ▀  ▀ ▀  ▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀
```

Open schema + CLI for capturing agent traces into a private bucket, linking them to Git and context evidence, building workflow-projected datasets, and publishing reviewed dataset rows to Hugging Face Hub.

Every coding session leaves behind the data you actually want: prompts, tool calls, reasoning, edits, outcome signals, and eventually the code that shipped. opentraces captures that locally as raw bucket evidence, links each change to the Git history that accepted it, reconstructs what the agent saw at each step, and lets workflows turn selected evidence into datasets.

> Sharing traces can leak secrets, credentials, internal paths, or customer data. opentraces reduces that risk, but it does not remove it. Read the [security docs](https://opentraces.ai/docs/security/tiers) before you publish anything.

## What It Does

1. Capture traces from supported agents (Claude Code and Codex CLI) via session hooks.
2. Store capture-time evidence in a private bucket: `trace.json`, patch history, Trail events, Context Tree events, source events, and content-addressed blobs.
3. Search, map, and slice retained traces without loading full transcripts.
4. Correlate trace patches to Git history via Trace Trails: blame, graph, and track.
5. Reconstruct what the agent saw at a step via the Context Tree.
6. Sync the private bucket to a HuggingFace remote when you explicitly opt in.
7. Run local workflow skills that turn traces into schema-valid dataset rows.
8. Run named security tools over records before they leave the bucket.
9. Review dataset rows and publish approved rows to HuggingFace remotes.

## Concepts

opentraces is organized around a small set of subsystems. Knowing the boundaries makes the CLI predictable.

| Subsystem | What it holds | Primary commands |
|-----------|---------------|------------------|
| **Capture** | Inbound boundary: agent hooks, the attribution watcher, optional OTLP receiver | `setup`, `init`, `capture-otlp` |
| **Bucket** | Private, local-first store of raw captured evidence (one self-sufficient unit per trace) | `bucket`, `ctx list/info` |
| **Trace** | Search, map, and slice projections over retained traces | `trace query/map/slice/get` |
| **Trail** | VCS-anchored lineage from a trace patch to the commit that accepted it | `trail blame/graph/track` |
| **Context Tree** | What the LLM saw at each step (system, messages, tools, runtime state) | `ctx tree/show/reads/writes/...` |
| **Workflows + Datasets** | Workflow skills that project bucket traces into reviewable HF dataset rows | `workflow`, `dataset` |
| **Security** | Per-record detectors, transformers, and judges run before publication | `security`, `setup <tool>` |

A **bucket** holds raw captured traces; a **dataset** holds workflow-projected rows. They are distinct stores.

What one coding session leaves behind, layered across the substrates:

```text
opentraces <capture> · what happened between an agent and its environment

                ◄─ start ··········· session ··········· end ─►    patch trail ─►
                                                                    t+n      t+n+{}
  Git   │ attribution    HEAD ◇──────────────────────────────►  commit     ◇──◇
        │ across runs                                            └ anchor    survival
  ──────┼─────────────────────────────────────────────────────────────────────────
  Trail │ agent changes  snap▢ ····◌ patch ·······◌ patch ····▢  git_anchor_id
    ▲   │ to the env                                             trace_patch_id
  change│                                                        commit_sha · evidence
  ──────┼─────────────────────────────────────────────────────────────────────────
  Trace │ agent          ▮     ●      ●      ◍      ●      ●
        │ trajectory     user   read   read   agent  write  write
 observe│                └──────────── bash ───────────┘
    ▼   │
  ──────┼─────────────────────────────────────────────────────────────────────────
  Ctx   │ agent          ▤     ▤▤     ▤▤▤    ▤▤▤    ▤▤▤▤   ▤▤▤▤
        │ observations   (context the LLM saw, growing per step)
  ──────┼─────────────────────────────────────────────────────────────────────────
  LLM   │ ingest/produce ─────────────────────────────────────►  $ in / $$ out
```

Changes flow up to the Trail and are anchored to the commits that accepted them; observations flow down to the Context Tree as what the LLM actually saw.

## Pipeline

Capture sources write into the local bucket; workflows project bucket traces into reviewable rows; approved rows publish to HuggingFace remotes. Everything above the dashed line is local; everything below is opt-in remote.

```text
opentraces <pipeline> · traces become datasets

   capture sources       bucket (local)          workflow           dataset
  ┌────────────┐        ┌──────────────┐      ┌───────────┐      ┌───────────┐
  │ OT watcher │↻       │ manifest.json│      │ search API│      │ ▤▤▤▤▤▤▤▤▤ │
  │ agent hooks│─write─►│ traces/v1/   │◄────►│ security  │─run─►│ ✓ approve │
  │ OTel       │↻       │ blobs/v1/    │search│ custom    │ sync │ ✗ reject  │
  └────────────┘        │ events/v1/   │ sync └───────────┘      └───────────┘
        │ git           └──────┬───────┘            │                  │
        └── attribute ─────────┘                    │                  │
  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ sync ╌╌╌╌╌╌ │ ╌╌╌╌╌ run ╌╌╌╌╌╌╌╌ │ ╌╌╌╌ push ╌╌╌╌╌╌ │ ╌╌  local
                               ▼                      ▼                  ▼    remote
                         HF bucket ──────────►   ML Intern   ◄──── HF Hub dataset
                                                     └─ eval / training / scoring ─┘
```

## Install

Preferred end-user install:

```bash
pipx install opentraces
```

Homebrew:

```bash
brew install JayFarei/opentraces/opentraces
```

skills.sh (installs the opentraces skill so your coding agent can drive the workflow):

```bash
npx skills add jayfarei/opentraces
```

From source:

```bash
git clone https://github.com/JayFarei/opentraces
cd opentraces
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
```

Use plain `pip install opentraces` only in CI or disposable environments.

## Quick Start

opentraces has a two-phase bootstrap: `setup` wires the machine once, `init` wires each repo.

```bash
# one-time machine setup — interactive wizard over every integration
# (capture hooks, attribution watcher, HF login, optional security tools)
opentraces setup

# initialize this repo (agent enrollment + committable marker)
opentraces init

# search retained trace evidence
opentraces trace query --lex "bug fix failing test"

# extract bounded trace slices for dataset rows
opentraces trace slice <trace-id> --template bursts --json

# walk Git-anchored trace lineage
opentraces trail track <trace-id>

# inspect or sync the private bucket of captured traces
opentraces bucket status
opentraces bucket remote push

# create and run a workflow-backed local dataset
opentraces dataset new bug-fixes --workflow ./workflows/bug-fix-curator/WORKFLOW.md
opentraces dataset run bug-fixes --dry-run --limit 5

# publish reviewed dataset rows when a remote is bound
opentraces dataset publish bug-fixes --check-only
```

`init` writes the committable marker at `.opentraces.json`. Captured traces, bucket state, and upload bookkeeping stay machine-local under `~/.opentraces/`.

`opentraces doctor` checks auth, integrations, and pipeline health at any time.

## Capture

`opentraces setup` runs an interactive wizard; each integration is also a direct subcommand:

- `setup claude-code` / `setup codex-cli` install session-capture hooks. Run `opentraces init --agent <name>` inside each repo you want to capture. (Codex Desktop is not covered.)
- `setup git` installs a post-commit hook that correlates each commit to the trace that produced it (via `refs/notes/opentraces`), powering `trail blame`.
- `setup watcher` installs a background **attribution watcher** (launchd on macOS, systemd `--user` on Linux; offered by default in the wizard). On an interval it walks enlisted projects, observes filesystem mutations as a backstop for writes hooks miss, reconciles them against open step windows, and matures Trace Trails over time. The watcher is polling-based today; a real-time (inotify/watchdog) observer that would narrow attribution windows is deferred. Subcommands: `install/start/stop/status/tick`.
- `setup capture-otlp` patches `~/.claude/settings.json` so Claude Code emits OpenTelemetry, enabling the higher-fidelity Context Tree capture source. Control the receiver with `capture-otlp start/stop/restart/status/flush`.
- `setup skill` installs the shared agent skill into Claude Code and Codex CLI harness skill directories.
- `setup auth` logs in to HuggingFace for dataset and bucket remotes.
- `setup upgrade` upgrades the CLI and refreshes the project skill file.

## Trace

The trace surface returns bounded projections over a local BM25 + semantic Trace Index, so you can search and slice without loading full transcripts.

- `trace query` returns bounded candidate packets; `trace map` returns a deterministic Trace Map; `trace get` resolves a trace, trace unit, map node, or `ot://` Trail resource.
- `trace slice <trace-id> --template bursts` materializes one deterministic slice per detected change burst. Manual `--from-step/--to-step`, `--around-step`, and `--around-patch` windows are available when a workflow needs an explicit range. A Trace Slice is context for audit and later dataset projection, not a training datum by itself.
- `trace index rebuild` rebuilds the local Trace Index after capture changes; `trace teleport` moves a trace and its retained Git evidence between workspaces.

A *trace patch* is one Edit/Write tool call (roughly one hunk on one file). A *change burst* clusters nearby patches by step proximity.

## Trail

Trace Trails are the evidence chain from a trace step to the Git history that accepted its patch. The substrate is VCS-anchored lineage: append-only `TrailEvent` batches under `refs/opentraces/local/events/v1`, plus rebuildable projections (CLI explanations, doctor checks, search/dataset views).

Visible commands:

- `trail blame commit <sha>` attributes a commit's lines back to the traces that authored them. `trail blame pr render | create | update` projects that blame across a branch range into a GitHub PR body (deterministic synthesis, no LLM) and wraps `gh` for idempotent create-or-update.
- `trail graph` renders commit + trace history.
- `trail track <trace-id>` walks a trace's lineage through Git history and reports `HEAD` survival. Pass `--patch`/`--anchor` to track one patch or anchor, `--since`/`--all` for batch JSONL, and `--history-limit N` to bound the per-anchor walk.

Survival states reported per anchor: `alive_on_path`, `alive_transformed`, `alive_moved`, `partially_preserved`, `repaired`, `reverted`, `lost`, `unknown`. Anchor identity is tiered: an exact range hash first, then a structural (line-similarity) fallback, so identity survives format-then-commit pipelines (firmness drops `firm` → `provisional`). Substrate commands (`explain`, `sync`, `timeline`, `resolve`, `attach`, `rebuild`, `diff`, `resume`, `snapshots`) remain callable for scripting and debugging but are hidden from `--help`. See the [Trace Trails docs](https://opentraces.ai/docs/workflow/blame) for the full model.

## Context Tree

The Context Tree captures what the LLM actually saw at each step of a session: `system`, `messages`, `tool_registry`, and `runtime_state` layers, content-addressed and joined to the trace via `Step.context_node_id` and `TraceRecord.context_tree_summary`.

`opentraces ctx tree/show/step/reads/writes/diff/compactions/prune/resume/resolve/anchor-for-step` navigate it. `ctx list` and `ctx info` read the bucket manifest with zero blob loads.

Two capture sources feed the same substrate. The JSONL parser (harness-side) ships shared session-level layers per node — walk-back to "what did the LLM see at step 7" is a session-level approximation in that path. The OTLP receiver (`setup capture-otlp`) closes the assembled-system-prompt, tool-schema, and sampling-params gaps for sessions captured over OpenTelemetry. See the [Context Tree docs](https://opentraces.ai/docs/workflow/context-tree).

## Bucket

Every captured trace lands in a local-first private bucket under `~/.opentraces/bucket/`: a per-trace envelope (`trace.json` plus gzip-deterministic Trail/context/source companions), content-addressed blobs, a canonical event-log mirror, and a top-level manifest. The bucket is the self-sufficient unit — read verbs accept `--remote <hf-repo>` for symmetric local/remote access.

- `bucket status`, `bucket manifest`, `bucket verify`, `bucket repair`, `bucket rebuild`, `bucket prune`, `bucket prefetch` inspect and maintain the local bucket.
- `setup bucket` opts into remote-by-default sync against a private (S3-backed) HuggingFace bucket remote, reusing existing HF auth.
- `bucket remote push/pull/diff/status` syncs (push order: blobs → events → envelopes → manifest); `bucket replay --repo` reconstructs the canonical Git event ref byte-identically.

## Workflows and Datasets

A dataset is a workflow-driven row projection over one or more bucket traces.

- `workflow create/list/templates/remove` manages local dataset workflow skill packages. The bundled `skill-command-trajectory-eval-v1` template is materialized with `workflow create --template <name>`.
- `dataset new <name> --workflow <path>` creates the manifest; `dataset run` executes the workflow (dry-run, current-agent, or headless); `dataset review/approve/reject` controls per-row publication state; `dataset remote create` binds a HuggingFace dataset remote; `dataset publish` ships approved rows; `dataset schedule` controls recurring runs; `dataset status/list/remove` round out the surface.

## Security

The security pipeline is versioned independently from the CLI and schema (currently `SECURITY_VERSION = 0.5.0`). The contract is deliberately simple: all per-record security tools default off, and workflows opt into the named tools they need.

| Tool | Kind | Default | What it does |
|------|------|---------|--------------|
| `regex` | detector | off | Built-in token/key pattern detectors |
| `entropy` | detector | off | High-entropy secret-like strings |
| `trufflehog` | detector | off | Optional deep secret detector, configured with `opentraces setup trufflehog` |
| `privacy_filter` | detector | off | Optional local/HF NER PII detector, configured with `opentraces setup privacy-filter` |
| `llm_pii` | detector | off | Advanced per-field LLM PII detector, configured directly |
| `path_anonymizer` | transformer | off | Rewrites local usernames in filesystem paths |
| `classifier` | judge | off | Heuristic sensitivity verdict without mutating content |

Run `opentraces security tools list` to see the active config, and pipe JSON through `opentraces security sanitize --tools regex,entropy` when a workflow wants explicit sanitization. `--use-config` runs only tools you have enabled. Session-level LLM review (`opentraces dataset review`) is a separate, on-demand publication gate, not a per-record tool.

See [security tools](https://opentraces.ai/docs/security/tiers) and [scanning details](https://opentraces.ai/docs/security/scanning).

## Schema

The trace format lives in [`packages/opentraces-schema/`](packages/opentraces-schema/). Each JSONL line is one `TraceRecord`, with:

- task and agent identity
- TAO-loop steps
- tool calls and observations
- token and cost metrics
- outcome signals
- security metadata
- optional attribution and commit correlation data

The schema is a superset of ATIF and borrows ideas from Agent Trace, ADP, and OTel GenAI. Current schema version: `0.6.0`. `TraceRecord` is the spine; `Step.context_node_id` and `TraceRecord.context_tree_summary` are the Context Tree join keys, and `TraceRecord.patches[]` is the authoritative output set. `Outcome.patch` was removed; clients assemble diffs from `patches[]` and the trace's `trail.jsonl.gz`.

## Tell Your Agent

Paste this into your coding agent:

~~~
Set up opentraces in this project.

1. Check whether `opentraces --version` works.
   If not, install with `pipx install opentraces`.

2. Run the one-time machine setup:
   `opentraces setup`

   This walks each integration (capture hooks, attribution watcher,
   HuggingFace login, optional TruffleHog, optional privacy-filter,
   optional LLM review).

3. Confirm authentication:
   `opentraces auth whoami`
   If unauthenticated, use browser login (`opentraces auth login`)
   or token login (`opentraces auth login --token`).

4. Initialize the repo:
   `opentraces init`
   This enrolls the project. Dataset remotes and review policy live under
   `opentraces dataset ...`.

5. After init, the daily workflow is:
   - `opentraces status`
   - `opentraces trace query ...`
   - `opentraces trail track <trace-id>`
   - `opentraces dataset run <name>`
   - `opentraces dataset publish <name>`

6. Optional hardening:
   - `opentraces doctor`
   - `opentraces setup trufflehog`
   - `opentraces setup llm-review`
   - `opentraces dataset publish <name> --check-only`

7. Attribution queries (run `opentraces setup git` once to install the post-commit correlator):
   - `opentraces trail blame commit <sha>`
   - `opentraces trail graph`
   - `opentraces trail track <trace-id>`

8. Private bucket sync (optional):
   - `opentraces setup bucket` to configure the remote-by-default private bucket
   - `opentraces bucket status` to inspect local bucket health
   - `opentraces bucket remote push/pull` to sync with the configured remote
~~~

## Docs

| Section | Link |
|---------|------|
| Installation | https://opentraces.ai/docs/getting-started/installation |
| Authentication | https://opentraces.ai/docs/getting-started/authentication |
| Quick Start | https://opentraces.ai/docs/getting-started/quickstart |
| Commands | https://opentraces.ai/docs/cli/commands |
| Supported Agents | https://opentraces.ai/docs/cli/supported-agents |
| Troubleshooting | https://opentraces.ai/docs/cli/troubleshooting |
| Security Tools | https://opentraces.ai/docs/security/tiers |
| Security Configuration | https://opentraces.ai/docs/security/configuration |
| Security Scanning | https://opentraces.ai/docs/security/scanning |
| Schema Overview | https://opentraces.ai/docs/schema/overview |
| Schema: TraceRecord | https://opentraces.ai/docs/schema/trace-record |
| Schema: Steps | https://opentraces.ai/docs/schema/steps |
| Outcome & Attribution | https://opentraces.ai/docs/schema/outcome-attribution |
| Schema Versioning | https://opentraces.ai/docs/schema/versioning |
| Parsing | https://opentraces.ai/docs/workflow/parsing |
| Dataset Row Review | https://opentraces.ai/docs/workflow/review |
| Publish | https://opentraces.ai/docs/workflow/pushing |
| Trace Trails | https://opentraces.ai/docs/workflow/blame |
| Portable Bucket | https://opentraces.ai/docs/workflow/bucket |
| Context Tree | https://opentraces.ai/docs/workflow/context-tree |
| Trace Discovery | https://opentraces.ai/docs/workflow/trace-discovery |
| Dataset Workflows | https://opentraces.ai/docs/workflow/workflow-templates |
| Clients | https://opentraces.ai/docs/clients/overview |
| Agent Workflows | https://opentraces.ai/docs/clients/agent-workflows |
| Trace Capsule | https://opentraces.ai/docs/clients/trace-capsule |
| Agent Setup | https://opentraces.ai/docs/integration/agent-setup |
| CI/CD | https://opentraces.ai/docs/integration/ci-cd |
| Post-Processor Contract | https://opentraces.ai/docs/integration/post-processor-contract |
| Contributing | https://opentraces.ai/docs/contributing/development |
| Schema Changes | https://opentraces.ai/docs/contributing/schema-changes |

## Packages

| Package | Description |
|---------|-------------|
| [`src/opentraces/`](src/opentraces/) | CLI, capture, review, publish, security, enrichment |
| [`packages/opentraces-schema/`](packages/opentraces-schema/) | Standalone Pydantic schema package |
| [`packages/opentraces-ui/`](packages/opentraces-ui/) | Shared design tokens and UI primitives |

## Project Layout

```text
packages/
  opentraces-schema/
  opentraces-ui/
src/opentraces/
  cli/                  # Click command groups: trace, trail, ctx, bucket, dataset, workflow, setup, ...
  core/                 # Domain glue: config, paths, state, pipeline, datasets, bursts, intent, ...
    trails/             # VCS-anchored Trace Trails substrate (event log, snapshots, anchors, ...)
    context_tree/       # Context Tree substrate (layers, nodes, ctx projections)
  capture/              # Inbound boundary: claude_code, codex_cli, hermes, git, fs_watcher, otlp, tool_boundary
  watcher/              # Background attribution daemon (launchd/systemd polling worker)
  publish/              # Outbound boundary: format serializers and HuggingFace publisher
  enrichment/           # Read-only enrichers: git signals, attribution, dependencies, metrics
  quality/              # Parser gates and publication scoring helpers
  security/             # Secret scanning, anonymization, classification
  clients/              # Legacy TUI/web review clients (currently decommissioned)
  workflow_templates/   # Bundled dataset workflow skill templates
web/
  viewer/               # Legacy React trace review UI (currently decommissioned)
  site/                 # Next.js marketing site
  coming-soon/          # Static coming-soon page (Vercel)
skill/                  # Claude Code skill definition (skills.sh convention)
tests/
```

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
