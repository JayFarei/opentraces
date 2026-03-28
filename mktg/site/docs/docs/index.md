# opentraces Documentation

Open protocol + CLI for crowdsourcing agent traces to HuggingFace Hub.

opentraces parses coding agent sessions, applies configurable security tiers, enriches with git signals and attribution, and publishes as structured JSONL datasets. Your agent traces become training data.

## Quick Start

```bash
pip install opentraces
opentraces login
opentraces init
opentraces parse
opentraces push
```

## Explore the Docs

<!-- cards -->

- **[Getting Started](/docs/getting-started/installation)** - Install, authenticate, and push your first traces in under a minute.
- **[CLI Reference](/docs/cli/commands)** - Complete reference for every opentraces command.
- **[Security](/docs/security/tiers)** - Three tiers: open, guarded, strict. Control what gets uploaded.
- **[Schema](/docs/schema/overview)** - Training-first JSONL format. TraceRecord, Steps, Attribution.
- **[Workflow](/docs/workflow/parsing)** - Parse, review, push. The full trace lifecycle.
- **[Integration](/docs/integration/ci-cd)** - GitHub Actions, headless environments, agent hooks.
- **[Contributing](/docs/contributing/schema-changes)** - Propose schema changes, set up a dev environment.
