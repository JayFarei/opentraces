# opentraces

Open protocol + CLI for repo-local agent trace capture, review, and upload to Hugging Face Hub.

## Install

```bash
pip install opentraces
```

```bash
brew install opentraces
```

## Quick Start

```bash
opentraces login
opentraces init --review-policy review --push-policy manual
opentraces web
```

That gives you the browser inbox backed by the React viewer in `web/viewer/`. Review traces with the browser UI or `opentraces tui`, then `opentraces commit` and `opentraces push` when you are ready to publish.

## Docs

| Section | What's inside |
|---------|---------------|
| **[Installation](/docs/getting-started/installation)** | Install, verify, upgrade |
| **[Authentication](/docs/getting-started/authentication)** | Hugging Face login and credentials |
| **[Quick Start](/docs/getting-started/quickstart)** | Init, inbox, commit, push |
| **[Commands](/docs/cli/commands)** | Public and hidden CLI surface |
| **[Security Modes](/docs/security/tiers)** | Review policy, push policy, security tiers |
| **[Schema](/docs/schema/overview)** | TraceRecord, steps, outcome, attribution |
| **[Workflow](/docs/workflow/parsing)** | Parse, review, commit, push lifecycle |
| **[CI/CD](/docs/integration/ci-cd)** | Headless automation and token auth |
| **[Contributing](/docs/contributing/development)** | Local dev and schema changes |

## Links

- [GitHub](https://github.com/opentraces/opentraces)
- [Schema Rationale](/docs/schema/overview)
- [opentraces.ai](https://opentraces.ai)
