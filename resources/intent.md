# opentraces.ai: Agent Trace Crowdsourcing for Hugging Face Hub

## One-line

A plugin that lets code-agent users opt in to sharing their session traces as structured JSONL datasets on Hugging Face Hub, with configurable security tiers for sensitive-data handling.

---

## The Problem

There is a growing ecosystem of tools that _capture_ code-agent traces — `claude-trace`, Langfuse hooks, OpenAmnesia, `claudebin.com` — but no standard path from capture to _contribution_. Meanwhile, the training and RL communities are starved for high-quality agentic trajectory data. NVIDIA's Nemotron-RL-Agentic-SWE-Pivot-v1 dataset demonstrates the value: ~34k trajectories of OpenHands agent runs on SWE-bench, structured as turn-level records with system prompts, tool calls, and outcomes. But that dataset was produced by a single org running a single agent on a single benchmark. The real frontier is crowdsourced traces from real developer workflows across real codebases — and that requires solving the trust, privacy, and format problems that currently prevent anyone from sharing.

The existing ecosystem breaks down as:

| Layer             | Tools                                                                                                        | Gap                                                                                                                                                                                                                                                                                                                  |
| ----------------- | ------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Capture**       | `claude-trace` (fetch interception → JSONL), Langfuse hooks (stop-hook → OTel), Claude Code native OTel logs | Proprietary formats, no upload path                                                                                                                                                                                                                                                                                  |
| **Ingestion & Search** | CASS (19 auto-discovery connectors, `franken_agent_detection` crate): normalises JSONL, SQLite, Markdown, JSON, encrypted formats into unified `Conversation -> Message -> Snippet` model. Regex + entropy secret redaction with severity tiers. Cross-machine SSH/rsync sync. Rich robot/agent JSON API with introspection. | Local search only, no upload path, no training-format output. License unclear (NOASSERTION). Core parsing crate (`franken_agent_detection`) not published to crates.io, only available as local path dependency. Full scouting brief: `kb/background-research/06-cass-coding-agent-session-search.md` |
| **Sharing / Collaboration** | **traces.com** (Lab 0324, proprietary SaaS, 10 agent adapters)                                               | Proprietary platform, data locked in Convex backend with no bulk export, sharing-first schema (5 message types, no outcome signals, no per-message token counts, no sub-agent hierarchy), undisclosed pricing, GitHub-only auth, no training-data utility. See [Competitive Analysis: traces.com](#competitive-analysis-tracescom) below. |
| **Observability** | Langfuse, LangSmith                                                                                         | Designed for internal debugging, not dataset contribution                                                                                                                                                                                                                                                            |
| **Standards**     | Agent Trace spec (Cursor/community, v0.1.0 RFC, 10+ corporate backers)                                       | Solves attribution (which lines came from AI) but not trajectory (the conversation that produced them). No existing tool bridges both. opentraces.ai embeds Agent Trace attribution within trajectory records, creating the complete process + output record.                                                         |
| **Datasets**      | Nemotron-RL, SWE-bench traces                                                                                | Synthetic/benchmark-only, not real-world crowdsourced                                                                                                                                                                                                                                                                |
| **Privacy**       | Anthropic's code-execution sandboxing patterns                                                               | No reusable pipeline for trace sanitisation                                                                                                                                                                                                                                                                          |
| **Crowdsource**   | **DataClaw** (peteromallet, 2k stars, Feb 2026)                                                              | Closest open-source tool: reads 7 agents' logs, redacts PII, publishes JSONL to HF. But shallow schema (no outcome signals, no sub-agent hierarchy, no environment metadata), single security tier, federated-but-uncoordinated governance. See [Competitive Analysis: DataClaw](#competitive-analysis-dataclaw) below. |

opentraces.ai sits in the gap: it takes captured traces, normalises them to a generic agent-trace JSONL schema, applies configurable security review, and uploads to HF Hub as dataset contributions. Two competitors validate the market from opposite directions: **traces.com** (proprietary SaaS, 10 adapters, team collaboration features) proves that developers want to share agent sessions, but locks data in a walled garden with no training utility. **DataClaw** (open source, 2k stars, ~32 HF datasets in 1 month) proves that developers will contribute to open datasets, but its shallow schema is insufficient for RL/training consumers and its single-tier security model limits adoption by teams with sensitive codebases. opentraces.ai combines the open-data ethos of DataClaw with richer schema depth than either competitor, while adding the contributor incentives (analytics dashboard) that neither provides.

---

## Core Experience

### Three Security Tiers

The plugin offers three modes for controlling what gets uploaded. These are configured per-project or per-session, not globally, because the sensitivity of a personal side-project differs enormously from a client codebase.

**Tier 1 — Open Mode (Minimal Gate)**

Traces are uploaded with minimal friction, but not blindly. Before any upload a lightweight baseline security check runs: regex-based scanning for high-confidence secrets (API keys, tokens, passwords) and obvious PII (emails, IP addresses). Anything that trips the baseline is auto-redacted, not escalated. The user never reviews individual traces, but the floor of "no raw credentials in a public dataset" is maintained.

This is for:

- Open-source projects where the codebase is already public
- Benchmark runs (SWE-bench, Aider-bench) where there's nothing to protect
- Researchers who want maximum throughput and accept the risk

The "open" label signals this tier is for projects where the codebase is already public. The user must explicitly opt in with a confirmation that names the target dataset and acknowledges only baseline checks will occur.

```
Trace captured
    │
    ▼
┌──────────────────────┐
│ Baseline secret/PII  │  regex scan only
│ scan + auto-redact   │  no human review
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Upload to HF Hub     │  continuous, per-turn
└──────────────────────┘
```

**Security principle:** Even the lowest-friction tier guarantees no raw secrets or credentials leak into the public dataset. The user trades depth of review for speed, not safety floor for speed.

---

**Tier 2 — Guarded Screening + Escalation**

Traces pass through a classifier/extraction pipeline before upload. This pipeline:

1. **PII detection**: Scans for emails, API keys, tokens, credentials, internal hostnames, IP addresses, filesystem paths that reveal org structure. Uses a combination of regex patterns and a lightweight classifier.
2. **Sensitive content classification**: Flags traces that reference proprietary codebases, internal tool names, customer data, or anything that looks like it shouldn't be in a public dataset. This is where a small LLM or embedding-based classifier earns its keep.
3. **De-anonymisation risk scoring**: Estimates how identifiable the contributor is from the trace content, not just explicit PII, but stylometric and contextual signals (unique variable naming patterns, distinctive prompt phrasing, references to specific internal systems).
4. **Escalation**: Anything flagged gets surfaced to the user in an interactive review before upload. The user sees exactly what was flagged, why, and can approve, redact, or reject per-trace.

This is the default mode for most users. It balances contribution friction against data safety.

```
Trace captured
    │
    ▼
┌──────────────────────┐
│ Baseline secret/PII  │  same regex layer as Tier 1
│ scan + auto-redact   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Classifier pipeline  │  LLM/embedding-based
│  - sensitive content │
│  - de-anon risk      │
└──────────┬───────────┘
           │
      ┌────┴────┐
      │         │
   clean     flagged
      │         │
      ▼         ▼
┌──────────┐ ┌──────────────┐
│ Upload   │ │ Interactive  │
│ to HF    │ │ review       │
└──────────┘ │ (approve /   │
             │  redact /    │
             │  reject)     │
             └──────┬───────┘
                    │
                    ▼
              Upload or discard
```

**Security principle:** Machine classifiers handle the bulk of traces automatically. Humans only see the edge cases the classifier is uncertain about, keeping friction proportional to actual risk.

---

**Tier 3 — Strict Post-Session Review (Human-in-the-Loop)**

Nothing is uploaded during the session. All traces are buffered locally. After the session ends, the user manually reviews every trace before anything leaves their machine, either through a CLI review interface or a local web UI. They can:

- Approve individual traces or the full session
- Redact specific turns, tool outputs, or file contents
- Annotate traces with quality signals (was this a good interaction? did the agent succeed?)
- Skip the upload entirely

The user explicitly pushes approved traces to Hugging Face Hub as a deliberate action, comparable to `git push`, not an automatic upload.

This is for users working on sensitive codebases who still want to contribute selectively. The review interface is also valuable for the contributor's own learning, reviewing your agent traces is genuinely useful for improving how you work with code agents.

```
Trace captured
    │
    ▼
┌──────────────────────┐
│ Buffer locally       │  nothing leaves the machine
│ (session JSONL)      │
└──────────┬───────────┘
           │
     session ends
           │
           ▼
┌──────────────────────┐
│ Human review         │  CLI: `opentraces review`
│  - per-trace approve │  or local web UI
│  - redact turns      │
│  - annotate quality  │
│  - reject / skip     │
└──────────┬───────────┘
           │
    user confirms push
           │
           ▼
┌──────────────────────┐
│ Upload to HF Hub     │  explicit, deliberate action
└──────────────────────┘
```

**Security principle:** Full human-in-the-loop. Nothing is uploaded without the contributor seeing and approving it. The machine does zero filtering, the human is the entire security layer.

---

## Schema Design

### Requirements

The trace format must satisfy several downstream consumers simultaneously:

1. **Training / SFT**: Needs clean message sequences with role labels, tool-use structured as tool_call/tool_result pairs, and outcome signals.
2. **RL / RLHF**: Needs trajectory-level reward signals (did the task succeed?), step-level annotations, and the ability to identify decision points.
3. **Telemetry / Analytics**: Needs token counts, latency, model identifiers, cache hit rates, cost estimates.
4. **Cross-agent compatibility**: Must represent traces from Claude Code, Cursor, Cline, Codex CLI, OpenCode, Pi, and future agents without agent-specific fields in the core schema.

### Relationship to Existing Standards

Three standards are relevant to opentraces.ai's schema design. Each solves a different piece of the puzzle:

**ATIF / Harbor (v1.6)** is a training trajectory serialization format. It defines the step-based TAO (Thought-Action-Observation) loop structure that the community has converged on, with fields for `reasoning_content`, `tool_call_id`, `tool_definitions`, `logprobs`, and `subagent_trajectory_ref`. ATIF is the closest thing to a standard for what opentraces.ai's schema is trying to be. The strategic question is whether opentraces.ai should: (a) define its own schema (current approach), (b) output ATIF-compatible records, or (c) treat ATIF as the target format and position opentraces.ai purely as the capture-sanitise-upload pipeline. Option (c) would simplify schema work and make output immediately compatible with ATIF-consuming training pipelines, but would tie opentraces.ai to ATIF's governance and release cadence. **This is the single most important schema decision to resolve before implementation.**

**ADP (Agent Data Protocol)** is an interlingua for normalizing diverse agent trace formats into a common structure for training. Its core argument is O(D+A) adapters (one per data source + one per agent) instead of O(D\*A). ADP has been used to normalize 1.3M+ trajectories. opentraces.ai's adapter-based normalisation layer is essentially the same pattern. If opentraces.ai adopts ATIF as its output format, ADP's adapter patterns could inform the adapter contract.

**Agent Trace spec (Cursor/community, v0.1.0 RFC)** solves the adjacent problem of attributing which lines of code came from which agent conversation, at file/line granularity. Backed by 10+ corporate sponsors (Cloudflare, Vercel, Google Jules, Cognition). Agent Trace focuses on the _output_ (code attribution), while ATIF/ADP focus on the _process_ (trajectory). No existing tool or standard bridges both, and this is opentraces.ai's highest-leverage opportunity.

**The core insight**: Every commit systematically discards the reasoning that produced the code (Cognition's "context as currency" thesis). Agent Trace preserves _which_ lines came from AI. ATIF/ADP preserve _how_ the agent reasoned. Neither alone tells the complete story. opentraces.ai should be the format that connects the full conversation trajectory to the specific code output at line granularity, the complete record of process + output.

opentraces.ai's schema should:

- **Embed** Agent Trace attribution blocks directly in the trace record, not just link by URL. URLs may not persist in a crowdsourced dataset, and embedding creates a self-contained record that pairs reasoning with output. The research report's recommended schema (section 15.2) already demonstrates this pattern with an `attribution` field containing Agent Trace's `files → conversations → ranges` structure.
- **Construct attribution deterministically** from trace data. Claude Code traces contain edit operations (file_path, line ranges, content), the `outcome.patch` gives the unified diff, and `snippets` extract code blocks with file positions. These can be synthesized into Agent Trace `attribution` records without user annotation or LLM enrichment.
- **Complement** Agent Trace by providing the conversation content that Agent Trace only links to. An Agent Trace record says "lines 42-55 of parser.ts were AI-generated." An opentraces.ai record says "here is the full conversation that produced lines 42-55, including the reasoning, the failed attempts, the tool calls, and the final edit."
- **Enable the training value chain** that neither format alone supports: given a code change (attribution), reconstruct the reasoning that produced it (trajectory), evaluate whether it was good (outcome signals), and use the complete record for SFT/RL training.

This positions opentraces.ai as **ADP + Agent Trace**, the unified format that bridges trajectory data and code attribution. Neither DataClaw, traces.com, nor any existing dataset provides this combination. The 10+ corporate backers of Agent Trace become potential adopters of opentraces.ai as the format that gives their attribution data a conversation context.

**traces.com's normalized message taxonomy** provides a practical reference for message classification. Their server-side processing classifies raw messages into 5 types: `user_message`, `agent_text`, `agent_thinking`, `agent_context`, `tool_call`, with open-ended tool subtypes (`terminal_command`, `edit`, `update_plan`, `view_image`, `mcp__{server}__{tool}`). This taxonomy is sharing-oriented (optimized for human browsing), not training-oriented (no step-level token counts, no outcome signals, no sub-agent hierarchy). opentraces.ai's schema should be a superset: our `role` + `call_type` + `agent_role` fields can reconstruct traces.com's 5 types while providing the richer per-step metadata that training pipelines require. Their `mcp__{server}__{tool}` naming convention for MCP tools is worth adopting directly, as it aligns with the AAIF/MCP ecosystem.

**AAIF (Agentic AI Foundation)**, launched Dec 2025 by Anthropic, OpenAI, Block/Linux Foundation, governs AGENTS.md, MCP, and Goose. As opentraces.ai is a plugin for Claude Code (an Anthropic tool), alignment with AAIF standards from the start avoids future incompatibility.

### Proposed JSONL Record Structure

Each line in the JSONL file represents one complete agent session or task unit. The schema is informed by ATIF v1.6, ADP, and the field patterns found in existing HF datasets (nlile, Nebius, CoderForge).

```jsonl
{
  "schema_version": "0.1.0",
  "trace_id": "uuid",
  "session_id": "uuid",
  "content_hash": "sha256-hex",
  "timestamp_start": "ISO8601",
  "timestamp_end": "ISO8601",
  "task": {
    "description": "Fix the failing test in src/parser.ts",
    "source": "user_prompt",
    "repository": "owner/repo",
    "base_commit": "abc123def456..."
  },
  "agent": {
    "name": "claude-code",
    "version": "1.0.83",
    "model": "anthropic/claude-sonnet-4-20250514"
  },
  "environment": {
    "os": "darwin",
    "shell": "zsh",
    "vcs": {
      "type": "git",
      "base_commit": "abc123def456...",
      "branch": "main",
      "diff": "unified diff string or null"
    },
    "language_ecosystem": [
      "typescript",
      "python"
    ]
  },
  "system_prompts": {
    "sp_a1b2c3": "You are Claude Code..."
  },
  "tool_definitions": [
    {
      "name": "bash",
      "description": "Execute shell commands",
      "parameters": {}
    }
  ],
  "steps": [
    {
      "step_index": 1,
      "role": "user",
      "content": "Fix the failing test in src/parser.ts",
      "timestamp": "ISO8601"
    },
    {
      "step_index": 2,
      "role": "agent",
      "content": "I'll investigate the failing test...",
      "reasoning_content": "The user wants me to...",
      "model": "anthropic/claude-sonnet-4-20250514",
      "system_prompt_hash": "sp_a1b2c3",
      "agent_role": "main",
      "parent_step": null,
      "call_type": "main",
      "tools_available": [
        "bash",
        "read",
        "edit",
        "glob",
        "grep",
        "write",
        "agent"
      ],
      "tool_calls": [
        {
          "tool_call_id": "tc_001",
          "tool_name": "bash",
          "input": {
            "command": "npm test -- --grep parser"
          },
          "duration_ms": 3400
        }
      ],
      "observations": [
        {
          "source_call_id": "tc_001",
          "content": "FAIL src/parser.test.ts...",
          "output_summary": "1 test failed: parser.test.ts line 42 assertion error"
        }
      ],
      "snippets": [
        {
          "file_path": "src/parser.ts",
          "start_line": 42,
          "end_line": 55,
          "language": "typescript",
          "text": "function parseToken(input: string)..."
        }
      ],
      "token_usage": {
        "input_tokens": 12400,
        "output_tokens": 890,
        "cache_read_tokens": 11200,
        "cache_write_tokens": 1200,
        "prefix_reuse_tokens": 11200
      },
      "timestamp": "ISO8601"
    },
    {
      "step_index": 5,
      "role": "agent",
      "agent_role": "explore",
      "parent_step": 3,
      "call_type": "subagent",
      "content": "Searching for related parser implementations...",
      "subagent_trajectory_ref": "session_id_of_explore_subagent"
    }
  ],
  "outcome": {
    "success": true,
    "signal_source": "user_annotation",
    "description": "Test passes after fix",
    "patch": "unified diff string",
    "committed": true,
    "commit_sha": "def789abc..."
  },
  "dependencies": [
    "stripe",
    "prisma",
    "next"
  ],
  "metrics": {
    "total_steps": 42,
    "total_input_tokens": 1800000,
    "total_output_tokens": 34000,
    "total_duration_s": 780,
    "cache_hit_rate": 0.92,
    "estimated_cost_usd": 2.4
  },
  "security": {
    "tier": "guarded",
    "flags_reviewed": 3,
    "redactions_applied": 1
  },
  "attribution": {
    "version": "0.1.0",
    "files": [
      {
        "path": "src/parser.ts",
        "conversations": [
          {
            "contributor": {
              "type": "ai",
              "model_id": "anthropic/claude-sonnet-4-20250514"
            },
            "url": "opentraces://trace_id/step_2",
            "ranges": [
              {
                "start_line": 42,
                "end_line": 55,
                "content_hash": "murmur3:9f2e8a1b"
              }
            ]
          }
        ]
      }
    ]
  },
  "metadata": {}
}
```

Key design decisions:

- **`steps` replaces `turns`**: Each step is an LLM API call (request + response), not a conversational turn. This aligns with ATIF's step-based model and correctly represents the TAO (Thought-Action-Observation) loop
- **`role: "agent"` not `"assistant"`**: Follows the ATIF/community convention (`"system" | "user" | "agent"`)
- **`tool_calls` and `observations` are separated**: Tool calls carry a `tool_call_id`, observations link back via `source_call_id`. This preserves the call/result separation that training pipelines depend on, rather than collapsing output into the tool call object
- **`system_prompt_hash` + dedup map**: System prompts are deduplicated into a top-level lookup table. A 20K-token system prompt repeated 92 times across steps would be catastrophically wasteful
- **`parent_step` for hierarchy**: Replaces the session-level `sub_agents` array with `turn_range`. Per-step `parent_step` links let you reconstruct the full parent-child tree precisely, even with interleaved subagents
- **`agent_role` per step**: Labels like `"main"`, `"explore"`, `"plan"` allow filtering by phase without reconstructing hierarchy from turn ranges
- **`subagent_trajectory_ref`**: Sub-agent transcripts are linked by session_id reference to separate trajectory records (ATIF pattern), not embedded. This separates storage depth from schema design
- **`tool_definitions`**: The complete set of available tools at session level, plus `tools_available` per step for when the tool set varies (e.g. Explore subagents get 10/18 tools). Critical for training models to select the right tool
- **`snippets`**: Code blocks extracted from tool results and agent responses, with file_path, start/end_line, and language. Following CASS's `Snippet` extraction pattern, this surfaces high-value code fragments without requiring consumers to parse full message content. Useful for code-search training and as the raw material for constructing the `attribution` block
- **`attribution`**: Embedded Agent Trace-compatible attribution block that records which files and line ranges were produced by the agent session. This is the field that bridges trajectory (process) and attribution (output), making opentraces.ai the ADP + Agent Trace unified format. Constructed deterministically from edit operations in the trace: `Edit` tool calls provide file_path and line ranges, `outcome.patch` provides the unified diff, and `snippets` provide extracted code blocks. The `conversation.url` field uses `opentraces://trace_id/step_N` to link each attributed range back to the specific step in the trajectory that produced it. `content_hash` (murmur3, matching Agent Trace convention) enables tracking attribution across refactors and file moves. This field is nullable, sessions that produce no code changes (pure research/exploration) have `attribution: null`
- **`reasoning_content`**: Explicit chain-of-thought field per step. Exposing reasoning artifacts improved SWE-Bench scores by ~3 points and cache hit rates by 40-80% (Cognition data)
- **`task` object**: Structured task metadata (source, repository, base_commit) enables filtering by task type, repository, and benchmark for downstream consumers
- **`content_hash`**: SHA-256 of the trace content for deduplication at upload time, following the nlile pattern
- **`outcome.committed`**: Boolean indicating whether the session's changes were committed to git. A session that results in a commit is higher-signal than one that was abandoned or reverted, this is a cheap, deterministic quality signal that can be derived from git history without user annotation. When true, `outcome.commit_sha` links to the specific commit, enabling cross-referencing with `git diff` and CI results
- **`dependencies`**: List of package/library names referenced or used during the session, extracted from `package.json`, `Gemfile`, `requirements.txt`, `pyproject.toml`, or tool call arguments. Enables downstream consumers to build datasets filtered by dependency (e.g., "all sessions involving the Stripe CLI" or "all sessions working with Prisma"). Package registries serve as a natural taxonomy for organizing and searching trace data on HF Hub or a discovery website
- **`outcome.patch`**: The unified diff produced by the session, following the convention in Nebius, CoderForge, and SWE-Fuse datasets
- **`observations.output_summary`**: Lightweight preview of tool results so consumers can assess relevance without downloading full multi-KB outputs
- **`call_type`**: Classifies steps as `"main"`, `"subagent"`, or `"warmup"` (KV cache priming calls with empty output). Warmup calls are noise for SFT but signal for caching research
- **Model identifiers** follow the `provider/model-name` convention from Agent Trace / models.dev
- **Security metadata** records what tier was used and what was flagged/redacted

**Optional RL-specific fields** (not in v0.1, but the schema reserves space for them):

- `token_usage.completion_token_ids`: Token ID sequence for the completion, enabling RL training without retokenization drift
- `token_usage.logprobs`: Log probabilities per token, required for PPO/DPO-style training. Without these, traces are only useful for SFT
- Step-level reward annotations for process reward models

---

## Architecture

### Vendor and Reference, Not Depend

**Strategic decision (revised)**: opentraces.ai vendors DataClaw's small utility modules (`secrets.py`, `anonymizer.py`, ~380 lines, MIT-licensed) and writes its own parsers that reference DataClaw's implementations for agent-specific edge cases, but output opentraces.ai's richer schema directly. DataClaw is NOT a runtime dependency (`pip install dataclaw` is not required).

**Why not runtime dependency**: DataClaw's parsers output "flat session dicts" (messages with role/content/tool_uses, session-level stats). opentraces.ai's enrichment layer needs access to raw trace data to construct: `parent_step` links (from session structure DataClaw flattens), `attribution` blocks (from edit operations DataClaw may not preserve), `system_prompt_hash` (from system prompts DataClaw may strip), step-level `token_usage` (DataClaw only captures session-level aggregates), and `snippets` with file positions (requires parsing raw tool call arguments). Depending on DataClaw as ingestion would require re-reading the raw files anyway to extract what DataClaw threw away, then merging two representations. Writing parsers that output our schema directly is simpler than this double-read.

**Why not fork**: Forking inherits DataClaw's tightly-coupled CLI architecture and ties us to their release cadence. DataClaw is a 1-month-old project with version string drift (`pyproject.toml` says 0.3.2, `__init__.py` says 0.3.0), CI that publishes to PyPI on every merge to main, and a "performance art protest" framing that creates branding risk for enterprise/institutional adoption.

**What we vendor (MIT license, copy directly)**:
- `secrets.py` (~273 lines): 19 regex patterns + Shannon entropy analysis + allowlist for false positives. This IS our Tier 1 "Open" security layer. Battle-tested patterns covering JWT, API keys by provider prefix, DB URLs, private keys, Bearer tokens, IPs, emails, high-entropy strings. We extend with: credit card numbers (Luhn), SSNs, phone numbers.
- `anonymizer.py` (~105 lines): SHA-256 username hashing, `/Users/`/`/home/` path stripping, macOS hyphen-encoded path handling. Small, proven, exactly what we need for universal path sanitization.

**What we reference (study, port the edge cases, write our own)**:
- `parser.py` (~2,038 lines): The 7 agent parsers contain valuable knowledge (Gemini SHA-256 hash resolution, Codex event-stream state machine, OpenCode SQLite schema, Kimi MD5 hashing, Claude Code tool result correlation). We reference these implementations when writing our own parsers that output opentraces.ai's enriched schema directly, including step-level token usage, parent_step hierarchy, system prompt extraction, tool definitions, and the data needed to construct `attribution` blocks. v0.1 ships only the Claude Code parser, with the adapter contract ready for multi-agent expansion.

**What we ship for DataClaw compatibility**:
- A `dataclaw` import adapter that reads existing DataClaw `conversations.jsonl` exports and enriches them with opentraces.ai schema fields where possible (adding `schema_version`, `outcome: null`, `environment`, `attribution: null`, etc.). This captures DataClaw's existing community without making them a runtime dependency.

**Why this positions against traces.com**: traces.com has 10 proprietary, closed-source adapters. Our own open-source parsers + vendored DataClaw security patterns + HF Hub infrastructure creates a fully open alternative. Contributors retain ownership of their data, training teams get immediate access via `datasets.load_dataset()`, and the community isn't dependent on either a VC-funded startup's continued operation (traces.com) or a protest-art project's release discipline (DataClaw).

### CLI-First, Agent-Operable Design

**Strategic decision**: opentraces.ai is a CLI tool first, designed to be operated by agents as easily as by humans. Like traces.com's CLI, it should work seamlessly as part of a Claude Code skill, a git hook, or a CI pipeline step.

**Rationale**:
- The primary integration point is a **git post-commit hook**: after a commit, `opentraces publish` runs automatically, enriching the trace with commit metadata (`outcome.committed: true`, `outcome.commit_sha`). Sessions that survive to a commit are higher-signal than abandoned sessions, this is a cheap, deterministic quality signal.
- **Agent-native JSON output** on every command (adopted from DataClaw's `---DATACLAW_JSON---` sentinel pattern and traces.com's `--json` flag). Every command emits structured JSON with `next_steps` and `next_command` fields so agents can chain operations.
- **Skill file** (`SKILL.md`) ships with the package for Claude Code integration, mirroring both DataClaw and traces.com's skill install patterns.
- **Structured errors** with `{code, kind, message, hint, retryable}` fields and a defined exit code vocabulary (0=OK, 2=usage, 3=missing config, 4=network, 5=data corrupt, 7=lock/busy), following CASS's robot-mode conventions.
- **Machine-discoverable API**: `opentraces capabilities --json` for feature/version discovery, `opentraces introspect --json` for full API schema, making the CLI self-documenting for other agents.

The CLI-first design also positions opentraces.ai for the git hook integration that traces.com pioneered. traces.com uses `refs/notes/traces` with a `traces:` prefix to link commits to sessions. opentraces.ai can adopt the same decentralized pattern with an `opentraces:` prefix, enabling "clone repo, see all traces" workflows without a centralized platform.

### Passive Capture Only (No Real-Time Hooks)

**Strategic decision**: opentraces.ai reads existing agent log files from disk after sessions end. No stop-hooks, no runtime instrumentation, no background daemons.

**Rationale**:
- Agent log files are already on disk after every session. There is nothing to "capture" that isn't already captured.
- Nobody consumes traces in real-time. Training pipelines run hours or days later.
- Real-time capture adds hook complexity, crash recovery, state management, and mid-session security exposure (secrets in flight before redaction), all for zero user-facing benefit.
- DataClaw's passive model has proven sufficient for 2k-star adoption. Zero friction > technical sophistication.
- This eliminates an entire class of bugs (hook registration failures, file locking, partial trace corruption) without sacrificing any capability.

### Three-Layer Architecture

opentraces.ai operates as three layers, with its own parsers (referencing DataClaw's edge-case handling) and vendored security modules:

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: INGESTION (opentraces.ai parsers)         │
│                                                     │
│  opentraces.discover_projects()                     │
│    → scans ~/.claude, ~/.codex, ~/.gemini, etc.     │
│  opentraces.parse_session()                         │
│    → adapter-per-agent (v0.1: Claude Code)          │
│    → outputs rich schema directly (steps, tokens,   │
│      parent_step, system_prompts, tool_definitions) │
│  Vendored from DataClaw (MIT):                      │
│    → anonymizer: path/username sanitization          │
│    → secrets: 19 regex + entropy + allowlist         │
│                                                     │
│  Output: opentraces.ai enriched session data        │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│  Layer 2: ENRICHMENT + SECURITY (opentraces.ai)     │
│                                                     │
│  Schema enrichment:                                 │
│    → Add schema_version, content_hash               │
│    → Construct attribution block from edit ops      │
│    → Extract dependencies from manifest files       │
│    → Correlate with git (committed, commit_sha)     │
│    → Compute metrics (cost, cache_hit_rate)         │
│    → Attach outcome signals (user annotation / CI)  │
│                                                     │
│  Security tiers:                                    │
│    → Tier 1 (Open): vendored patterns + extras      │
│    → Tier 2 (Guarded): classifier + escalation      │
│    → Tier 3 (Strict): CLI/web review interface      │
│                                                     │
│  Quality filter:                                    │
│    → Min 1 tool call, min 2 steps                   │
│    → Content dedup via SHA-256 content_hash          │
│                                                     │
│  Output: opentraces.ai enriched JSONL               │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│  Layer 3: HF EXPERIENCE (opentraces.ai platform)    │
│                                                     │
│  Upload:                                            │
│    → huggingface_hub SDK to personal dataset repos  │
│    → Auto-generated dataset card (richer than DC)   │
│    → Tagged 'opentraces' for community discovery    │
│    → Batched upload (local buffer, threshold flush)  │
│                                                     │
│  Contributor Dashboard (HF Space):                  │
│    → Personal analytics from your published traces  │
│    → Community comparisons and benchmarks            │
│    → The "hook" that makes contributing selfish      │
│    → See "Growth Loop" section below                 │
│                                                     │
│  Staged pipeline:                                   │
│    → auth → configure → review → publish            │
│    → Push hard-gated behind review completion       │
│    → Agent-native JSON output on every command      │
└─────────────────────────────────────────────────────┘
```

### Legacy Reference: Original 6-Stage Pipeline

> The original design assumed a custom capture pipeline with stop-hooks. The three-layer architecture above supersedes it by delegating ingestion to DataClaw. The enrichment, security, and upload stages remain, reorganized into Layers 2 and 3.

The original 6-stage pipeline was:

1. **Capture layer**: A stop hook that reads the transcript JSONL after each turn. Uses incremental file trawling (file-offset + inode + mtime tracking) to avoid re-ingesting large trace files. Appends to a local staging file.
2. **Deterministic parsing**: Splits raw input strings into system_prompt + tools + messages. Parses `tool_use`/`tool_result` blocks (which are interleaved across messages in Claude Code's format and must be merged). Links parent-child relationships via session_id. Deduplicates system prompts by hash. Assigns `agent_role` labels. Computes SHA-256 `content_hash` per step for dedup.
3. **Quality filter**: Rejects traces that do not meet minimum contribution thresholds (at least 1 tool call, at least 2 steps). Prevents noise from reaching any security tier.
4. **Universal path sanitization**: Strips `/Users/`, `/home/`, and similar filesystem path prefixes from all content. Applied unconditionally before any tier-specific processing, as this is too obviously necessary to be tier-dependent.
5. **Security layer**: Runs the configured tier's pipeline against the staged traces.
6. **Upload layer**: Uses `huggingface_hub` Python SDK to push approved traces to a HF dataset repo. Uploads in batches (local buffer with configurable threshold, default 10 traces) rather than per-turn, to reduce API calls and avoid partial records.

```
Claude Code session
    │
    ├─ stop hook fires after each response
    │
    ▼
┌─────────────────────┐
│  Capture + Stage    │  incremental trawl of ~/.claude/projects/...
│  (local JSONL)      │  offset + inode + mtime tracking
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Deterministic      │  parse tool_use/tool_result blocks
│  Parse              │  link parent-child, dedup system prompts
│                     │  assign agent_role, compute content_hash
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Quality Filter     │  min 1 tool call, min 2 steps
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Path Sanitization  │  strip /Users/, /home/ universally
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Security Pipeline  │  Tier 1: 17-pattern regex scan + auto-redact
│                     │  Tier 2: classify → flag → escalate
│                     │  Tier 3: buffer → post-session review
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Upload             │  huggingface_hub SDK
│  → HF Dataset Repo  │  batched upload (default threshold: 10)
└─────────────────────┘
```

**Tier 1 baseline sanitization**: DataClaw's 19 patterns (JWT, API keys by provider prefix, DB URLs, private keys, Bearer tokens, IPs, emails, high-entropy strings) serve as the floor. opentraces.ai extends with: credit card numbers (Luhn validation), SSNs, phone numbers. The full pattern list is maintained as a versioned configuration file, not hardcoded. Includes DataClaw's allowlist (noreply emails, Python decorators, private IPs, example URLs) plus extensions.

**Log-level redaction**: opentraces.ai's own debug logs must also be scrubbed of trace content. A `RedactingFilter` on all log handlers (following EloPhanto's pattern) prevents credentials from leaking into local logs during processing.

### Multi-Agent Extensibility

DataClaw provides the primary ingestion adapters for 7 agents via `dataclaw.parser`. opentraces.ai adds its own adapter layer for sources DataClaw doesn't cover, using a `typing.Protocol` interface (structural typing, not inheritance):

- **DataClaw-provided** (via `dataclaw.parser`): Claude Code, Codex, Gemini CLI, OpenCode, OpenClaw, Kimi CLI, Custom
- `adapters/dataclaw_export.py` — reads existing DataClaw `conversations.jsonl` exports from HuggingFace. Migration path for existing DataClaw users. Enriches with opentraces.ai schema fields.
- `adapters/claude_trace.py` — reads `.claude-trace/` JSONL (Zechner format with full request/response pairs). Richer source than DataClaw's transcript parser, capturing raw API traffic including system prompts and tool definitions.
- Future: `adapters/langfuse.py`, `adapters/otel_anthropic.py`, `adapters/cursor.py` (SQLite format)

**CASS / `franken_agent_detection` as v0.2 multi-agent accelerator**: CASS's `franken_agent_detection` Rust crate has working parsers for 19 agent formats: Claude Code, Codex, Cursor, Gemini, Aider, Cline, OpenCode, Amp, ChatGPT (including encrypted v2/v3), Copilot, Copilot CLI, Pi-Agent, Vibe, Kimi, Qwen, OpenClaw, Clawdbot, Crush, and Factory. This is the most comprehensive multi-agent parser available, nearly double traces.com's 10-adapter coverage. Three integration options for v0.2:
- **(a) FFI/subprocess**: Call CASS binary with `--robot` output from our Python pipeline. Simplest but adds a Rust binary dependency.
- **(b) Extract the crate**: Fork or convince the author to publish `franken_agent_detection` to crates.io, then write thin Python bindings (PyO3). Best option but blocked by the author's willingness and the unclear license.
- **(c) Port patterns**: Use CASS's connector implementations as reference for writing Python adapters. Most effort but no dependency. DataClaw's MIT-licensed parsers cover 7 agents, CASS's cover 19, between them all major agents have at least one reference implementation.

The author (Jeff Emanuel / Dicklesworthstone) also maintains `cross_agent_session_resumer`, which converts between agent session formats, relevant to our normalisation layer.

**Storage path reference** (from traces.com's 10-adapter survey + CASS's 19-connector coverage): Claude Code uses `~/.claude/projects/*/session.jsonl`, Cursor uses `~/Library/Application Support/Cursor/User/` (SQLite `state.vscdb`), Copilot uses SQLite via VS Code extension storage, OpenCode uses `.opencode/` directories (SQLite), OpenClaw uses `.openclaw/agents/main/sessions/*.jsonl`, Codex uses `~/.codex/sessions/` (SQLite state DB + JSONL rollouts), Gemini CLI uses `~/.gemini/tmp/` (chat JSON), Aider uses `~/.aider.chat.history.md` + per-project files, ChatGPT uses `~/Library/Application Support/com.openai.chat` (v1 unencrypted JSON, v2/v3 encrypted), Pi-Agent uses `~/.pi/agent/sessions/` (JSONL with thinking content), Amp uses `~/.local/share/amp` + VS Code storage.

### HF Hub Integration

Upload uses the `huggingface_hub` Python library (v1.5.0+):

- Each contributor pushes to a personal dataset repo (e.g., `username/opentraces-claude-code`)
- Or contributes to a shared community dataset via PR (e.g., `opentraces/agent-traces-v1`)
- JSONL files are appended, not overwritten, each upload adds new trace records
- Dataset card is auto-generated with schema documentation, contributor stats, and license info
- The dataset is immediately loadable via `datasets.load_dataset()` for training pipelines

**Two-layer storage model** (for consideration in v0.2+): HF Hub's git-backed Dataset repos work well for structured metadata, but storing 32K+ traces with full prompt text as JSONL in a git repo will hit friction at scale. A future architecture could split: Layer 1 (queryable metadata as Parquet columns) in an HF Dataset for filtering via Dataset Viewer API, Layer 2 (full trace content) in HF Storage Buckets ($12/TB/month, S3-like API, no git versioning overhead). For v0.1, JSONL in a Dataset repo is sufficient.

**Parquet conversion**: For v0.1, upload as JSONL. The `datasets` library can handle the conversion to Parquet automatically when users load with `datasets.load_dataset()`. Explicit Parquet dual-write is a v0.2 optimization when schema is stable.

**Agent ID vocabulary**: opentraces.ai adopts the emerging community convention for agent identifiers, shared across traces.com, DataClaw, and the Agent Trace spec: `claude-code`, `cursor`, `opencode`, `codex`, `gemini-cli`, `pi`, `amp`, `copilot`, `cline`, `openclaw`. Convergence on the same vocabulary reduces friction for users migrating between tools and enables cross-dataset analysis.

---

## What This Enables Downstream

### Training Data

The core value proposition. Real-world agent traces from diverse codebases, tasks, and developer skill levels create training signal that synthetic benchmarks cannot:

- **SFT on successful trajectories**: Filter to `outcome.success == true`, use as supervised fine-tuning data for coding agents
- **RL on trajectory pairs**: Compare successful vs failed attempts at similar tasks for reward model training
- **Tool-use training**: The structured tool_call/tool_result pairs are exactly the format needed for training tool-using models
- **Sub-agent orchestration**: Claude Code's explore→plan→execute pattern, captured in the `parent_step` hierarchy, provides training signal for agent orchestration strategies
- **Code attribution → reasoning reconstruction**: The embedded `attribution` block links specific code output (which lines, which files) back to the reasoning steps that produced them. No existing dataset provides this mapping. Researchers can train models on "given this code change, here is the reasoning that produced it" rather than treating the conversation and the code output as separate artifacts

### Reinforcement Learning

The Nemotron-RL dataset demonstrates the pattern: each trajectory has a `pass_rate` derived from test execution. opentraces.ai's `outcome` field serves the same purpose but accepts richer signals — user annotations, CI results, or post-hoc evaluation. This makes the data usable for:

- RLHF on code agent behaviour
- Process reward models (step-level rewards from the turn structure)
- Outcome reward models (trajectory-level rewards from the outcome field)

### Telemetry and Research

Beyond training, the dataset enables:

- **Agent behaviour analysis**: How do developers actually use code agents? What tasks succeed/fail? Where do agents waste tokens?
- **Cache efficiency studies**: The `cache_hit_rate` and token breakdown data lets researchers study context engineering strategies
- **Cross-agent comparison**: With multiple adapters contributing to the same schema, researchers can compare how different agents approach similar tasks
- **Cost modelling**: Real-world cost data from diverse usage patterns

### Dependency-Based Dataset Discovery

The `dependencies` field enables a powerful discovery and filtering dimension. Package registries serve as a natural taxonomy for organizing trace data:

- **Library-specific datasets**: "Show me all sessions involving the Stripe CLI" or "all sessions working with Prisma ORM." Researchers fine-tuning models for specific ecosystems can filter by dependency rather than guessing from code content.
- **Searchable on HF Hub**: Dependencies can be surfaced as HF dataset tags (e.g., `dependency:stripe`, `dependency:prisma`), enabling HF Hub's built-in search and filtering. A dedicated discovery website could also index traces by dependency for richer querying.
- **Commit-signal quality filtering**: Combined with `outcome.committed`, dependency metadata enables queries like "all committed sessions using Next.js", which represents the highest-quality training signal: real work, with real dependencies, that a developer considered worth committing.

---

## Growth Loop: Contributor Dashboard

### The Problem With Pure Altruism

DataClaw asks users to publish their traces for the good of the open-source community. The result: 2k stars, ~10-12 actual contributors. The conversion from "cool idea" to "I'll share my data" is ~0.5%. The bottleneck isn't tooling, it's motivation.

### The Hook: Selfish Reasons to Share

opentraces.ai solves this by giving contributors something back. Publish your traces, get a personal analytics dashboard for free. The dashboard is the product for contributors; the dataset is the product for the training community.

**How this differs from traces.com's analytics**: traces.com offers profile pages and team analytics, but their analytics are **usage-oriented** (top agents used, average session length, AI output percentage, message type distribution). opentraces.ai's dashboard is **efficiency-oriented**: cost per successful outcome, cache hit rates, tool selection patterns, model performance comparison on your specific task types, success/failure rates across projects. traces.com shows you _what happened_, opentraces.ai shows you _how to get better_. This distinction matters because our schema captures the per-step token counts, cost data, and outcome signals that traces.com's schema does not.

**What the dashboard shows** (HuggingFace Space, reads from the user's published dataset repo):

- **Sessions over time**: Activity trends, models used, daily/weekly patterns
- **Token spend tracking**: Input/output/cache breakdown, cost estimates per session and cumulative
- **Tool usage breakdown**: Which tools do you lean on? How does that compare to the community average?
- **Success/failure rate**: Across projects, models, task types (once outcome signals exist)
- **Efficiency score**: "Your coding agent efficiency" compared to community benchmarks (tokens per successful outcome, cache hit rates, tool calls per task)
- **Model comparison**: How do different models perform on your specific types of tasks?
- **Language/ecosystem breakdown**: What you work on, session duration by domain
- **Shareable profile**: "Here's my coding agent stats", the kind of thing developers post on Twitter

### Why This Works

1. **People are genuinely curious** about their own agent usage but have no way to see it today. There is no "Spotify Wrapped for coding agents."
2. **Community comparisons create social proof pressure**: "Other devs use agents 3x more efficiently" makes you want to improve (and keep contributing data to keep your dashboard fresh).
3. **It's a reason to keep contributing**, not just a one-time export. Fresh data = fresh dashboard = fresh insights.
4. **It's shareable**: Developer identity is increasingly tied to AI tool usage. A public stats profile is the new GitHub contribution graph.
5. **It's cheap to build**: The structured JSONL is already there. The dashboard is a Gradio/Streamlit HF Space that reads from the user's dataset repo. No backend, no auth beyond HF login, no infrastructure cost.

### Implementation

- **v0.1.1**: Basic dashboard as a HuggingFace Space. Enter your HF username, see stats from your `opentraces` dataset. Sessions over time, model distribution, token usage, tool breakdown.
- **v0.2**: Community comparisons (percentiles, benchmarks). "You're in the top 20% for cache efficiency." Shareable profile cards.
- **v0.3**: Recommendations based on community patterns. "Users who switched from Sonnet to Opus on refactoring tasks saw 40% fewer tool calls." This is where the flywheel really spins, the more data in the system, the better the recommendations, the more reason to contribute.

### The Pitch

> Share your agent traces, get insights about how you code with AI. Your data helps the open-source training community, and you get a free analytics dashboard that no one else offers.

---

## Competitive Positioning

### Capture, Sharing, and Observability Tools

| Project        | Captures                 | Normalises          | Sanitises                             | Uploads         | Trains                  | Analytics               |
| -------------- | ------------------------ | ------------------- | ------------------------------------- | --------------- | ----------------------- | ----------------------- |
| **traces.com** | ✅ (10 agent adapters)   | ✅ (5 message types + AI summaries/embeddings) | ✅ (automatic scrub + `[REDACTED]`) | Own platform (proprietary) | ❌ (no outcome signals, no per-step tokens) | ✅ (team dashboards, profiles, community feed) |
| **DataClaw**   | ✅ (7 agent log readers) | ✅ (own JSONL)      | ✅ (19 regex + entropy + attestation) | ✅ (HF Hub)     | ❌ (no outcome signals) | ❌                      |
| `claude-trace` | ✅ (fetch interception)  | ❌                  | ❌                                    | ❌              | ❌                      | ❌                      |
| Langfuse       | ✅ (OTel hooks)          | ✅ (OTel)           | ❌                                    | ❌              | ❌                      | ✅ (internal)           |
| OpenAmnesia    | ✅ (multi-agent)         | ✅ (best in class)  | ❌ (stores PII verbatim)              | ❌              | ❌                      | ❌                      |
| ClaudeBin      | ✅ (Claude Code only)    | Partial (own types) | Partial (path sanitize only)          | Own platform    | ❌                      | ❌                      |
| EloPhanto      | ✅ (own agent)           | ✅ (OpenAI chat)    | ✅ (17+14 regex patterns)             | ✅ (HF via API) | Planned                 | ❌                      |
| **CASS**       | ✅ (19 auto-discovery)   | ✅ (best-in-class, unified model with Snippet extraction) | ✅ (regex + entropy + severity tiers) | ❌ (local search only) | ❌ | ❌ |

### Standards and Formats

| Project                   | Focus                             | Relevance to opentraces.ai                                                                             |
| ------------------------- | --------------------------------- | --------------------------------------------------------------------------------------------------- |
| ATIF / Harbor (v1.6)      | Training trajectory serialization | Direct schema competition; has RL-ready fields (logprobs, token IDs, tool_definitions)              |
| ADP (Agent Data Protocol) | Interlingua for training data     | Used to normalize 1.3M+ trajectories; O(D+A) adapter argument                                       |
| Agent Trace spec (Cursor) | Code attribution (file/line)      | Embedded, not just complementary; opentraces.ai includes Agent Trace attribution blocks to bridge trajectory + output. 10+ corporate backers (Cloudflare, Vercel, Google Jules, Cognition) become potential adopters. |

### Existing Datasets

| Dataset                              | Size | Source                     | Schema Quality | Key Feature                                                                |
| ------------------------------------ | ---- | -------------------------- | -------------- | -------------------------------------------------------------------------- |
| NVIDIA Nemotron-RL                   | ~34K | OpenHands on SWE-bench     | ✅ Structured  | Benchmark-only, single agent                                               |
| Nebius/SWE-rebench                   | 67K  | OpenHands + Qwen3-Coder    | ✅ Structured  | Largest open trajectory dataset                                            |
| TogetherAI/CoderForge                | 51K  | Multi-agent                | ✅ Structured  | Boosted SWE-Bench to 59.4%                                                 |
| nlile/misc-merged-claude-code-traces | 32K  | 10 merged sources          | ✅ 16 fields   | Deduplicated via content_hash, parsed tool_use                             |
| PatronusAI/TRAIL                     | 148  | OTel/OpenInference         | ✅ Annotated   | Only dataset with structured error categorization                          |
| EloPhanto dataset                    | 475  | Single contributor         | Partial        | Only working end-to-end capture-to-HF pipeline                             |
| **DataClaw community** (32 repos)    | ~2K+ | 7 agents, ~25 contributors | Partial        | Largest crowdsourced effort, but shallow schema (no outcome, no hierarchy) |

### Downstream Consumers

| Project             | What it does                                    | Relationship to opentraces.ai                               |
| ------------------- | ----------------------------------------------- | -------------------------------------------------------- |
| huggingface/upskill | Trace-to-skill extraction CLI (teacher-student) | opentraces.ai is upstream; produces traces upskill consumes |
| CASS / `franken_agent_detection` | 19-agent session parser + unified search (Rust) | Potential upstream: its parsing crate covers 19 agent formats we'd need adapters for. If published as a standalone dep, could replace our adapter layer for v0.2 multi-agent support. Also: `cross_agent_session_resumer` (same author) converts between agent formats. |

### opentraces.ai's Position

|                | Captures     | Normalises | Sanitises    | Uploads     | Trains                      | Analytics               |
| -------------- | ------------ | ---------- | ------------ | ----------- | --------------------------- | ----------------------- |
| **opentraces.ai** | Via adapters | ✅         | ✅ (3 tiers) | ✅ (HF Hub) | ✅ (schema designed for it) | ✅ (contributor dashboard, efficiency-focused) |

**traces.com is building the GitHub of agent sessions**, a centralized proprietary platform for sharing and team collaboration. **opentraces.ai is building the Commons of agent traces**, open data on open infrastructure for the ML training ecosystem. These serve different primary consumers (team workflows vs training pipelines) but overlap on the sharing/analytics axis, where opentraces.ai differentiates on four fronts:

1. **Open data, not walled garden**: Traces published to HF Hub in standard JSONL/Parquet, directly consumable by `datasets.load_dataset()`. No vendor lock-in, no proprietary API dependency, no undisclosed pricing. Contributors own their data under CC-BY-4.0.
2. **ADP + Agent Trace, the unified format**: opentraces.ai is the only tool that bridges trajectory data (what the agent thought and did) with code attribution (which lines it produced). By embedding Agent Trace-compatible attribution blocks inside trajectory records, every trace is a complete record of process + output. Neither DataClaw, traces.com, ATIF, nor Agent Trace alone provides this mapping. This is the single strongest differentiator.
3. **Training-first schema depth**: Outcome signals for RL/reward modeling, sub-agent hierarchy for orchestration research, per-step token counts and cost data for efficiency analysis, commit-signal quality filtering, dependency-based dataset discovery, schema versioning for pipeline stability. traces.com's 5-type message classification is optimized for human browsing, not model training.
4. **Configurable security, not one-size-fits-all**: Three tiers (Open/Guarded/Strict) with per-project configuration, versus traces.com's single automatic scrub with no user control over sensitivity thresholds.

The competitive differentiation is not capture (commodity, both traces.com and DataClaw do this) or storage (HF provides this). It is the combination of the trajectory + attribution bridge, training-ready schema depth, configurable security tiers, open infrastructure, and contributor incentives (the analytics dashboard) that no competitor delivers.

---

## Competitive Analysis: traces.com

> **Source**: Product analysis of [traces.com](https://traces.com/) and [traces.com/docs](https://traces.com/docs) (March 2026). Built by Lab 0324, Inc. (operates as market.dev). Full scouting brief at `kb/background-research/04-traces-com.md`.

traces.com is opentraces.ai's primary closed-source competitor. It is the most polished product in the agent session sharing space, a proprietary SaaS with excellent CLI DX, 10 agent adapters, team collaboration, and a community feed. Understanding where it leads and where it falls short is essential for positioning.

### What traces.com Is

A proprietary platform (CLI + web app) that captures conversations between developers and AI coding agents, normalizes them into a unified format, and makes them shareable. Tagline: "Make Coding Agents Multiplayer." Closed-source, GitHub-only auth, Convex backend, undisclosed pricing ("billing docs coming soon"). 53 npm versions in its first month, active iteration.

### What traces.com Gets Right (Patterns to Learn From)

1. **Adapter architecture with auto-detection**: 10 agents (Claude Code, Cursor, Codex, Gemini CLI, OpenCode, Pi, Amp, Copilot, Cline, OpenClaw) with directory-signature-based auto-detection. Session resolution priority system (env var > source path > directory match > agent fallback) is well-designed. Both traces.com and DataClaw independently converged on this pattern, validating it as the correct approach.

2. **CLI DX**: Multiple install paths (Homebrew, npm, curl), `--json` on all commands, `--follow` for real-time streaming, `--agent auto` for zero-config, interactive TUI for browsing. This is the bar for CLI polish.

3. **Git integration via post-commit hooks + git notes**: Elegant, decentralized pattern. `traces:` prefix in `refs/notes/traces` prevents collisions. CLI discovery from git notes enables "clone repo, see all traces" without manual imports. This pattern works equally well with decentralized backends like HF Hub.

4. **Server-side AI processing**: Auto-generated titles, summaries, and 64-dim embeddings for semantic search. Adds real discovery value on top of raw trace storage. opentraces.ai could replicate this client-side before upload or as a HF Space post-processor (deferred to v0.2).

5. **Privacy defaults**: `direct` visibility as default for individuals, `private` for orgs. Team-level visibility policies. These defaults protect users from accidental exposure without adding friction.

6. **Non-human identities**: Agent identities for CI/CD attribution (separate from human users, linked to API keys). Thoughtful enterprise feature that shows maturity in thinking about automated workflows.

### Where opentraces.ai Differentiates

| traces.com Limitation | opentraces.ai Advantage | Why It Matters |
| --- | --- | --- |
| **Proprietary, data locked in Convex** | Open data on HF Hub, `datasets.load_dataset()` ready | Training pipelines need bulk data access, not proprietary APIs |
| **Sharing-first schema** (5 message types, trace-level aggregates only) | Training-first schema (per-step tokens, cost, reasoning, outcome signals) | SFT/RL require step-level metadata that traces.com does not capture |
| **No outcome signals** | `outcome` field: `{success, signal_source, description, patch}` | Without success/failure signals, traces cannot train reward models |
| **No sub-agent hierarchy** | `parent_step` links + `agent_role` labels + `subagent_trajectory_ref` | Claude Code spawns subagents every session, flattening loses orchestration signal |
| **Single automatic scrub, no user control** | 3 configurable security tiers (Open/Guarded/Strict) per-project | One-size-fits-all fails: too permissive for enterprise, too restrictive for open-source |
| **No per-message metadata** | Per-step timestamps, token counts (input/output/cache), cost data | Critical for training data quality, cost analysis, and cache efficiency research |
| **No environment metadata** | `environment` block: OS, shell, VCS (base_commit, branch, diff), language_ecosystem | Enables filtering by ecosystem, reproducing conditions, and correlating with outcomes |
| **No schema versioning** | `schema_version: "0.1.0"` with migration path | Format changes in traces.com break consumers silently |
| **Undisclosed pricing** | Free forever (HF Spaces, no backend infrastructure) | No pricing uncertainty, no vendor dependency |
| **GitHub-only auth** | HF auth (broader ML/research community) | HF Hub is where the training community lives |
| **No export/download** ("coming soon") | Data is yours from day 1 (JSONL on HF) | Contributors own their data under CC-BY-4.0 |

### What We Don't Need to Replicate

These are traces.com features that serve its team collaboration mission but are outside opentraces.ai's open data mission:

1. **Team collaboration SaaS** (orgs, roles, invites, namespace management): Our focus is open data contribution, not team workflow management. HF Hub organizations serve this need for users who want it.
2. **Real-time Convex backend**: We push to HF datasets, no need for real-time sync or a managed database.
3. **PR bot comments** (`tracebot`): Nice-to-have but not core to the training data mission. Can be built as a GitHub Action consuming HF dataset traces.
4. **Follow mode** (`--follow` for real-time session streaming): Our passive capture model (read logs after session ends) makes this unnecessary.

### Strategic Positioning vs traces.com

**Framing**: traces.com positions as "Make Coding Agents Multiplayer", a team productivity tool. opentraces.ai should position as **"Open infrastructure for the ML training ecosystem"**, a public good that gives contributors analytics in return. These frames are complementary: a user could share a trace on traces.com for team review AND contribute it to opentraces.ai for training data. The relationship is GitHub (proprietary, collaboration-focused) vs Commons (open, research-focused), not a zero-sum competition.

**Recruiting argument**: traces.com's proprietary lock-in is our strongest argument for open-source advocates. No export, no self-hosting, no code inspection, single-company dependency (small team, unclear funding). Every trace contributed to traces.com is data the training community cannot access. Every trace contributed to opentraces.ai is immediately available to the entire ML ecosystem.

**Where traces.com could outflank us**: If traces.com adds bulk export to HF Hub (their "coming soon" download feature), or if they open-source their adapters, the gap narrows. Our moat is schema depth (outcome signals, sub-agent hierarchy, per-step metadata) and the contributor dashboard, which require the richer data our schema captures.

---

## Competitive Analysis: DataClaw

> **Source**: Deep code analysis of [github.com/peteromallet/dataclaw](https://github.com/peteromallet/dataclaw) (v0.3.2, Feb 2026). Full scouting brief at `kb/background-research/05-dataclaw.md`.

DataClaw is opentraces.ai's primary open-source competitor and our chosen ingestion dependency. It is the closest existing implementation to what opentraces.ai aims to build on the open-data side. Understanding exactly what it does, what it doesn't, and where we can surpass it is critical.

### What DataClaw Is

A Python CLI (~4,100 LOC, single dependency: `huggingface_hub`) that passively reads log files from 7 coding agents, anonymizes/redacts PII and secrets, and publishes structured JSONL to HuggingFace. Framed as a "performance art project" protesting Anthropic's distillation-prevention policies. 2,005 stars, 234 forks, ~32 HF datasets published (of which ~10-12 are unique original contributions) in its first month.

### Market Validation

DataClaw proves three things we assumed but couldn't confirm:

1. **Real demand exists** for crowdsourced agent trace sharing (2k stars in 1 month)
2. **Passive log reading is sufficient** for v1, no runtime instrumentation needed
3. **Agent-driven CLI workflows work**, their JSON output with `next_steps`/`next_command` is heavily used

### Feature Comparison: Match vs Differentiate

#### Features We MUST Match (Table Stakes)

These are features DataClaw ships today that users will expect from any competitor:

| Feature                         | DataClaw Implementation                                                                                                                                                                                                  | opentraces.ai Approach                                                                                                                                                                                                                                                   |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Passive log reading**         | Reads existing `~/.claude/projects/`, `~/.codex/sessions/`, etc. No agent modification.                                                                                                                                  | Same, via adapter pattern. Must support at minimum Claude Code + Codex at launch.                                                                                                                                                                                     |
| **Multi-agent support**         | 7 sources: Claude Code, Codex, Gemini CLI, OpenCode, OpenClaw, Kimi CLI, Custom                                                                                                                                          | Adapter-based. v0.1 = Claude Code only, but adapter contract must be ready for multi-agent from day 1. Consider shipping Codex adapter in v0.1 since DataClaw already has it.                                                                                         |
| **Secret detection**            | 19 regex patterns + Shannon entropy analysis + allowlist for false positives. Covers JWT, API keys (Anthropic/OpenAI/HF/GitHub/PyPI/NPM/AWS/Slack/Discord), private keys, DB URLs, Bearer tokens, IPs, emails.           | Our Tier 1 baseline has 17 patterns. **Must match or exceed DataClaw's 19-pattern coverage.** Their patterns are MIT-licensed, we can reference them directly. Add: PyPI tokens (`pypi-...`), Discord webhooks, high-entropy string detection with entropy threshold. |
| **Username/path anonymization** | SHA-256 hash of system username (8-char hex prefix), strips `/Users/`/`/home/` to project-relative paths, handles macOS hyphen-encoded paths. Extra usernames (GitHub handles) configurable.                             | Our universal path sanitization covers this. Must also implement username hashing and configurable extra usernames.                                                                                                                                                   |
| **Staged pipeline with gates**  | 4 stages: auth -> configure -> review -> confirmed/done. Push is hard-gated behind `confirm`. Cannot accidentally publish without completing the full pipeline.                                                          | Implement equivalent stage gating. Our 3 security tiers replace the single "review" stage but the overall flow (auth -> configure -> review -> publish) should match.                                                                                                 |
| **Agent-native CLI output**     | Every command emits JSON with `next_steps` array and `next_command` field. `---DATACLAW_JSON---` sentinel separates human text from machine-parseable JSON. Bundled `SKILL.md` installs into Claude Code's skill system. | **Must adopt this pattern.** Our `--json` flag is opt-in; DataClaw's is always-on. Consider defaulting to JSON output with a `--human` flag for pretty-printing, or matching their sentinel pattern. Ship a `SKILL.md` equivalent.                                    |
| **Custom redaction strings**    | `--redact "string1,string2"` appends to config, persists across runs. Masked in config display.                                                                                                                          | Must support. Add to our config surface.                                                                                                                                                                                                                              |
| **Project exclusion**           | `--exclude "project1,project2"` to skip specific projects.                                                                                                                                                               | Must support. Our per-project consent model already covers this.                                                                                                                                                                                                      |
| **HF dataset tagging**          | All exports tagged `dataclaw` for community discovery. Browse at `huggingface.co/datasets?other=dataclaw`.                                                                                                               | Tag all exports `opentraces`. Consider also tagging with `agent-traces` as a shared community tag.                                                                                                                                                                    |
| **Auto-generated dataset card** | README.md with YAML frontmatter, model distribution, schema docs, token counts, load snippet.                                                                                                                            | Must match. Our auto-generated card should be richer (include outcome signal distribution, security tier used).                                                                                                                                                       |
| **Custom source directory**     | `~/.dataclaw/custom/<project>/*.jsonl` for injecting data from unsupported agents.                                                                                                                                       | Adopt same pattern at `~/.opentraces/custom/` or via the adapter contract.                                                                                                                                                                                            |
| **Two-pass PII scanning**       | First pass during parsing (anonymizer + secrets), second pass during `confirm` as verification.                                                                                                                          | Our Tier 1 + Tier 3 model already provides this. Tier 1 is first pass, Tier 3 review is second pass.                                                                                                                                                                  |

#### Features Where We Differentiate (Competitive Advantages)

These are gaps in DataClaw's design that opentraces.ai fills:

| Gap in DataClaw                    | opentraces.ai Solution                                                                                  | Why It Matters                                                                                                                                                                                             |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **No outcome signals**             | `outcome` field: `{success: bool, signal_source, description, patch}`                                | Critical for RL/reward modeling. Without outcome signals, traces are only useful for SFT on raw conversations. DataClaw's schema cannot train reward models.                                               |
| **No sub-agent hierarchy**         | `parent_step` links + `agent_role` labels (`main`, `explore`, `plan`) + `subagent_trajectory_ref`    | Claude Code spawns subagents on every session. DataClaw includes subagent JSONL but flattens the hierarchy. opentraces.ai preserves the delegation tree, enabling research on agent orchestration strategies. |
| **Single security tier**           | 3 tiers: Open (regex auto-redact), Guarded (classifier + escalation), Strict (human-in-the-loop) | DataClaw's single tier is too permissive for teams with sensitive codebases and too restrictive for open-source contributors who want zero friction. Configurable tiers per-project is the right model.    |
| **No environment metadata**        | `environment` block: OS, shell, VCS (base_commit, branch, diff), language_ecosystem                  | Enables filtering by ecosystem, correlating trace quality with environment factors, and reproducing conditions.                                                                                            |
| **No code attribution**            | Embedded Agent Trace-compatible `attribution` block linking files/lines to conversation steps         | The single biggest differentiator. DataClaw captures conversations but cannot tell you which lines of code the agent actually produced. opentraces.ai bridges trajectory and attribution.                  |
| **No git diff/commit correlation** | `task.base_commit`, `outcome.patch` (unified diff), `outcome.committed`, `outcome.commit_sha`        | Enables matching traces to code changes, essential for SWE-bench-style evaluation. Committed sessions are higher-signal than abandoned ones.                                                               |
| **No cost tracking**               | `metrics.estimated_cost_usd`, per-step `token_usage` with cache breakdown                            | Enables cost-efficiency research and practical cost modeling for teams evaluating agents.                                                                                                                  |
| **No schema versioning**           | `schema_version: "0.1.0"` field + migration path                                                     | DataClaw's schema has no version field. Format changes break consumers silently.                                                                                                                           |
| **No content deduplication**       | `content_hash` (SHA-256) at trace level                                                              | DataClaw has no dedup mechanism. The 32 HF datasets include many mirrors/copies with no way to detect overlap.                                                                                             |
| **No quality filtering**           | Min 1 tool call + min 2 steps threshold                                                              | DataClaw exports everything, including trivial interactions. Noise degrades dataset quality for training consumers.                                                                                        |
| **No system prompt dedup**         | `system_prompt_hash` + dedup map                                                                     | A 20K-token system prompt repeated 92 times across steps is catastrophically wasteful. DataClaw includes system prompts inline.                                                                            |
| **No tool definitions**            | `tool_definitions` at session level + `tools_available` per step                                     | Critical for training models to select the right tool. DataClaw captures tool _usage_ but not the available tool set.                                                                                      |
| **No reasoning content**           | `reasoning_content` field per step (extended thinking / chain-of-thought)                            | DataClaw includes `thinking` as an optional field on assistant messages but does not structure it. Our per-step reasoning field aligns with ATIF and supports CoT research.                                |
| **No standards alignment**         | References Agent Trace, ATIF, ADP, OTel conventions                                                  | DataClaw's schema is ad-hoc. Our alignment with established standards makes traces immediately compatible with existing training pipelines.                                                                |
| **Ad-hoc dataset governance**      | Canonical aggregated dataset option + federated personal repos                                       | DataClaw's federated-only model (each user publishes to their own repo) creates discovery friction. We offer both models.                                                                                  |
| **No annotation support**          | Tier 3 review interface supports per-trace annotations and quality signals                           | DataClaw has no annotation mechanism. Annotations are the bridge from raw traces to RL-ready datasets.                                                                                                     |

#### DataClaw Design Patterns to Adopt

These are implementation patterns from DataClaw's codebase worth incorporating:

1. **Append-only config merging** (`cli.py`): `--exclude`, `--redact`, `--redact-usernames` always append (never replace), making the CLI safe to call repeatedly. Prevents accidental data loss from config overwrites.

2. **Pre-pass tool result correlation** (`parser.py:682`): Before processing Claude Code sessions, DataClaw builds a `tool_result_map` that correlates `tool_use_id` -> `tool_result` entries. This handles the interleaved format cleanly. Our deterministic parser should use the same pattern.

3. **Semantic attestation validation** (`cli.py:849-926`): Rather than just checking "did the user type yes", DataClaw validates that attestation text contains specific keywords ("ask", "scan", "manual", session count >= 20). This is a creative accountability measure for agent-driven flows.

4. **Gemini hash resolution** (`parser.py:57-119`): Gemini CLI hashes project paths with SHA-256 for directory names. DataClaw reverses this by hashing `$HOME` subdirectories and matching, or extracting paths from tool call arguments. If we ever add a Gemini adapter, this is the reference implementation.

5. **`---DATACLAW_JSON---` sentinel pattern** (`cli.py`): Separating human-readable output from machine-parseable JSON in a single stdout stream. Simple but effective for dual-audience CLI output.

6. **Kimi MD5 project hashing** (`parser.py:279`): Kimi CLI uses MD5 of CWD for session directory names. DataClaw implements the reverse mapping.

#### DataClaw Weaknesses to Exploit

These are real limitations found in code review that our positioning can highlight:

1. **Silent parser failures**: Non-custom source parsers return `None` on errors without logging (`parser.py` various `_parse_*` functions). Missing sessions are invisible to users. We should log all parse failures with context.

2. **No integration tests**: All tests are unit-level with mocking. No end-to-end test confirms the full pipeline. We should ship integration tests that exercise the real pipeline.

3. **Config file permissions**: `~/.dataclaw/config.json` stores `redact_strings` (which may contain real secrets) with default permissions, no `chmod 0600`. We should harden config file permissions from day 1.

4. **Skill fetch without integrity check**: `update-skill` downloads `SKILL.md` from GitHub with no checksum verification. We should sign or hash our distributed skill files.

5. **Version drift**: `pyproject.toml` says `0.3.2`, `__init__.py` says `0.3.0`. Signals rapid iteration without release discipline.

6. **Publish on every main push**: CI publishes to PyPI on every merge to `main`, not on tagged releases. Combined with `skip-existing`, this is fragile.

### Strategic Positioning

**Decision: Vendor DataClaw's security modules, reference its parsers, ship our own pipeline.**

opentraces.ai vendors DataClaw's `secrets.py` + `anonymizer.py` (~380 lines, MIT) for proven security patterns, references its parser implementations for agent-specific edge cases, and ships its own parsers that output the enriched schema directly. A DataClaw JSONL import adapter captures their existing community. This:

1. Eliminates runtime dependency on a 1-month-old project with version drift and fragile CI
2. Enables parsers to output our rich schema directly (attribution blocks, step-level tokens, parent_step hierarchy) without re-reading raw files
3. Captures DataClaw's community via import adapter (migration path, not dependency)
4. Differentiates on the trajectory + attribution bridge (ADP + Agent Trace), schema depth (outcome signals, sub-agent hierarchy, environment metadata, dependencies, commit signals)
5. Differentiates on security flexibility (3 tiers vs 1) and developer experience (contributor dashboard, HF-native integration)
6. Adds a growth loop DataClaw structurally cannot build (the dashboard requires a platform, not just a CLI)
7. Avoids branding risk from DataClaw's "performance art protest" framing

**Framing**: DataClaw frames itself as protest art. opentraces.ai should frame itself as the ADP + Agent Trace bridge, open infrastructure for the training community that connects agent conversations to code output. A constructive, neutral tool focused on data quality, downstream utility, and giving contributors something back (the dashboard). This broadens adoption to enterprise and institutional users who would avoid a "protest" tool.

**Timeline**: Writing our own Claude Code parser (v0.1) is comparable effort to writing the enrichment layer that would sit on top of DataClaw. The parser IS the enrichment, they're the same code when you output the rich schema directly. Multi-agent expansion (Codex, Gemini, etc.) follows in v0.2, referencing DataClaw's and CASS's implementations for each agent's edge cases.

---

## Open Questions

### Resolved by Research

These questions from the original intent have been answered by the background research:

- **Licensing** (was Q4): CC-BY-4.0 is confirmed as the community standard. Agent Trace spec, Nemotron-RL, and the dominant HF trace datasets all use it. **Decision: default to CC-BY-4.0.** Contributors who need different terms can use their personal dataset repos with different licenses.

- **Redaction fidelity** (was Q5): Every shipping tool (EloPhanto, traces.com, ClaudeBin) uses `[REDACTED]` replacement. No production system implements synthetic replacements. **Decision: use `[REDACTED]` for v0.1.** Synthetic replacement is a research problem, not a v0.1 engineering problem. The training-data quality impact is real but accepted industry-wide.

- **Sub-agent trace depth** (was Q6): The ATIF `subagent_trajectory_ref` pattern resolves this cleanly. Capture full sub-agent transcripts as separate trajectory records linked by session_id. This separates storage depth from schema design, you can capture full depth without bloating the parent record. **Decision: link by reference, capture full depth.** However, see new question on v0.1 sub-agent inclusion policy below.

- **Incremental vs batch upload** (was Q7): EloPhanto's production pattern (local SQLite buffer with configurable `batch_size`, default 10) is validated. Continuous per-turn upload creates excessive API calls and partial records. **Decision: batch with local buffer, threshold-based flushing.**

- **Deduplication** (was Q8): The nlile dataset demonstrates the answer: add a `content_hash` field (SHA-256) at upload time. Handle deduplication at the dataset merge step, not client-side. Agent Trace's `content_hash` (murmur3) provides a mechanism for cross-trace structural similarity. **Decision: `content_hash` in schema, dedup at dataset level.**

### Remaining Open Questions

1. **Consent granularity**: Should consent be per-session, per-project, or per-turn? Per-session feels right as the default, but per-project persistence (always share traces from this open-source repo) is a quality-of-life feature.

2. **Annotation workflow**: How much annotation burden is acceptable? Bare minimum is the outcome signal (success/fail). Ideal is step-level quality ratings. Where's the sweet spot?

3. **Dataset governance**: Should there be a single canonical `opentraces/agent-traces` dataset, or a federated model where each contributor maintains their own? The nlile dataset (32K records merged from 10 sources) demonstrates the federated-then-merge pattern. A canonical dataset with community PRs has better discoverability but creates curation overhead.

### New Strategic Questions (Raised by Research)

4. **ATIF alignment strategy**: Should opentraces.ai (a) define its own schema (current approach), (b) output ATIF-compatible records, or (c) treat ATIF as the target format and position opentraces.ai purely as the capture-sanitise-upload pipeline? Option (c) simplifies schema work and makes output immediately compatible with ATIF-consuming training pipelines. Option (a) gives full control but risks fragmentation. **This is the single most important decision before implementation.**

5. **AAIF standards alignment**: The Agentic AI Foundation (Anthropic, OpenAI, Block/Linux Foundation) governs AGENTS.md, MCP, and Goose. Should opentraces.ai align with AAIF standards from the start? MCP is becoming the universal tool interface, the adapter design should consider MCP-compatible tool definitions as the output format.

6. **Reward signal standardization**: Neither ADP nor ATIF standardize how to attach reward signals (human feedback, test pass/fail, benchmark scores) to trajectories for RLHF. opentraces.ai's `outcome` field is doing this work informally. Should opentraces.ai propose a convention, or wait for the standards bodies?

7. **v0.1 sub-agent inclusion policy**: Should the Claude Code adapter read `subagents/*.jsonl` in v0.1? OpenAmnesia explicitly excludes them. Sub-agent transcripts have higher sensitive-data surface area (they operate more autonomously, make more filesystem reads). But every Claude Code session spawns subagents, a v0.1 trace without them is structurally incomplete. The recommendation from research: include sub-agent steps with `parent_step` links in v0.1, but defer full sub-agent transcript capture (the linked separate trajectory records) to v0.2.

8. **Quality filtering threshold**: What is the minimum viable trace for dataset contribution? EloPhanto enforces min 1 tool call + min 2 turns. Is this the right threshold, or should opentraces.ai be more/less aggressive?

9. **Warm-up call handling**: Claude Code traces include calls with `session_id: "0"` and empty outputs that exist purely for KV cache priming. These are noise for SFT but signal for caching research. Include with `call_type: "warmup"` label, or filter at normalisation?

10. **`output_summary` length**: For tool result summaries, how long is useful? Too short is useless, too long defeats the purpose. This affects the enrichment pipeline design.

### DataClaw-Specific Strategic Questions (Raised by Competitive Analysis)

11. **DataClaw import adapter priority**: Should we ship a DataClaw JSONL import adapter in v0.1? Low effort (their format is simple), high compatibility value, captures existing community. But could also signal "we're a DataClaw wrapper" rather than "we're the better product."

12. **Codex adapter in v0.1**: DataClaw supports 7 agents. Should we ship Claude Code + Codex adapters in v0.1 to reduce the agent coverage gap, or stay focused on Claude Code only and nail the schema depth differentiator?

13. **Community tag strategy**: DataClaw uses `dataclaw` tag. Should we use `opentraces` only, or also tag with a shared community tag like `agent-traces` that encompasses both DataClaw and opentraces.ai datasets for broader discoverability?

14. **Schema migration for DataClaw users**: If someone has already published a DataClaw dataset, should opentraces.ai offer a `opentraces migrate --from dataclaw` command that enriches their existing export with opentraces.ai schema fields (adding `schema_version`, `outcome: null`, `environment`, etc.)?

15. **Competitive framing**: DataClaw positions as protest art against Anthropic. Should opentraces.ai explicitly differentiate as "infrastructure for the training community" (neutral/constructive), or is there value in acknowledging the protest framing and building on it?

16. **Timeline urgency**: DataClaw has 2k stars in 1 month. At what point does its schema become a de facto standard that we'd need to be compatible with rather than competing against? Is there a window for schema influence, and if so, how narrow is it?

### traces.com-Specific Strategic Questions (Raised by Competitive Analysis)

20. **traces.com import adapter**: Should we support importing traces from traces.com's download endpoint as a migration path? Their download API returns normalized message data that could be enriched with opentraces.ai schema fields. Low effort, high switching-cost reduction, but could normalize their platform as a valid upstream rather than a competitor.

21. **Message type taxonomy**: traces.com classifies messages into 5 types (`user_message`, `agent_text`, `agent_thinking`, `agent_context`, `tool_call`). Should opentraces.ai adopt this taxonomy as-is (simplicity, interoperability), or build a richer taxonomy for training data consumers? Our `role` + `call_type` + `agent_role` fields can reconstruct their 5 types while adding step-level granularity they lack.

22. **MCP tool naming convention**: traces.com uses `mcp__{server}__{tool}` for MCP tool types (e.g., `mcp__playwright__browser_navigate`). This is a useful convention. Should we adopt it directly in our `tool_calls` schema, or use a different structure that separates server and tool into nested fields?

23. **Server-side AI enrichment**: traces.com generates AI titles, summaries, and 64-dim embeddings server-side. Should opentraces.ai replicate this client-side before upload (adds LLM cost/dependency), as a HF Space post-processor (deferred compute), or rely on HF Hub's built-in search for discovery?

24. **Git notes for trace-commit linking**: traces.com's `refs/notes/traces` pattern with `traces:` prefix is elegant and decentralized. Should opentraces.ai adopt the same git notes mechanism (with `opentraces:` prefix), or is this outside scope for a training-data-focused tool?

### CASS Integration Questions (Raised by Scouting Brief)

17. **CASS license clarification**: CASS's license is listed as "NOASSERTION" on GitHub. Before any integration (FFI, port, or reference), the actual license terms need to be confirmed with the author. This blocks all three v0.2 multi-agent integration options.

18. **`franken_agent_detection` as standalone crate**: Should we approach Jeff Emanuel about publishing the parsing crate independently? If published to crates.io with a permissive license, it would give us 19 agent parsers for free via PyO3 bindings. The alternative is porting patterns manually from CASS + DataClaw reference implementations.

19. **CASS robot API adoption depth**: CASS's `introspect` and `capabilities` commands make the CLI self-documenting for other agents. Should opentraces.ai implement these in v0.1 (adds scope but makes the CLI agent-discoverable from day 1), or defer to v0.2?

---

## Initial Scope (v0.1)

### In Scope

- **Own parsers, vendored security**: opentraces.ai ships its own Claude Code parser (v0.1) that outputs the enriched schema directly, with vendored DataClaw `secrets.py` + `anonymizer.py` (MIT, ~380 lines) for baseline security. No `pip install dataclaw` required. Adapter contract ready for multi-agent expansion.
- **DataClaw JSONL import adapter**: Accept existing DataClaw `conversations.jsonl` exports as an input source, enriching them with opentraces.ai's schema fields where possible. This captures DataClaw's existing community as a migration path without making DataClaw a runtime dependency.
- **Tier 1 (open) and Tier 3 (strict review) only**, defer the Tier 2 classifier pipeline
- **Upload to personal HF dataset repos** (defer community dataset PRs)
- **CLI-based review interface** for Tier 3 (`opentraces review`), defer web UI
- **Python implementation** (matches HF Hub SDK ecosystem)
- **Agent-native CLI output**: All commands emit JSON with `next_steps` and `next_command` by default (DataClaw pattern). Add `--human` flag for pretty-printed output. Ship a Claude Code `SKILL.md` for agent-driven workflows. Adopt CASS's robot-mode conventions where applicable: `opentraces capabilities --json` for feature/version discovery, `opentraces introspect --json` for full API schema (makes the CLI machine-discoverable by other agents), structured errors with `{code, kind, message, hint, retryable}` fields, and a defined exit code vocabulary (0=OK, 2=usage, 3=missing config, 4=network, 5=data corrupt, 7=lock/busy).
- **Git post-commit hook**: `opentraces setup git` installs a post-commit hook that enriches traces with commit metadata (`outcome.committed: true`, `outcome.commit_sha`). Sessions that survive to a commit are automatically flagged as higher-signal.
- **Schema v0.1.0** with all deterministic fields including embedded Agent Trace `attribution` block (see below)
- **CC-BY-4.0** as default license
- **Secret detection**: Vendored DataClaw patterns (19 regex + Shannon entropy + allowlist) extended with credit cards (Luhn), SSNs, phone numbers. Maintained as versioned config.
- **Staged pipeline with gates**: auth -> configure -> review -> publish. Push hard-gated behind review completion.
- **`opentraces` HF tag**: All exports tagged `opentraces` for community discovery. Dependency-based tags (`dependency:stripe`, etc.) for searchable filtering.

### v0.1 Schema: Deterministic Fields (In) vs LLM Enrichment (Deferred)

The research draws a clean line between two kinds of enrichment. v0.1 includes everything that can be deterministically extracted from raw traces:

**In v0.1 (deterministic parsing):**

- `steps` array with `step_index`, `role`, `content`, `timestamp`
- `parent_step` links for sub-agent hierarchy reconstruction
- `agent_role` labels (`main`, `explore`, `plan`) derived from session structure
- `system_prompt_hash` + dedup map
- `tool_calls` with `tool_call_id` and separated `observations` with `source_call_id`
- `tool_definitions` at session level
- `tools_available` per step
- `token_usage` per step (input, output, cache read/write, prefix_reuse)
- `content_hash` (SHA-256) for dedup
- `task.base_commit`, `task.repository` (from git context)
- `environment` block (os, shell, vcs with base_commit, branch, diff)
- `outcome` with `patch` (unified diff), `committed` (bool, whether session changes were committed), `commit_sha` (link to specific commit)
- `dependencies` list (package/library names extracted from manifest files and tool call arguments, enables dependency-based dataset filtering and HF tag generation)
- `call_type` (`main`, `subagent`, `warmup`)
- `snippets` per step (code blocks extracted from tool results/agent responses, with file_path, start/end_line, language, text)
- `attribution` block (Agent Trace-compatible, constructed deterministically from edit operations and `outcome.patch`: which files and line ranges were produced by the session, with `content_hash` for refactor tracking and `conversation.url` linking back to specific steps)
- `metrics` aggregates (total_steps, total_tokens, duration, cache_hit_rate, estimated_cost)

**Deferred to v0.2 (LLM enrichment):**

- `task.description` (auto-generated summary of what the session accomplished)
- `reasoning_content` (requires model-specific extraction, not available from all trace formats)
- `output_summary` on observations (requires summarization)
- Domain tags, task type classification
- `completion_token_ids` and `logprobs` (provider-dependent, not in transcript JSONL)

### v0.1 Pipeline Components

- **Layer 1 (Ingestion)**: Own Claude Code parser reading `~/.claude/projects/*/session.jsonl` directly, outputting enriched schema (steps with token_usage, parent_step hierarchy, agent_role labels, system_prompt extraction, tool_definitions, tools_available per step). Vendored `anonymizer` + `secrets` modules (~380 lines from DataClaw, MIT) for path sanitization and 19-pattern secret redaction.
- **Layer 2 (Enrichment + Security)**: Attribution block construction from edit operations, dependency extraction from manifest files (`package.json`, `Gemfile`, `requirements.txt`, `pyproject.toml`), git commit correlation (`outcome.committed`, `outcome.commit_sha`), content_hash computation, metrics aggregation, quality filter (min 1 tool call, min 2 steps), content dedup.
- **Tier 1 (Open)**: Vendored patterns + extensions (credit cards with Luhn, SSNs, phone numbers). Maintained as versioned config. Include allowlist for false positives.
- **Tier 3: CLI review interface** (`opentraces review` with per-trace approve/redact/reject/annotate)
- **Layer 3 (HF Experience)**: Batched upload via `huggingface_hub`, auto-generated dataset card with attribution summaries and dependency tags, `opentraces` HF tag, dependency-based tags.
- **Agent-native CLI**: JSON output with `next_steps`/`next_command` on every command. Claude Code `SKILL.md`. Git post-commit hook.
- **`RedactingFilter`** on all opentraces.ai log handlers (prevent credential leaks in debug logs)

### v0.1.1: Contributor Dashboard (The Growth Hook)

Ship immediately after v0.1 to activate the growth loop:

- **HuggingFace Space** (Gradio or Streamlit): Enter your HF username, see stats from your `opentraces`-tagged dataset
- **Personal analytics**: Sessions over time, model distribution, token usage breakdown, tool usage breakdown, cost estimates
- **No backend needed**: Reads directly from the user's public HF dataset repo via `datasets` library. Auth = HF login. Infrastructure cost = zero (HF Spaces are free).
- **The pitch**: "Share your agent traces, get insights about how you code with AI."

### v0.2: Community Layer + Tier 2

- **Community comparisons**: Percentile rankings ("you're in the top 20% for cache efficiency"), aggregate benchmarks, model comparison across contributors
- **Shareable profile cards**: "Here's my coding agent stats", designed for Twitter/LinkedIn sharing
- **Tier 2 guarded classifier pipeline** (LLM/embedding-based sensitive content detection + de-anonymisation risk scoring)
- **`claude-trace` adapter** (richer source than DataClaw's transcript parser)
- **Web UI for Tier 3 review** (replace CLI-only review)

### v0.3: Recommendations + Scale

- **Data-driven recommendations**: "Users who switched from Sonnet to Opus on refactoring tasks saw 40% fewer tool calls." This is where the flywheel spins, more data = better recommendations = more reason to contribute.
- **Parquet dual-write** for scale
- **Community dataset PRs** and federated governance
- **`upskill` integration** (`--extract-skills` flag)

### Explicitly Deferred (No Current Timeline)

- Sub-agent full transcript capture as separate trajectory records (v0.1 includes sub-agent steps inline with `parent_step` links, but not the `subagent_trajectory_ref` linked separate files)
- HF Storage Buckets two-layer architecture
- Real-time capture hooks (explicitly killed, see "Passive Capture Only" above)
- Session continuation / re-ingestion from contributed traces
- Team collaboration SaaS features (orgs, roles, invites, namespace management), these serve traces.com's team collaboration mission, not our open data mission. HF Hub organizations cover this for users who need it.
- PR bot comments (GitHub Action consuming HF dataset traces, nice-to-have but not core)
- Follow mode / real-time session streaming (unnecessary with passive capture model)

This gets a working end-to-end flow into users' hands quickly, with the dashboard following fast to activate the growth loop. The key insight from both competitors: DataClaw proved the open pipeline works but failed to convert stars into contributors (0.5% conversion, no selfish incentive). traces.com proved that developers want analytics and shareable profiles, but locks data in a proprietary platform with no training utility. opentraces.ai combines what works from each: DataClaw's security patterns (vendored, not depended on), traces.com's insight that contributors need something back, and the ADP + Agent Trace bridge that neither delivers, connecting agent conversations to code output in a single record. Writing our own Claude Code parser (v0.1) that outputs the enriched schema directly is comparable effort to writing an enrichment layer on top of DataClaw's flat output, and produces better results because there's no lossy intermediate format.
