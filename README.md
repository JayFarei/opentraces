# opentraces

Open schema + CLI for capturing agent traces, linking them to Git evidence, building local datasets, and publishing reviewed dataset rows to Hugging Face Hub.

Every coding session leaves behind the data you actually want: prompts, tool calls, reasoning, edits, outcome signals, and eventually the code that shipped. opentraces captures that locally, runs layered security passes, exposes Trace Trails over the Git evidence, and lets workflows turn traces into datasets.

> Sharing traces can leak secrets, credentials, internal paths, or customer data. opentraces reduces that risk, but it does not remove it. Read the [security docs](https://opentraces.ai/docs/security/tiers) before you publish anything.

## What It Does

1. Capture traces from supported agents such as Claude Code.
2. Enrich them with task, model, token, dependency, and git metadata.
3. Run regex, entropy, optional TruffleHog, and optional LLM review passes.
4. Search and map retained traces without loading full transcripts.
5. Correlate traces to later commits via Trace Trails, blame, graph, and resume.
6. Run local workflow skills that append schema-valid rows to local datasets.
7. Review dataset rows and publish approved rows to Hugging Face remotes.

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
# one-time machine setup (capture hooks, watcher, HF login, optional tiers)
opentraces setup

# initialize this repo (agents and project enrollment)
opentraces init

# search retained trace evidence
opentraces trace query --lex "bug fix failing test"

# extract bounded trace slices for dataset rows
opentraces trace slice <trace-id> --template bursts --json

# inspect Git-anchored trace evidence
opentraces trail sync --patch <trace_patch_id>

# create and run a workflow-backed local dataset
opentraces dataset new bug-fixes --workflow ./workflows/bug-fix-curator/WORKFLOW.md
opentraces dataset run bug-fixes --dry-run --limit 5

# publish reviewed dataset rows when a remote is bound
opentraces dataset publish bug-fixes --check-only
```

`init` writes the committable marker at `.opentraces.json`. Captured traces, runtime state, and upload bookkeeping stay machine-local under `~/.opentraces/projects/<slug>/`.

Useful follow-ups:

- `opentraces doctor` checks auth, integrations, and pipeline health.
- `opentraces setup auth` logs in to Hugging Face for dataset remotes.
- `opentraces trace query/map/slice/get` searches, maps, slices, and retrieves retained traces.
- `opentraces trail blame <sha>` and `opentraces trail graph` show commit-to-trace attribution (run `opentraces setup git` first to install the post-commit correlator).
- `opentraces trail explain --trace <id> --step <n>` explains Trace Trails evidence rebuilt from the local Git event log.
- `opentraces trail explain <path>:<line>` resolves a Git-side file line back to Trace Patch evidence when an exact anchor exists.
- `opentraces trail sync --patch <id>` synchronizes an anchored Trace Patch with current Git history and reports current `HEAD` survival.
- `opentraces trail timeline <trace-id>` shows the observed Trace Trails timeline for a trace.
- `opentraces trail teleport export/open` moves a trace and retained Git evidence between workspaces.
- `opentraces setup trufflehog` enables Tier 1.5 scanning.
- `opentraces setup llm-review` configures Tier 2 semantic review.
- `opentraces trail resume <trace-id>` reopens the upstream agent session behind a trace.
- `opentraces dataset review/approve/reject/publish` manages dataset row publication.

## Tell Your Agent

Paste this into your coding agent:

~~~
Set up opentraces in this project.

1. Check whether `opentraces --version` works.
   If not, install with `pipx install opentraces`.

2. Run the one-time machine setup:
   `opentraces setup`

   This walks each integration (capture hooks, watcher, HuggingFace login,
   optional TruffleHog, optional LLM review).

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
   - `opentraces trail sync ...`
   - `opentraces dataset run <name>`
   - `opentraces dataset publish <name>`

6. Optional hardening:
   - `opentraces doctor`
   - `opentraces setup trufflehog`
   - `opentraces setup llm-review`
   - `opentraces dataset publish <name> --check-only`

7. Attribution queries (run `opentraces setup git` once to install the post-commit correlator):
   - `opentraces trail blame <sha>`
   - `opentraces trail graph`
~~~

## Security

The built-in pipeline is versioned independently from the CLI and schema (currently `SECURITY_VERSION = 0.3.0`). Run `opentraces doctor --security` to see the exact tiers, versions, and commands active in your install.

| Tier | Name | Status | What it does |
|------|------|--------|--------------|
| 1a | Regex patterns | always on | Built-in secret detectors for known token and key formats |
| 1b | Shannon entropy | always on | Flags high-entropy strings that look like secrets |
| 1.5 | TruffleHog | optional | Local scan for broader secret detection, findings redacted in place |
| 2 | LLM trace review | optional, on demand | Semantic review over the whole trace transcript |
| 3 | Human review | always available | Web inbox, TUI, and CLI review before upload |

See [security tiers](https://opentraces.ai/docs/security/tiers) and [scanning details](https://opentraces.ai/docs/security/scanning).

## Schema

The trace format lives in [`packages/opentraces-schema/`](packages/opentraces-schema/). Each JSONL line is one `TraceRecord`, with:

- task and agent identity
- TAO-loop steps
- tool calls and observations
- token and cost metrics
- outcome signals
- security metadata
- optional attribution and commit correlation data

The schema is a superset of ATIF and borrows ideas from Agent Trace, ADP, and OTel GenAI. Current schema version: `0.3.0`.

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

`opentraces trail explain --trace <id> --step <n>` rebuilds from the local
event log and reports the Trace Snapshot references, Trace Patch identity, Git
Anchor, evidence tier, firmness, source events, and any limitations.
`opentraces trail diff --trace <id> --from-step <a> --to-step <b>` compares
captured snapshot trees and emits the resulting Trace Patch. The delayed Git
Anchor reconciler can search a later commit for existing Trace Patches and
record exact anchors, which can be queried from `--commit <sha>` or
`<path>:<line>`.

`opentraces trail resolve ot://... --json` resolves stable resource paths:
`ot://trace/<trace_id>/patches/<trace_patch_id>/trail`,
`ot://git-anchor/<git_anchor_id>`, and
`ot://file/<path>/line/<n>/origin`. Resolution returns IDs and metadata,
including `containing_segment_id`, without embedding full Trace Slice
content.

`opentraces trail attach --trace <id> --commit <sha>` retroactively
connects a trace's evidence to a Git commit when the post-commit
correlator missed (hook failure, daemon crash, out-of-order
backfill). New events carry `capture_method=["manual_attach"]` so
downstream consumers can distinguish manual from automatic capture.
Source events are byte-identical after attach; the operation is fully
append-only and idempotent.

`opentraces trail rebuild` re-derives advisory snapshot refs from the
canonical event log. The append-only event log is the source of truth;
projections can be dropped and rebuilt without losing replayability,
even after `git gc --prune=now --aggressive`, because batch commits
embed snapshot trees as subtrees so Git GC cannot prune them.

Trace-side history is the append-only sequence of snapshots, patches, searches,
and anchors observed by OpenTraces. Git-side Patch Trails are computed from Git
history and repository state after a patch has a Git Anchor. `opentraces trail
follow --patch <trace_patch_id>` and `--anchor <git_anchor_id>` report bounded
chronological survival observations from each Git Anchor to current `HEAD`;
`current_observations` contains one latest observation per anchor, and
`current_survival` aggregates those latest answers so any alive anchor wins over
later lost anchors. Each observation carries `observation_sequence` (global,
contiguous), `anchor_trail_index` (per-anchor), `observed_commit_time`, and
`anchor_descendant_count` so consumers can sort, group, or compute truncation
gaps without re-walking Git. Use `--history-limit N` to bound how many commits
per anchor are observed (default 500).

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
| Security Tiers | https://opentraces.ai/docs/security/tiers |
| Security Configuration | https://opentraces.ai/docs/security/configuration |
| Security Scanning | https://opentraces.ai/docs/security/scanning |
| Schema Overview | https://opentraces.ai/docs/schema/overview |
| Schema: TraceRecord | https://opentraces.ai/docs/schema/trace-record |
| Schema: Steps | https://opentraces.ai/docs/schema/steps |
| Outcome & Attribution | https://opentraces.ai/docs/schema/outcome-attribution |
| Schema Versioning | https://opentraces.ai/docs/schema/versioning |
| Parsing | https://opentraces.ai/docs/workflow/parsing |
| Inbox & Review | https://opentraces.ai/docs/workflow/review |
| Push | https://opentraces.ai/docs/workflow/pushing |
| Blame & Graph | https://opentraces.ai/docs/workflow/blame |
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
  cli/
  core/
  capture/
  publish/
  enrichment/
  quality/
  security/
  clients/
web/
  viewer/
  site/
  coming-soon/
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
