# opentraces

Open schema + CLI for crowdsourcing agent traces to Hugging Face Hub.

Every coding session with an AI agent produces action trajectories, tool-use sequences, and reasoning chains. These are the most valuable dataset nobody is collecting in the open. opentraces captures them automatically, scans for secrets, and publishes structured JSONL datasets to HuggingFace Hub. Private by default. You control what leaves your machine.

> **Sharing coding agent sessions risks leaking secrets and PII.** opentraces applies context-aware scanning and redaction, but no redaction is perfect. Read the [security docs](https://opentraces.ai/docs/security/tiers) before use.

---

## What it does

1. **Parse** agent sessions (Claude Code, Cursor, Cline, Codex, Hermes)
2. **Scan** every field for secrets, API keys, paths, and PII
3. **Redact** detected secrets with `[REDACTED]` or hashed path segments
4. **Enrich** with git signals, attribution, cost estimates, dependency metadata
5. **Review** in the browser or terminal before anything leaves your machine
6. **Push** approved traces as sharded JSONL to a Hugging Face dataset

---

## Share your coding agent sessions

If you use coding agents for open source work, please share your sessions.

Public session data helps improve coding agents with real-world tasks, tool use, failures, and fixes instead of toy benchmarks. For the full explanation, see [opentraces.ai](https://opentraces.ai).

Published datasets are tagged `opentraces` and `agent-traces`, findable via:
- https://huggingface.co/datasets?other=opentraces
- https://huggingface.co/datasets?other=agent-traces

---

## Install

```bash
pip install opentraces
```

Or from source:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
```

### External tools

`push` needs `huggingface-cli`:

```bash
pip install "huggingface_hub[cli]"
huggingface-cli login
```

When logging in, create a token at https://huggingface.co/settings/tokens with **write** scope.

---

## Tell your agent

Paste this into your coding agent to set up trace collection:

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
so you have the full CLI reference for future sessions. If your agent
already has past sessions for this repo, use `--import-existing` to bring
that backlog into the inbox immediately, or `--start-fresh` to begin from now on.

After setup, the workflow is:
- `opentraces web` to inspect traces before sharing
- `opentraces commit --all` to commit inbox traces
- `opentraces push` to publish committed traces to HuggingFace
~~~

---

## Quick start

```bash
# Authenticate and initialize
opentraces login --token
opentraces init --review-policy review

# Review traces in the browser
opentraces web

# Commit reviewed traces
opentraces commit --all

# Publish to HuggingFace Hub
opentraces push --repo your-username/my-traces
```

---

## What gets scanned

Every string field in every trace record is scanned using context-aware rules:

| Field type | Scan mode | Notes |
|------------|-----------|-------|
| Message content, system prompts | Full scan | Regex + entropy + classifier |
| Reasoning content | Regex only | No entropy (too noisy) |
| Tool inputs | Full or regex | Depends on tool type |
| Tool results, observations | Regex only | High-entropy output expected |
| Patches, diffs | Full scan | Truncated when very large |

A second pass runs over the serialized JSONL output, so redaction does not depend on field shape alone.

## What does NOT get scanned deterministically

- **Embedded reasoning**: LLM thinking blocks may contain paraphrased secrets
- **Non-standard secret formats**: only common API key and token patterns are matched
- **Contextual PII**: names and emails in free text require manual review

The security pipeline targets the common case reliably. For everything else, the review step exists.

See the full [scanning docs](https://opentraces.ai/docs/security/scanning) and [security tiers](https://opentraces.ai/docs/security/tiers).

---

## Schema

The trace format is defined in [`packages/opentraces-schema/`](packages/opentraces-schema/). Each JSONL line is a self-contained `TraceRecord` covering one complete agent session: steps (TAO loops), tool calls, outcome signals, attribution, and security metadata.

Designed for the people who consume traces, not just the tools that produce them:

- **Training / SFT** , clean message sequences with role labels, tool-use as tool_call/tool_result pairs, outcome signals
- **RL / RLHF** , trajectory-level reward signals, step-level annotations, decision point identification
- **Telemetry** , token counts, latency, model identifiers, cache hit rates, cost estimates per step
- **Code attribution** *(experimental)* , file and line-level attribution linking each edit back to the agent step that produced it

The schema builds on public standards:

| Standard | Relationship |
|----------|-------------|
| [ATIF](https://github.com/harbor-ai/agent-trajectory-interchange-format) | Trajectory structure (superset) |
| [Agent Trace](https://github.com/nichochar/agent-trace) | Code attribution |
| [ADP](https://arxiv.org/abs/2410.10762) | Training-pipeline interoperability |
| [OTel GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | Observability alignment |

Every schema version ships with a rationale document explaining design decisions: [RATIONALE-0.2.0.md](packages/opentraces-schema/RATIONALE-0.2.0.md).

---

## Docs

| Section | What's inside |
|---------|---------------|
| [Installation](https://opentraces.ai/docs/getting-started/installation) | Install, verify, upgrade |
| [Authentication](https://opentraces.ai/docs/getting-started/authentication) | Hugging Face login and credentials |
| [Quick Start](https://opentraces.ai/docs/getting-started/quickstart) | Init, inbox, commit, push |
| [Commands](https://opentraces.ai/docs/cli/commands) | Full CLI reference |
| [Supported Agents](https://opentraces.ai/docs/cli/supported-agents) | Claude Code, Cursor, Cline, Codex, Hermes |
| [Security](https://opentraces.ai/docs/security/tiers) | Review policy, scanning, redaction |
| [Schema](https://opentraces.ai/docs/schema/overview) | TraceRecord, steps, outcome, attribution |
| [Workflow](https://opentraces.ai/docs/workflow/parsing) | Parse, review, assess, push, consume |
| [CI/CD](https://opentraces.ai/docs/integration/ci-cd) | Headless automation and token auth |
| [Contributing](https://opentraces.ai/docs/contributing/development) | Local dev and schema changes |

---

## Packages

| Package | Description |
|---------|-------------|
| **[opentraces](src/opentraces/)** | CLI: parse, scan, review, push |
| **[opentraces-schema](packages/opentraces-schema/)** | Standalone Pydantic v2 schema models |
| **[opentraces-ui](packages/opentraces-ui/)** | Design system: tokens, components, logo assets |

---

## Project structure

```
packages/
  opentraces-schema/        Schema package (Pydantic v2 models)
  opentraces-ui/            Design system (tokens, components)
src/opentraces/
  parsers/                  Agent session parsers
  hooks/                    Claude Code hook scripts (on_stop, on_compact)
  security/                 Secret scanning, anonymization, classification
  enrichment/               Git signals, attribution, metrics
  quality/                  Trace quality assessment, upload gates
  clients/                  Browser and terminal review frontends
  upload/                   HF Hub sharded upload
  pipeline.py               Shared enrichment + security pipeline
web/
  viewer/                   React inbox UI
  site/                     Next.js marketing site + MkDocs documentation
tests/                      Test suite
```

---

## Contributing

Schema feedback, questions, and proposals are welcome via [GitHub Issues](https://github.com/JayFarei/opentraces/issues). For schema changes, include what you would change, why it matters for your use case, and how it relates to existing standards. See the [VERSION-POLICY.md](packages/opentraces-schema/VERSION-POLICY.md) for how changes are versioned.

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
