# opentraces

Open schema + CLI for collecting, reviewing, and publishing agent traces to Hugging Face Hub.

Every coding session leaves behind the data you actually want: prompts, tool calls, reasoning, edits, outcome signals, and eventually the code that shipped. opentraces captures that locally, runs layered security passes, lets you review it, and publishes sharded JSONL datasets in an open schema.

> Sharing traces can leak secrets, credentials, internal paths, or customer data. opentraces reduces that risk, but it does not remove it. Read the [security docs](https://opentraces.ai/docs/security/tiers) before you publish anything.

## What It Does

1. Capture traces from supported agents such as Claude Code.
2. Enrich them with task, model, token, dependency, and git metadata.
3. Run regex, entropy, optional TruffleHog, and optional LLM review passes.
4. Stage traces locally for review in the terminal, browser, or CLI inbox.
5. Publish them as immutable JSONL shards to a Hugging Face dataset.
6. Correlate traces to later commits via `blame`, `graph`, and Trace Trails.
7. Export staged traces to downstream formats such as `atif` and `agent-trace`.
8. Import existing datasets with `opentraces pull --parser hermes`, routed through the same security and staging flow.

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

Both review surfaces (`opentraces web`, `opentraces tui`) ship in the default install, no extras required.

## Quick Start

opentraces has a two-phase bootstrap: `setup` wires the machine once, `init` wires each repo.

```bash
# one-time machine setup (capture hooks, watcher, HF login, optional tiers)
opentraces setup

# initialize this repo (agents, review policy, remote dataset)
opentraces init

# review traces locally
opentraces web
# or: opentraces tui

# stage reviewed traces for upload
opentraces add --all

# publish the staged set
opentraces push
```

`init` writes the committable marker at `.opentraces.json`. Captured traces, runtime state, and upload bookkeeping stay machine-local under `~/.opentraces/projects/<slug>/`.

Useful follow-ups:

- `opentraces doctor` checks auth, integrations, and pipeline health.
- `opentraces blame <sha>` and `opentraces graph` show commit-to-trace attribution (run `opentraces setup git` first to install the post-commit correlator).
- `opentraces trail explain --trace <id> --step <n>` explains Trace Trails evidence rebuilt from the local Git event log.
- `opentraces trail explain <path>:<line>` resolves a Git-side file line back to Trace Patch evidence when an exact anchor exists.
- `opentraces trail diff --trace <id> --from-step <a> --to-step <b>` shows the Trace Patch between captured step snapshots.
- `opentraces trail follow --patch <id>` reports whether an anchored Trace Patch is still alive at the current Git `HEAD`.
- `opentraces setup trufflehog` enables Tier 1.5 scanning.
- `opentraces setup llm-review` configures Tier 2 semantic review.
- `opentraces push --llm-review` gates uploads on a clean Tier 2 verdict.
- `opentraces assess` scores trace quality locally or across a remote dataset.
- `opentraces pull owner/dataset --parser hermes` imports traces from an existing Hugging Face dataset.
- `opentraces log` lists recent pushes grouped by date; `-v` expands to per-trace rows with tokens and cost.
- `opentraces resume <trace-id>` reopens the upstream agent session behind a trace.

## Tell Your Agent

Paste this into your coding agent:

~~~
Set up opentraces in this project.

1. Check whether `opentraces --version` works.
   If not, install with `pipx install opentraces`.

2. Run the one-time machine setup:
   `opentraces setup`

   This walks each integration (capture hooks, watcher, entity parser,
   HuggingFace login, optional TruffleHog, optional LLM review).

3. Confirm authentication:
   `opentraces auth whoami`
   If unauthenticated, use browser login (`opentraces auth login`)
   or token login (`opentraces auth login --token`).

4. Initialize the repo:
   `opentraces init`
   This prompts for agents, review policy, and the HuggingFace remote.

5. After init, the daily workflow is:
   - `opentraces status`
   - `opentraces web` or `opentraces tui`
   - `opentraces add --all`
   - `opentraces push`

6. Optional hardening:
   - `opentraces doctor`
   - `opentraces setup trufflehog`
   - `opentraces setup llm-review`
   - `opentraces push --llm-review`

7. Attribution queries (run `opentraces setup git` once to install the post-commit correlator):
   - `opentraces blame <sha>`
   - `opentraces graph`

8. Import from an existing dataset:
   - `opentraces pull owner/dataset --parser hermes`
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

The initial surface supports exact patch explanations, snapshot diffs, and
current-head Patch Trail follow-up.
`opentraces trail explain --trace <id> --step <n>` rebuilds from the local
event log and reports the Trace Snapshot references, Trace Patch identity, Git
Anchor, evidence tier, firmness, source events, and any limitations.
`opentraces trail diff --trace <id> --from-step <a> --to-step <b>` compares
captured snapshot trees and emits the resulting Trace Patch. The delayed Git
Anchor reconciler can search a later commit for existing Trace Patches and
record exact anchors, which can be queried from `--commit <sha>` or
`<path>:<line>`.

Trace-side history is the append-only sequence of snapshots, patches, searches,
and anchors observed by OpenTraces. Git-side Patch Trails are computed from Git
history and repository state after a patch has a Git Anchor. `opentraces trail
follow --patch <trace_patch_id>` and `--anchor <git_anchor_id>` currently report
one current-`HEAD` survival observation per anchor, ordered by anchor event
sequence; `current_survival` aggregates patch-level answers so any alive anchor
wins over later lost anchors. Phase 4 states are `alive_on_path`,
`alive_transformed`, `reverted`, `lost`, and `unknown`. Rename, partial
preservation, repair, and orphan reasoning are reserved for the Phase 5
reconciler.

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
