# opentraces

Open protocol + CLI for crowdsourcing agent traces to HuggingFace Hub. Parses
coding agent sessions, applies security scanning and redaction, enriches with git
signals, and publishes as structured JSONL datasets.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
```

## Tell Your Agent

Paste this into your coding agent:

~~~
I want you to set up opentraces in this project for trace collection.

Step 1 - Install:
pipx install opentraces

Step 2 - Authenticate:
Run `opentraces auth status` to check if already logged in.
If not authenticated, ask me to run `opentraces login --token` myself,
I need to paste a HuggingFace access token with write scope
(from https://huggingface.co/settings/tokens).

Step 3 - Choose agent:
Ask me which coding agent I'm using. Supported agents can be
listed with `opentraces init --help` (the --agent option).
If I'm using you (the agent running this prompt), detect which
agent you are and suggest that. I can pick multiple.

Step 4 - Initialize and install skill:
opentraces init --agent <chosen-agent> --review-policy review --start-fresh

This sets up automatic trace collection with manual review before
anything is shared, and installs the opentraces agent skill into
.agents/skills/opentraces/ (plus a symlink in .<agent>/skills/)
so you have the full CLI reference for future sessions. If Claude Code
already has past sessions for this repo, use `--import-existing` to bring
that backlog into the inbox immediately, or `--start-fresh` to begin from now on.

After setup, the workflow is:
- `opentraces web` to inspect traces before sharing
- `opentraces commit --all` to commit inbox traces
- `opentraces push` to publish committed traces to HuggingFace
~~~

## Quick Start

```bash
# Authenticate with a write-access token and initialize this repo inbox
opentraces login --token
opentraces init --review-policy review

# Review traces in the browser inbox
opentraces web
opentraces commit --all

# Publish committed traces to HuggingFace Hub
opentraces push --repo your-username/my-traces
```

## Schema

The trace format is defined in [`packages/opentraces-schema/`](packages/opentraces-schema/).
Each JSONL line is a self-contained `TraceRecord` covering one complete agent session,
including steps (TAO loops), tool calls, outcome signals, attribution, and security metadata.

The schema builds on public standards:
- [ATIF](https://github.com/harbor-ai/agent-trajectory-interchange-format) for trajectory structure
- [Agent Trace](https://github.com/nichochar/agent-trace) for code attribution
- [ADP](https://arxiv.org/abs/2410.10762) for training-pipeline interoperability
- [OTel GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/) for observability alignment

Every schema version ships with a rationale document explaining the design decisions
behind each model and field. The current rationale is
[RATIONALE-0.1.0.md](packages/opentraces-schema/RATIONALE-0.1.0.md).

## Contributing

Schema feedback, questions, and proposals are welcome via
[GitHub Issues](https://github.com/JayFarei/opentraces/issues). For schema changes,
include what you would change, why it matters for your use case, and how it relates
to existing standards. See the schema
[VERSION-POLICY.md](packages/opentraces-schema/VERSION-POLICY.md) for how changes
are versioned.

## Project Structure

```
packages/opentraces-schema/   Schema package (Pydantic v2 models)
src/opentraces/               CLI package
  parsers/                    Agent session parsers
  security/                   Secret scanning, anonymization, classification
  enrichment/                 Git signals, attribution, metrics
  clients/                    Browser and terminal inbox frontends
  pipeline.py                 Shared enrichment + security pipeline
  upload/                     HF Hub sharded upload
web/viewer/                   React inbox UI
web/site/docs/                Documentation source
tests/                        Test suite
```

## License

MIT
