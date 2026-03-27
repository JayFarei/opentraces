# Community Agent Trace Sharing for Training: Landscape Scan

> Research date: 2026-03-27
> Sources: PyPI (VibeLens), HuggingFace (PatronusAI/TRAIL, agent-evals/hal_traces, jupyter-agent), OWASP AOS, OTel GenAI Semantic Conventions, Claude Code security docs, Ceros, MLflow, AG2, Langfuse, AgentOhana, AgentBank, OpenCUA/AgentNet, Stanislas.blog
> Category: Landscape scan of projects enabling crowdsourced/community sharing of code agent session traces
> Context: Assessing whether anyone is building the "opt-in, share anonymized code-agent traces to HF Hub for training" pipeline

---

## Executive Summary

One project, **VibeLens**, directly addresses donating real-world coding agent session traces for research. Several HF Hub datasets contain agent traces but were produced by research teams, not crowdsourced from users. The infrastructure for community trace sharing exists in pieces (HF Storage Buckets for mutable trace ingestion, OTel GenAI Semantic Conventions for schema, OWASP AOS for agent-specific event types), but **no project currently combines opt-in collection from real code-agent users, configurable anonymization, and upload to a public HF Hub dataset**. The gap is the community pipeline layer.

---

## 1. Direct Hit: VibeLens

### What It Is

VibeLens is a Python CLI tool (PyPI: `vibelens`, v0.8.0) that lets developers view and **donate** their coding agent session traces to academic research.

### Key Details

| Aspect | Detail |
|--------|--------|
| **Publisher** | yejh123/VibeLens on GitHub |
| **Recipient** | CHATS-Lab (Conversation, Human-AI Technology, and Safety Lab) at Northeastern University |
| **Agent support** | Claude Code (`~/.claude/` sessions), Copilot (VS Code), OpenCode, Codex |
| **Mechanism** | Opens browser UI, reads local sessions, user selects sessions to donate, clicks Donate |
| **License** | Open source |
| **Tooling** | `uv run ruff check`, `uv run pytest` |

### How It Differs from the opentraces.ai Vision

| Dimension | VibeLens | opentraces.ai (our project) |
|-----------|----------|--------------------------|
| Destination | Single academic lab (CHATS-Lab) | Public HF Hub dataset, anyone can train on |
| Collection mode | Manual batch: select sessions, click Donate | Always-on opt-in plugin with per-session control |
| Anonymization | Not documented, likely session-level selection only | Configurable security tiers (full, redacted, structural-only) |
| Schema | Agent-native formats (raw session JSON) | Normalized JSONL aligned to OTel GenAI / AOS |
| Community access | Researchers only (lab-controlled) | Open dataset, community-contributed |

### Significance

VibeLens validates that developers **will** donate traces when the UX is simple. The donation model works. But it targets a closed research audience, not open community training data.

---

## 2. Existing Agent Trace Datasets on HF Hub

### 2.1 PatronusAI/TRAIL

- **Size**: 148 annotated agent execution traces, 841 labeled errors
- **Source agents**: OpenAI o3-mini and Anthropic Claude
- **Tasks**: GAIA and SWE-Bench
- **Collection method**: OpenTelemetry (OpenInference standard)
- **Key finding**: Even SOTA LLMs achieve only 11% accuracy on trace debugging
- **Gap**: Curated benchmark, not crowdsourced. Fixed task set, not real user sessions.

### 2.2 agent-evals/hal_traces

- **Origin**: Princeton PLI's Holistic Agent Leaderboard (HAL)
- **Purpose**: Standardized agent evaluation with cost-performance tradeoffs
- **Status**: Dataset viewer currently broken (JSON parse error: `FeaturesError: ArrowInvalid`)
- **Gap**: Evaluation-focused, not training-focused. Not community-contributed.

### 2.3 jupyter-agent/jupyter-agent-dataset

- **Size**: 51,389 synthetic notebooks, ~200M training tokens
- **Created by**: HF team (Baptiste Colle, Hanna Yukhymenko, Leandro von Werra)
- **Trace type**: Code execution traces with QA pairs (thinking and non-thinking subsets)
- **Code generator**: Qwen-Coder-480B with E2B executor
- **License**: Apache 2.0
- **Gap**: Synthetic, not real user sessions. No community contribution mechanism.

### 2.4 AgentOhana / AgentBank

