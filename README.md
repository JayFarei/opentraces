# opentraces

Open schema + CLI for collecting, reviewing, and publishing agent traces to Hugging Face Hub.

Every coding session leaves behind the data you actually want: prompts, tool calls, reasoning, edits, outcome signals, and eventually the code that shipped. opentraces captures that locally, runs layered security passes, lets you review it, and publishes sharded JSONL datasets in an open schema.

> Sharing traces can leak secrets, credentials, internal paths, or customer data. opentraces reduces that risk, but it does not remove it. Read the [security docs](https://opentraces.ai/docs/security/tiers) before you publish anything.

## What It Does

1. Capture traces from supported agents such as Claude Code.
2. Enrich them with task, model, token, dependency, and git metadata.
3. Run regex, entropy, optional TruffleHog, and optional LLM review passes.
4. Stage traces locally for review in the terminal or browser inbox.
5. Publish them as immutable JSONL shards to a Hugging Face dataset.
6. Correlate traces to later commits with an optional post-commit hook.
7. Export staged traces to downstream formats such as `agent-trace`.

## Install

Preferred end-user install:

```bash
pipx install opentraces
```

Homebrew:

```bash
brew install JayFarei/opentraces/opentraces
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

```bash
# authenticate
opentraces auth login

# initialize this repo
opentraces init --agent claude-code --review-policy review --start-fresh

# review traces locally
opentraces web
# or: opentraces tui

# stage reviewed traces for upload
opentraces add --all

# publish the staged set
opentraces push
```

Useful follow-ups:

- `opentraces doctor` checks auth, integrations, and pipeline health.
- `opentraces setup git` installs commit correlation for `graph` and `blame`.
- `opentraces setup trufflehog` enables Tier 1.5 scanning.
- `opentraces setup llm-review` configures Tier 2 semantic review.
- `opentraces push --llm-review` gates uploads on a clean Tier 2 verdict.

## Tell Your Agent

Paste this into your coding agent:

~~~
Set up opentraces in this project.

1. Check whether `opentraces --version` works.
   If not, install with `pipx install opentraces`.

2. Check authentication with `opentraces auth whoami`.
   If unauthenticated, ask me whether to use browser login (`opentraces auth login`)
   or token login (`opentraces auth login --token`).

3. Detect which agent you are and initialize the repo:
   `opentraces init --agent <agent> --review-policy review --start-fresh`

4. After init, use this workflow:
   - `opentraces status`
   - `opentraces web` or `opentraces tui`
   - `opentraces add --all`
   - `opentraces push`

5. Optional hardening:
   - `opentraces doctor`
   - `opentraces setup trufflehog`
   - `opentraces setup llm-review`
   - `opentraces push --llm-review`

6. Optional git correlation:
   - `opentraces setup git`
   - `opentraces graph`
   - `opentraces blame <sha>`
~~~

## Security

The built-in pipeline is versioned independently from the CLI and schema.

- Tier 1a: regex detectors, always on.
- Tier 1b: entropy scan, always on.
- Tier 1.5: TruffleHog, opt-in. Findings are redacted in place and force review.
- Tier 2: optional LLM semantic review, run on demand with `opentraces llm-review`.
- Human review: the browser, TUI, or CLI inbox.

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

## Docs

| Section | Link |
|---------|------|
| Installation | https://opentraces.ai/docs/getting-started/installation |
| Authentication | https://opentraces.ai/docs/getting-started/authentication |
| Quick Start | https://opentraces.ai/docs/getting-started/quickstart |
| Commands | https://opentraces.ai/docs/cli/commands |
| Security | https://opentraces.ai/docs/security/tiers |
| Schema | https://opentraces.ai/docs/schema/overview |
| Workflow | https://opentraces.ai/docs/workflow/parsing |
| Integration | https://opentraces.ai/docs/integration/ci-cd |
| Contributing | https://opentraces.ai/docs/contributing/development |

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
