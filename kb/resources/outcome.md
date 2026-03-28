# opentraces.ai: Deliverables

> Derived from [intent.md](./intent.md). This document defines the concrete product artifacts to ship.

---

## Deliverable 1: The CLI + Local Review App

**What it is**: A CLI tool and companion local web interface that captures agent session traces from disk, enriches them with opentraces.ai's schema (outcome signals, attribution blocks, sub-agent hierarchy, environment metadata), applies configurable security tiers, and publishes to HuggingFace Hub.

**Primary integration**: Claude Code. This is the first and only supported agent for v0.1. The CLI reads `~/.claude/projects/` session logs, parses them into the enriched JSONL schema defined in intent.md, and runs the security pipeline before upload.

### Core Capabilities

- **Passive capture**: Reads existing Claude Code session logs from disk. No hooks, no daemons, no agent modification.
- **Schema enrichment**: Outputs the full opentraces.ai JSONL schema, including `steps` with per-step `token_usage`, `parent_step` hierarchy, `reasoning_content`, `tool_definitions`, `snippets`, `attribution` blocks, `outcome` signals, `environment` metadata, `dependencies`, and `metrics`.
- **Three security tiers**: Tier 1 (Open, regex auto-redact), Tier 2 (Guarded, classifier + escalation), Tier 3 (Strict, local web review UI). Configurable per-project.
- **Local web review interface**: A lightweight local web app (served by the CLI on localhost) for Tier 3 strict review. Browse sessions, approve/reject/redact individual traces and turns, annotate outcome signals, then push approved traces to HF Hub.
- **Agent-native CLI protocol**: Every command emits structured JSON with `next_steps` and `next_command` fields. Designed to be driven by Claude Code itself via a bundled skill file.
- **Staged pipeline**: auth -> configure -> review -> publish. Push is hard-gated behind review completion. State persisted to `~/.opentraces/config.json`.
- **HuggingFace Hub upload**: Publishes enriched JSONL to personal dataset repos (e.g., `username/opentraces-claude-code`), tagged `opentraces` for community discovery. Auto-generated dataset card with schema docs, contributor stats, and load snippet.

### Distribution

Available across the main package registries, depending on the stack chosen:

| If Python | If TypeScript/Node | If Rust |
|-----------|-------------------|---------|
| PyPI (`pip install opentraces`) | npm (`npm install -g opentraces`) | Cargo + Homebrew + curl binary |
| Single runtime dep: `huggingface_hub` | Binary distribution via GitHub releases | Binary distribution via GitHub releases |

The stack decision is downstream of Deliverable 3 (distribution model). The CLI must be installable in a single command regardless of stack.

### What Ships

