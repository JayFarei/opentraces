# opentraces

Open schema + CLI for capturing coding-agent traces, reviewing them locally, and publishing structured datasets to Hugging Face Hub.

opentraces is built around a simple rule: capture locally, review locally, push explicitly. The tool parses agent sessions into a stable schema, runs layered security scanning and redaction, enriches each trace with git and attribution signals, and uploads sharded JSONL to a dataset you control.

## What 0.3 Adds

- A flatter public CLI: `add`, `push`, `list`, `show`, `status`, `web`, `tui`, `remote`, `auth`, `setup`, `doctor`
- Repo marker plus machine-local storage: `<repo>/.opentraces.json` and `~/.opentraces/projects/<slug>/...`
- Better review surfaces in both the web viewer and TUI
- Explicit remote management for Hugging Face datasets
- Post-commit git correlation for `opentraces blame`
- Optional Tier 1.5 TruffleHog and Tier 2 `llm-review`
- Deterministic quality scoring with optional dataset-card badges

## Workflow

```bash
pipx install opentraces
opentraces auth login
opentraces init
opentraces web          # or: opentraces tui
opentraces add --all
opentraces push
```

`init` writes the committable project marker at `.opentraces.json`. Captured traces, runtime state, and upload bookkeeping stay machine-local under `~/.opentraces/projects/<slug>/`.

## What You Get

**For individual developers.** A local inbox for reviewing traces before upload, plus a standard dataset format you can publish privately or publicly.

**For teams.** Shared remotes on Hugging Face, explicit review policy per repo, and deterministic upload shards that never append in place.

**For dataset consumers.** A schema designed for training, evaluation, analytics, and attribution rather than a raw dump of vendor-specific logs.

## Schema Design

The [schema](/docs/schema/overview) is a standalone package and the contract between capture, review, export, and downstream consumers.

- Training: normalized steps, tool calls, observations, reasoning, outcomes
- Analytics: token counts, cost estimates, timing, cache behavior
- Attribution: git links and file or line provenance when available
- Interop: export paths for ATIF and Agent Trace style consumers

## Start Here

| Section | What's inside |
|---------|---------------|
| **[Installation](/docs/getting-started/installation)** | Install, verify, upgrade, uninstall |
| **[Authentication](/docs/getting-started/authentication)** | OAuth, PATs, `HF_TOKEN`, auth precedence |
| **[Quick Start](/docs/getting-started/quickstart)** | Initialize a repo, review traces, upload your first shard |
| **[Commands](/docs/cli/commands)** | Current 0.3 command reference |
| **[Inbox & Review](/docs/workflow/review)** | Web viewer, TUI, and CLI review loop |
| **[Push](/docs/workflow/pushing)** | Upload behavior, remotes, visibility, migration, quality badges |
| **[Security Tiers](/docs/security/tiers)** | Regex, entropy, TruffleHog, Tier 2 review, human approval |
| **[Security Configuration](/docs/security/configuration)** | Global config, project marker, exclusions, custom redaction |
| **[Schema](/docs/schema/overview)** | Trace structure and field semantics |
| **[Consume](/docs/workflow/consume)** | Loading datasets back out of Hugging Face |