- **AgentOhana**: Unified pipeline aggregating agent trajectories from diverse sources, produced xLAM-v0.1
- **AgentBank**: 50K+ interaction trajectories, fine-tuned into Samoyed model
- **Gap**: Academic aggregation of existing datasets, not a live community pipeline.

### 2.5 OpenCUA / AgentNet

- **AgentNet**: 22.6K human-annotated computer-use tasks across Windows/macOS/Ubuntu, 200+ apps
- **Collection**: Synchronized screen video, mouse/keyboard events, accessibility trees via AgentNetTool annotation UI
- **License**: MIT
- **Gap**: Computer-use trajectories (GUI interaction), not code-agent session traces.

### 2.6 DeepNLP/Coding-Agent-Github-2025-Feb

- Appeared in search results as a coding-agent-specific HF dataset
- Limited documentation found
- **Gap**: Snapshot dataset, not a live contribution pipeline.

---

## 3. Infrastructure That Already Exists

### 3.1 HF Storage Buckets (Upload Mechanism)

HF launched Storage Buckets specifically for mutable, high-throughput ML artifacts. Their blog explicitly lists "agents storing traces, memory, and shared knowledge graphs" as a target use case. Key features:

- S3-like object storage, browsable on Hub
- Python SDK (`huggingface_hub`) and `hf` CLI support
- `CommitScheduler` for near real-time ingestion (configurable interval in minutes)
- Xet deduplication takes advantage of overlap between related artifacts (raw traces, processed summaries)
- HF Jobs for scheduled Python scripts (cron-based)

**Implication**: The upload infrastructure for a community trace dataset is production-ready. No custom backend needed.

### 3.2 OTel GenAI Semantic Conventions (Schema)

OpenTelemetry standardized LLM tracing in early 2026. Key schema elements:

- Span naming: `{operation} {name}` format (e.g., `chat gpt-4o`, `execute_tool web_search`)
- Standard attributes: `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.provider.name`, `gen_ai.operation.name`
- Hierarchy: agent loop (root span) -> turn -> step (LLM call, tool call)
- Adopted by: Datadog (v1.37+), AG2, LangChain, MLflow, OpenLLMetry

**Implication**: Aligning JSONL schema to these attributes gives free interop with every observability tool.

### 3.3 OWASP Agent Observability Standard (Agent-Specific Events)

AOS extends OTel with canonical agent events:

- `toolCallRequest` / `toolCallResult` with `toolId`, `executionId`, `inputs`, `reasoning`
- `agentTrigger` for autonomous activation events
- `memoryRetrieval` / `memoryRetrievalResult` for context lookups
- `reasoning` field on every step explaining agent rationale
- Agent metadata: `agent.id`, `agent.name`, `agent.version`
- LLM metadata: `llm.model.name`, `llm.provider.name`
- Supports both OTel and OCSF trace formats

**Implication**: AOS canonical events map directly to security tiers, the `reasoning` field is the highest-sensitivity data point for anonymization decisions.

### 3.4 MLflow: Traces as Dataset Fuel

MLflow docs explicitly state: "Traces from production systems capture perfect data for building high-quality datasets with precise details for internal components like retrievers and tools." Their tracing is fully OTel-compatible. But traces stay siloed per organization.

**Implication**: The "trace -> dataset -> fine-tuning" pipeline is a validated concept. The missing piece is cross-org sharing.

---

## 4. Security and Anonymization Landscape

### 4.1 Known Risks in Code Agent Traces

A security audit of Claude Code environments (per Medium, March 2026) found:

- **Credentials persisted in session transcripts** (API keys, tokens in tool arguments)
- **Zombie processes holding authenticated API sessions**
- **Unaudited MCP connections** with full schema access
- **No plugin governance** framework (at time of audit)
- **Elevated permissions inheritance**: if account has admin privileges, Claude Code and all subagents inherit them

### 4.2 Ceros Enterprise Visibility

Ceros (per Hacker News, March 2026) demonstrated full session logging for security teams:

- Complete process ancestry for every Claude Code invocation
- Binary signatures of every executable in the chain
- OS-level user identity tied to verified human
- Every action Claude Code took during the session
- Full tool schema visibility (built-in tools + MCP servers)

**Implication**: The data richness needed for training purposes is also the data that creates security risk. Tiered anonymization is not optional, it is the core product decision.

### 4.3 Claude Code Plugin Ecosystem Security