- `opentraces` CLI binary/package
- Bundled `SKILL.md` for Claude Code integration (installed via `opentraces install-skill claude`)
- Local web review app (served by `opentraces review --web`)
- Claude Code parser outputting the full enriched schema
- Vendored security patterns (DataClaw's `secrets.py` + `anonymizer.py`, extended with credit cards, SSNs, phone numbers)
- Adapter contract (`typing.Protocol` or equivalent) ready for multi-agent expansion in v0.2

### Open Decision: Stack Choice

The stack affects distribution, ecosystem fit, and development velocity. Three options:

| Option | Pros | Cons |
|--------|------|------|
| **Python** | Matches DataClaw's ecosystem, `huggingface_hub` is native, ML community familiarity, fastest to build | Slower CLI startup, requires Python runtime on user's machine |
| **TypeScript/Node** | Matches traces.com's approach (npm wrapper around binary), Claude Code plugin ecosystem is JS-native | HF Hub SDK is Python-only (would need REST API calls), less ML community familiarity |
| **Rust** | Fast binary, no runtime dependency, matches traces.com's compiled CLI pattern | Slowest to build, HF Hub integration via REST API only, smaller contributor pool |

**Recommendation**: Python for v0.1 (fastest path to a working product, native HF Hub integration, DataClaw patterns are directly referenceable). Compile to standalone binary via PyInstaller/Nuitka for distribution if startup time becomes an issue.

---

## Deliverable 2: Marketing Website (opentraces.ai)

**What it is**: A marketing site at opentraces.ai that explains the product, builds trust through security-first messaging, and serves two distinct audiences: developers who contribute traces, and ML teams who consume them.

### Two-Sided Landing Page

**For developers (contributors)**:

- **The selfish pitch**: "Share your agent traces, get a free analytics dashboard that shows you how to code better with AI. Your personal Spotify Wrapped for coding agents." Cost per outcome, cache hit rates, tool patterns, model comparison, all from your own data.
- **The competitive pitch**: "See how you compare." Show developers how their agent usage stacks up against others doing similar work, same language ecosystem, same frameworks, similar task types. Percentile rankings ("You're in the top 15% for cache efficiency among TypeScript developers"), peer benchmarks ("Developers working with Next.js average 30% fewer tool calls on refactoring tasks"), and anonymous cohort comparisons. This directly solves the game theory of sharing for nothing: you can't see how you compare unless others share too, and others can't compare unless you share. The more people contribute, the richer the comparisons become. The network effect IS the incentive.
- **The altruistic pitch**: "Every trace you share helps the open-source community train better coding models. Your real-world workflows are worth more than any synthetic benchmark."
- **Security-first language**: Lead with the three security tiers. Visuals showing the pipeline from raw session to redacted, reviewed, published trace. "You control what leaves your machine." Emphasize per-project configuration, the explicit push model (nothing uploads without your action), and the staged pipeline with gates.
- **How it works**: 3-step visual. Install -> Configure security tier -> Push to HuggingFace. Screenshot of the CLI in action. Screenshot of the local review web UI for Tier 3.
- **Use cases**: (1) Open-source contributor sharing publicly with Tier 1, (2) Professional developer reviewing sessions on a private codebase with Tier 3, (3) Researcher batch-exporting benchmark runs.

**For ML teams (consumers)**:

- **Why this data**: Real developer workflows across real codebases, not synthetic benchmarks. Outcome signals for RL/reward modeling. Sub-agent hierarchy for orchestration research. Per-step token counts for efficiency analysis.
- **Standards alignment**: How the schema relates to ATIF, ADP, Agent Trace, OTel. The ADP + Agent Trace bridge positioning: trajectory data (what the agent thought) + code attribution (what it produced), the complete record.
- **How to use it**: `datasets.load_dataset("username/opentraces-claude-code")` one-liner. Filter by outcome, by dependency, by agent, by model. Examples: building an RL environment from outcome signals, expanding training data with real tool-use trajectories, studying cache efficiency across the community.
- **Schema documentation**: Interactive schema explorer showing a sample trace record with annotations explaining each field.

### Design Principles

- Security-oriented: green/shield/lock visual language, not playful/casual
- Technical credibility: show the schema, show the pipeline, show the standards
- No "protest art" framing: constructive, neutral, infrastructure-for-the-community positioning
- Clear call to action: install the CLI, contribute your first trace, see your dashboard

### What Ships

- Static site at opentraces.ai (likely Next.js or Astro, deployed to Vercel/Netlify)
- Schema documentation page
- "Get Started" guide
- Dashboard preview/demo

---

## Deliverable 3: Distribution Model (Open Decision)

**What it is**: How the CLI reaches users and integrates with their workflow. This is an unresolved decision that affects Deliverable 1's architecture.

### Option A: Standalone CLI (like DataClaw)

A package on PyPI/npm/Homebrew that users install globally. Operates independently of any agent. Bundled skill file for Claude Code integration but not dependent on it.

```
pip install opentraces
opentraces prep
opentraces export
opentraces review --web
opentraces push
```

**Pros**: Maximum reach (works with any agent, any editor), independent release cycle, clear product boundary.
**Cons**: Another global install for users to manage, no deep integration with agent workflows.

### Option B: Claude Code Plugin/Skill Collection (like gstack)

A collection of markdown skills and shell scripts that install directly into Claude Code's skill system. The agent itself drives the workflow through natural language.

```
# User says to Claude Code:
"Share this session to opentraces"
# Claude Code reads the skill, runs the pipeline, handles review
```

**Pros**: Zero-friction for Claude Code users (the primary audience), agent-native by default, no separate install.
**Cons**: Locked to Claude Code ecosystem, harder to support other agents later, depends on Claude Code's skill system stability.

### Option C: Hybrid (Standalone CLI + Skill Layer)

Ship both: a standalone CLI that works independently, plus a thin skill layer that teaches Claude Code how to invoke the CLI. The skill file is a wrapper, not the product.

```
# Install the CLI
pip install opentraces

# Install the Claude Code skill (optional)
opentraces install-skill claude

# Now Claude Code can drive the CLI via natural language
# OR users can run the CLI directly
```

**Pros**: Both audiences served, clean separation of concerns, skill layer is thin and replaceable.
**Cons**: Two things to maintain, skill layer needs to stay in sync with CLI.

**Recommendation**: Option C (Hybrid). The CLI is the product, the skill is a convenience layer. This mirrors how DataClaw and traces.com both ship: a real CLI with an optional agent skill. It also keeps the door open for non-Claude-Code agents.

---

## Deliverable 4: Trace Explorer (HuggingFace Data Browser)

**What it is**: A web application (HuggingFace Space) that provides a user-friendly way to search and browse the community's published traces on HuggingFace Hub. We do not store the data, we provide a search and discovery layer over it.

### Core Capabilities

- **Search by username**: See all traces published by a specific contributor
- **Search by intent/task**: What was the developer trying to do? (Extracted from first user message or `task.description`)
- **Search by application domain**: Filter by language ecosystem, dependencies, project type
- **Search by outcome**: Show only successful sessions, only failed sessions, sessions with commits
- **Search by agent/model**: Filter by `agent.name`, `agent.model`
- **Trace viewer**: Render a single trace as a readable conversation with collapsible tool calls, highlighted code blocks, and outcome summary
- **Dataset aggregation**: Query across all `opentraces`-tagged datasets on HF Hub, not just one contributor's repo
- **Community stats**: Total traces, active contributors, agent distribution, model distribution, top dependencies
- **Peer comparison / competitive benchmarks**: The core retention mechanic. Shows contributors how they compare to others in their cohort, filtered by language ecosystem, dependency stack, or task type. Percentile rankings (cache efficiency, cost per outcome, tool call density, success rate), anonymous cohort averages, and trend lines. "Developers working with similar stacks average X, you're at Y." This is the feature that solves the game theory: the comparisons only exist because others contributed, and your contribution makes the comparisons richer for everyone. The network effect compounds, each new contributor makes the data more valuable for every existing contributor.

### How It Works

- Built as a HuggingFace Space (Gradio or Streamlit)
- Queries HF Hub's Dataset Viewer API to search across all `opentraces`-tagged datasets
- No backend database, all data lives on HF Hub, we just provide the search interface
- Also serves as the **contributor dashboard** from intent.md's "Growth Loop" section: enter your HF username, see your personal analytics (sessions over time, token spend, tool usage, success rate, efficiency score)
- Peer comparisons are computed over the aggregate of all `opentraces`-tagged datasets, cohorted by `environment.language_ecosystem`, `dependencies`, and `agent.model`

### What Ships

- HuggingFace Space at `huggingface.co/spaces/opentraces/explorer`
- Trace search and filtering UI
- Individual trace viewer
- Contributor dashboard (personal analytics + peer comparisons from published traces)
- Community overview page

---

## Deliverable Dependency Graph

```
Deliverable 3 (Distribution Decision)
       |
       v
Deliverable 1 (CLI + Review App)  -- produces data for -->  Deliverable 4 (Trace Explorer)
       |
       v
Deliverable 2 (Marketing Website)  -- links to -->  Deliverable 4 (Trace Explorer)
```

**Sequencing**: Resolve Deliverable 3 first (affects CLI architecture). Then build Deliverable 1 (the core product). Deliverable 2 and 4 can be built in parallel once D1 has a working prototype producing real data on HF Hub.

---

## Out of Scope for v0.1

These are explicitly deferred:

- Multi-agent support beyond Claude Code (v0.2, adapter contract ships in v0.1)
- Tier 2 guarded classifier (v0.2, requires training data from v0.1 contributions)
- Real-time capture / stop-hooks (intentionally excluded, passive-only)
- Canonical aggregated dataset curation (v0.2, after schema stability)
- Parquet dual-write (v0.2, after schema stability)
- Community comparison features in dashboard (v0.2)
- AI-generated summaries/embeddings for traces (v0.2)
- Team/org features (not our mission, HF orgs serve this)
- PR bot / GitHub integration (v0.2+)
