# opentraces

Open protocol + CLI for repo-local agent trace capture, review, and upload to Hugging Face Hub.

## Get Started

### pipx

```bash
pipx install opentraces
```

### brew

```bash
brew install JayFarei/opentraces/opentraces
```

### Copy to your agent

Paste this into your coding agent (Claude Code, Cursor, Codex, etc.):

```
Set up opentraces in this project for trace collection.

Step 1 - Install:
pipx install opentraces

Step 2 - Authenticate:
Run `opentraces auth status` to check if already logged in.
If not authenticated, ask me to run `opentraces login` myself,
I need to authorize in the browser.

Step 3 - Initialize:
Detect which agent you are (Claude Code, Cursor, etc.) and run:
`opentraces init --agent <agent> --review-policy auto --import-existing`

This will:
- set the review policy to auto (traces are captured, sanitized, committed, and pushed automatically)
- create a private dataset on HuggingFace
- install the agent hook so traces are captured at the end of every session
- import any existing sessions from this project

If I want manual review instead, use `--review-policy review`.

Step 4 - Review (if review policy):
Open the inbox to review, commit, redact, or reject traces:
`opentraces tui` or `opentraces web`

Step 5 - Push:
Sync committed traces to the remote dataset:
`opentraces push`

With auto review policy, the hook handles this automatically
at the end of each session, no manual push needed.
```

## Quick Start

```bash
opentraces login
opentraces init
opentraces push
```

`login` authenticates with HuggingFace. `init` creates a private dataset, installs the session hook, and starts capturing traces automatically. `push` uploads committed traces to your dataset.

Open the inbox to review traces before pushing:

### Web inbox

```bash
opentraces web
```

![Web inbox - timeline view](/docs/assets/web-timeline.png)

![Web inbox - review view](/docs/assets/web-review.png)

### Terminal inbox

```bash
opentraces tui
```

![Terminal inbox](/docs/assets/tui.png)

Then `opentraces commit` and `opentraces push` when ready.

## Why

### Contribute to the commons

Your agent traces are the most valuable dataset nobody is collecting. Every coding session produces action trajectories, tool-use sequences, and reasoning chains. When shared as open data, these traces let the tools you use every day improve based on real-world telemetry. Traces are searchable by dependency, so framework maintainers, tool authors, and researchers can find sessions relevant to their stack and build better models.

### Private team analytics

If you have several agents or several team members, commit traces automatically to a shared private dataset during your enterprise hacking sessions. Run downstream analytics, fine-tuning, or evaluation jobs on top of it. One dataset, many consumers, no vendor lock-in, all on HuggingFace.

```python
from datasets import load_dataset

ds = load_dataset("your-org/agent-traces")
```

## Docs

| Section | What's inside |
|---------|---------------|
| **[Installation](/docs/getting-started/installation)** | Install, verify, upgrade |
| **[Authentication](/docs/getting-started/authentication)** | Hugging Face login and credentials |
| **[Quick Start](/docs/getting-started/quickstart)** | Init, inbox, commit, push |
| **[Commands](/docs/cli/commands)** | Public and hidden CLI surface |
| **[Security Modes](/docs/security/tiers)** | Review policy, security pipeline |
| **[Schema](/docs/schema/overview)** | TraceRecord, steps, outcome, attribution |
| **[Workflow](/docs/workflow/parsing)** | Parse, review, commit, push lifecycle |
| **[CI/CD](/docs/integration/ci-cd)** | Headless automation and token auth |
| **[Contributing](/docs/contributing/development)** | Local dev and schema changes |

## Links

- [GitHub](https://github.com/jayfarei/opentraces)
- [Schema Rationale](/docs/schema/overview)
- [opentraces.ai](https://opentraces.ai)