As of March 2026:
- 101 plugins (33 Anthropic-built, 68 partner)
- `security-guidance` plugin runs as pre-tool hook, warns before unsafe patterns
- Managed settings can disable bypass-permissions mode and restrict plugin sources
- Community security-scanner plugin brings GitHub Dependabot + secret detection

### 4.4 What Anonymization Must Handle

Based on the research, a tiered anonymization system must address:

| Data Element | Risk Level | Anonymization Approach |
|-------------|------------|----------------------|
| Tool call arguments (file paths, URLs) | Medium | Path normalization, domain redaction |
| Code content in tool results | High | Content hashing or removal |
| API keys, tokens in prompts/results | Critical | Regex-based secret detection + removal |
| User identity, project names | Medium | Pseudonymization |
| Model reasoning / chain-of-thought | Low-Medium | Preserve (highest training value) |
| MCP server schemas and tool definitions | Low | Preserve (structural, not sensitive) |
| File system paths | Medium | Relativize and anonymize project root |
| Error messages with stack traces | Medium | Redact absolute paths, preserve structure |

---

## 5. The Gap: What Nobody Has Built

| Capability | VibeLens | TRAIL | jupyter-agent | AgentOhana | **Needed** |
|-----------|----------|-------|---------------|------------|-----------|
| Real user sessions | Yes | Partial (benchmark tasks) | No (synthetic) | No (aggregated) | **Yes** |
| Multi-agent support | Yes (4 agents) | Yes (2 models) | No | Yes | **Yes** |
| Community destination | No (single lab) | No (fixed dataset) | No | No | **HF Hub public dataset** |
| Opt-in per session | Yes (manual select) | N/A | N/A | N/A | **Plugin-level toggle** |
| Configurable anonymization | Not documented | N/A | N/A | N/A | **3 security tiers** |
| Standardized schema | Raw agent JSON | OpenInference/OTel | Custom | Custom per source | **OTel GenAI + AOS aligned** |
| Always-on collection | No (batch) | No | No | No | **Daemon/hook-based** |
| Upload to HF Hub | No | Pre-uploaded | Pre-uploaded | Pre-uploaded | **CommitScheduler or CLI push** |

**The specific gap**: A plugin/hook that sits inside a code agent (Claude Code first), captures session traces in OTel-aligned JSONL, applies configurable anonymization tiers, and pushes to a community HF Hub dataset repo. VibeLens proves the donation model works. The infrastructure (HF Buckets, OTel schema, AOS events) is ready. The integration layer is missing.

---

## 6. Tangential but Relevant

### 6.1 Stanislas.blog: Indexing Agent Sessions (January 2026)

Built a TUI tool (`fast-resume`) to index and search personal coding agent sessions. Indexed 1,152 sessions and 29,676 messages across Claude, Copilot, OpenCode, and Codex (Nov 2023 - Jan 2026). Key lesson: incremental indexing via modification time tracking is essential at scale.

### 6.2 GitHub Agent Activity (March 2026)

GitHub now surfaces agent sessions (Copilot, Claude, Codex) directly in Issues and Projects UI with live status. Each session shows status and links to session logs. This normalizes the concept of "agent session as a first-class artifact."

### 6.3 @mb202412 (X, March 2026)

Talk at Applied AI Summit on LLM agent evaluation: "Logging doesn't cut it for agents. Neither does unit testing. What works: trace every tool call." 56 likes. Reinforces the thesis that structured traces are the emerging evaluation primitive.

### 6.4 Langfuse Acquisition by ClickHouse (February 2026)

Langfuse (21K+ GitHub stars, YC W23) acquired by ClickHouse. Signal: LLM observability infrastructure is consolidating, and trace storage at scale is a solved problem at the platform level.

---

## 7. Conclusions for opentraces.ai

1. **VibeLens is the closest prior art** but targets a single academic lab, not community training. Study its UX for session selection.
2. **The "donate traces" model is validated** - developers will share when the friction is low.
3. **Schema should align to OTel GenAI Semantic Conventions + AOS canonical events** for maximum interop and downstream value.
4. **HF Storage Buckets + CommitScheduler** is the upload mechanism, no custom infra needed.
5. **Tiered anonymization is the core differentiator** - no existing project handles this well.
6. **Start with Claude Code** (richest session format, largest community), expand to Codex/Copilot/OpenCode later.
7. **The `reasoning` field is the highest-value, highest-risk data** - it is what makes traces useful for training but also where PII/secrets most often leak.
