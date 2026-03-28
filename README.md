# opentraces

Open protocol + CLI for crowdsourcing agent traces to HuggingFace Hub. Parses
coding agent sessions, applies configurable security tiers, enriches with git
signals, and publishes as structured JSONL datasets.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
```

## Quick Start

```bash
# Parse and review local Claude Code sessions
opentraces parse
opentraces review

# Upload to HuggingFace Hub
opentraces upload --repo your-username/my-traces
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
[GitHub Issues](https://github.com/opentraces/opentraces/issues). For schema changes,
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
  upload/                     HF Hub sharded upload
  review/                     CLI and web review interfaces
explorer/                     HF Space Gradio app
tests/                        Test suite
```

## License

Apache-2.0
