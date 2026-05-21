```
  █▀▀█ █▀▀█ █▀▀█ █▀▀▄ ▀█▀ █▀▀▄ █▀▀█ █▀▀▀ █▀▀█ █▀▀▀
  █  █ █  █ █▀▀▀ █  █  █  █▀▀▄ █▀▀█ █    █▀▀▀ ▀▀▀█
  ▀▀▀▀ █▀▀▀ ▀▀▀▀ ▀  ▀  ▀  ▀  ▀ ▀  ▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀
```

Open schema + CLI for capturing agent traces into a private bucket, linking them to Git and context evidence, building workflow-projected datasets, and publishing reviewed dataset rows to Hugging Face Hub.

Every coding session leaves behind the data you actually want: prompts, tool calls, reasoning, edits, outcome signals, and eventually the code that shipped. opentraces captures that locally as raw bucket evidence, exposes Trace Trails for what changed, exposes Context Trees for what the agent saw, and lets workflows turn selected evidence into datasets.

> Sharing traces can leak secrets, credentials, internal paths, or customer data. opentraces reduces that risk, but it does not remove it. Read the [security docs](https://opentraces.ai/docs/security/tiers) before you publish anything.

## What It Does

1. Capture traces from supported agents such as Claude Code and Codex CLI.
2. Store capture-time evidence in a private bucket: `trace.json`, patch history, Trail events, Context Tree events, source events, and content-addressed blobs.
3. Search, map, and slice retained traces without loading full transcripts.
4. Correlate trace patches to Git history via Trace Trails: blame, graph, and track.
5. Reconstruct what the agent saw at a step via Context Tree `ctx` commands.
6. Sync the private bucket to a HuggingFace remote when you explicitly opt in.
7. Run local workflow skills that turn traces into schema-valid dataset rows.
8. Optionally run named security tools in bucket flows or workflows before publishing rows.
9. Review dataset rows and publish approved rows to HuggingFace remotes.

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
# one-time machine setup (capture hooks, watcher, HF login, optional tools)
opentraces setup

# initialize this repo (agents and project enrollment)
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

`init` writes the committable marker at `.opentraces.json`. Captured traces, runtime state, and upload bookkeeping stay machine-local under `~/.opentraces/projects/<slug>/`.

Useful follow-ups:

- `opentraces doctor` checks auth, integrations, and pipeline health.
- `opentraces setup auth` logs in to HuggingFace for dataset remotes.
- `opentraces trace query/map/slice/get` searches, maps, slices, and retrieves retained traces.
- `opentraces trace index rebuild` rebuilds the local Trace Index after capture changes.
- `opentraces trace teleport` moves a trace and retained Git evidence between workspaces.
- `opentraces trail blame commit <sha>` and `opentraces trail graph` show commit-to-trace attribution (run `opentraces setup git` first to install the post-commit correlator).
- `opentraces trail track <trace-id>` walks a trace's lineage through Git history and reports current `HEAD` survival.
- `opentraces ctx tree/show/step/reads/writes/diff/resume` inspects the Context Tree: what the agent saw at a trace step.
- `opentraces setup capture-otlp` and `opentraces capture-otlp start/status/flush` enable the higher-fidelity OTel capture source for Claude Code Context Trees.
- `opentraces bucket status`, `bucket manifest`, `bucket verify`, `bucket repair`, `bucket rebuild`, `bucket prune`, and `bucket prefetch` inspect and maintain the local private trace bucket.
- `opentraces bucket remote push/pull/status/diff` syncs the private bucket with a private HuggingFace bucket remote (S3-backed storage) configured via `opentraces setup bucket`.
- `opentraces trace query/get --remote-bucket` pulls the configured private bucket remote before reading local trace state; `opentraces trace get --remote <owner/repo>` reads a specific HF bucket directly.
- `opentraces bucket replay` replays bucket-exported Trace Trails into a Git repository.
- `opentraces workflow create/list/templates/remove` manages local dataset workflow skill packages.
- `opentraces dataset list/new/run/review/publish/status` manages local datasets and row publication; `opentraces dataset remote create` binds a HuggingFace remote, and `opentraces dataset schedule` controls recurring runs.
- `opentraces security tools list/info` shows the optional security/privacy tool registry.
- `opentraces security sanitize --tools regex,entropy` runs named tools explicitly; `--use-config` runs only tools you have enabled.
- `opentraces setup trufflehog`, `setup privacy-filter`, and `setup llm-review` configure optional security tools/reviewers.
- `opentraces setup upgrade` upgrades the CLI and refreshes the project skill file.

## Tell Your Agent

Paste this into your coding agent:

~~~
Set up opentraces in this project.

1. Check whether `opentraces --version` works.
   If not, install with `pipx install opentraces`.

2. Run the one-time machine setup:
   `opentraces setup`

   This walks each integration (capture hooks, watcher, HuggingFace login,
   optional TruffleHog, optional privacy-filter, optional LLM review).

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

## Security

The security pipeline is versioned independently from the CLI and schema (currently `SECURITY_VERSION = 0.5.0`). The current contract is deliberately simple: all per-record security tools default off, and workflows opt into the named tools they need.

| Tool | Kind | Default | What it does |
|------|------|---------|--------------|
| `regex` | detector | off | Built-in token/key pattern detectors |
| `entropy` | detector | off | High-entropy secret-like strings |
| `trufflehog` | detector | off | Optional deep secret detector, configured with `opentraces setup trufflehog` |
| `privacy_filter` | detector | off | Optional local/HF NER PII detector, configured with `opentraces setup privacy-filter` |
| `llm_pii` | detector | off | Advanced per-field LLM PII detector, configured directly |
| `path_anonymizer` | transformer | off | Rewrites local usernames in filesystem paths |
| `classifier` | judge | off | Heuristic sensitivity verdict without mutating content |

Run `opentraces security tools list` to see the active config, and pipe JSON through `opentraces security sanitize --tools regex,entropy` when a workflow wants explicit sanitization. `--use-config` runs only tools that have been enabled in config.

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

The schema is a superset of ATIF and borrows ideas from Agent Trace, ADP, and OTel GenAI. Current schema version: `0.6.0`. It keeps `TraceRecord` as the spine, adds `Step.context_node_id` and `TraceRecord.context_tree_summary` for Context Tree joins, and makes `TraceRecord.patches[]` the authoritative output set. `Outcome.patch` was removed; clients assemble diffs from `patches[]` and the trace's `trail.jsonl.gz`.

## Trace Trails

Trace Trails are the user-facing evidence chain from a trace step to the Git
history that accepted its patch. The technical substrate is VCS-anchored
lineage: append-only local `TrailEvent` batches under
`refs/opentraces/local/events/v1`, plus rebuildable projections such as CLI
explanations, doctor checks, and later search/dataset views.

The substrate ships exact patch explanations, snapshot diffs, bounded
chronological Patch Trail follow-up, manual attach for hook-failure
recovery, projection rebuild from canonical events, watcher-based
backstop attribution, and adversarial reconciliation across Git
rewrites.

Trace Slices are the bounded local context around a Trace Patch or change
burst: nearby steps, prompts, tools, observations, tests, and map nodes when
known. A Trace Slice is context for audit and later dataset projections, not a
training datum by itself. `opentraces trace slice <trace-id> --template bursts`
materialises one deterministic slice per detected change burst; manual
`--from-step/--to-step`, `--around-step`, and `--around-patch` modes are
available when a workflow needs an explicit window.

The visible Trail commands are `trail blame`, `trail graph`, and
`trail track`. `trail track <trace-id>` walks a trace's lineage through Git
history; pass `--patch <id>` or `--anchor <id>` to track a single Trace
Patch or Git Anchor, `--since 12h` or `--all` for batch JSONL output, and
`--history-limit N` to bound the per-anchor commit walk (default 500). The
walker reports `current_observations` (one latest observation per anchor)
and `current_survival` (any alive anchor wins over later lost anchors),
plus `observation_sequence`, `anchor_trail_index`,
`observed_commit_time`, and `anchor_descendant_count` so consumers can
sort, group, or compute truncation gaps without re-walking Git.

Substrate-level introspection commands stay available for advanced
debugging: `trail explain --trace <id> --step <n>` rebuilds evidence from
the local event log, `trail explain <path>:<line>` resolves a Git-side
file line back to Trace Patch evidence, `trail sync --patch <id>`
synchronizes a Trace Patch against current Git history,
`trail timeline <trace-id>` shows the observed event timeline,
`trail resolve ot://...` resolves stable resource paths
(`ot://trace/<id>/patches/<id>/trail`, `ot://git-anchor/<id>`,
`ot://file/<path>/line/<n>/origin`), `trail attach --trace <id> --commit
<sha>` retroactively connects a trace's evidence to a commit when the
post-commit hook missed, and `trail rebuild` re-derives advisory
snapshot refs from the canonical event log.

Survival states. Phase 4 ships `alive_on_path`, `alive_transformed`,
`reverted`, `lost`, and `unknown`. Phase 5 adds three computed states:
`alive_moved` (rename detection via `git log -M --name-status`),
`partially_preserved` (subset of authored lines survives elsewhere in
the file), and `repaired` (a non-anchor committer touched the
anchored range, detected via `git blame --line-porcelain`). The fourth
Phase 5 state, `orphaned`, is reserved for reference-transaction
observation and is deferred until installed-base demand justifies the
hook surface.

Watcher backstop. Phase 5 ships an agent-agnostic filesystem watcher
event API for `filesystem_mutation_observed` events with `(path,
before_blob, after_blob, observed_at_start, observed_at_end)` —
intentionally no `trace_id` or `step_index`, because attribution is
the reconciler's job, not the watcher's. Production daemon wiring is
deferred. The reconciler consumes observations alongside
`trace_step_window_opened` / `trace_step_window_closed` events shipped
since Phase 2 and produces attribution under unambiguous conditions
only: when the mutation interval is fully inside exactly one writer's
*firm* step window, a `trace_patch_created` event is emitted or upgraded
with `capture_method=["...", "watcher_backstop"]`. Ambiguity is
recorded as a `capture_limitations` tag from the closed Phase 5
vocabulary (`concurrent_writer_overlap`, `unbounded_mutation_window`,
`background_process_overlap`, `hook_only`, `hook_payload_state_mismatch`,
`session_terminated_unexpectedly`, `watcher_buffer_overflow`,
`incomplete_step_window_capture`). The reconciler is idempotent —
re-running on the same event set produces the same attributions, keyed
by `observation_event_id`.

Trail-construction limitations such as `patch_trail_history_truncated`
land in `trail_limitations` at the response root; per-commit lookup
limitations stay on each observation. These are intentionally separate
from the Phase 5 capture-time `capture_limitations` vocabulary on
TrailEvents.

Git rewrite handling. When `git commit --amend`, `rebase`, or `git
reset` followed by re-commit produces a new SHA, the
`supersede_anchors_for_rewrite` substrate emits
`git_anchor_superseded` events tagged
`capture_method=["post_rewrite_hook"]` and re-runs the anchor
correlator against the new commit. Cherry-pick is *not* a rewrite —
both commits coexist, both receive anchors. Wiring the post-rewrite
hook into `.git/hooks/` is follow-up work; the substrate function is
the canonical contract.

Anchor identity tiers. The post-commit correlator tries
`exact_range_hash` first (whitespace-collapsed substring match) and
falls back to `structural_match` (line-level similarity above a 0.85
ratio threshold) when the exact tier fails. Identity is preserved
across formatter divergence — quote-style flips, minor refactors, and
format-then-commit pipelines all anchor — but firmness drops from
`firm` to `provisional` so consumers can filter on confidence.

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
| Dataset Publish | https://opentraces.ai/docs/workflow/pushing |
| Trace Trails | https://opentraces.ai/docs/workflow/blame |
| Private Bucket | https://opentraces.ai/docs/workflow/bucket |
| Context Tree | https://opentraces.ai/docs/workflow/context-tree |
| Trace Discovery | https://opentraces.ai/docs/workflow/trace-discovery |
| Export | https://opentraces.ai/docs/workflow/export |
| Assess | https://opentraces.ai/docs/workflow/quality |
| Consume | https://opentraces.ai/docs/workflow/consume |
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
  cli/                  # Click command groups: trace, trail, bucket, dataset, workflow, setup, ...
  core/                 # Domain glue: config, paths, state, pipeline, datasets, bursts, intent, ...
    trails/             # VCS-anchored Trace Trails substrate (event log, snapshots, anchors, ...)
  capture/              # Inbound boundary: claude_code, hermes, git, fs_watcher, tool_boundary
  publish/              # Outbound boundary: format serializers and HuggingFace publisher
  enrichment/           # Read-only enrichers: git signals, attribution, dependencies, metrics
  quality/              # Trace quality assessment and rubrics
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
