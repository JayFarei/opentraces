<p align="center" style="margin-bottom: 0">
  <strong style="font-size: 24px; letter-spacing: -0.02em">open<span style="opacity: 0.5">traces</span></strong>
</p>

<p align="center" style="font-size: 13px; color: #888; margin-top: 4px">
  Open protocol + CLI for crowdsourcing agent traces to HuggingFace Hub.
</p>

---

## Install

```bash
# pip
pip install opentraces

# or homebrew
brew install opentraces
```

## Quick Start

```bash
opentraces init          # pick auto or review mode
opentraces push          # publish to your HF dataset
```

That's it. The session hook captures traces automatically after every Claude Code conversation. Run `opentraces push` when you're ready to share.

## Docs

| Section | What's inside |
|---------|---------------|
| **[Installation](/docs/getting-started/installation)** | pip, brew, verify your install |
| **[Authentication](/docs/getting-started/authentication)** | HF Hub login via device code flow |
| **[Quick Start](/docs/getting-started/quickstart)** | Init, push, and automate in 60 seconds |
| **[Commands](/docs/cli/commands)** | Full CLI reference for every command |
| **[Security Modes](/docs/security/tiers)** | Auto vs review, scanning, redaction |
| **[Schema](/docs/schema/overview)** | TraceRecord, Steps, Attribution, Outcome |
| **[Workflow](/docs/workflow/parsing)** | Parse, review, push lifecycle |
| **[CI/CD](/docs/integration/ci-cd)** | GitHub Actions, headless environments |
| **[Contributing](/docs/contributing/development)** | Dev setup, schema change process |

## Links

- [GitHub](https://github.com/opentraces/opentraces)
- [Schema Rationale](/docs/schema/overview)
- [opentraces.ai](https://opentraces.ai)
