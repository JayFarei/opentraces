# Hugging Face Community Agent Traces Ecosystem: R&D Scouting Brief

> Research date: 2026-03-27
> Sources: kobe0938/blog (GitHub), huggingface/skills, huggingface/upskill, nlile/misc-merged-claude-code-traces-v1
> Category: ecosystem analysis (blog series + tooling + datasets + platform infrastructure)

---

## Overview

Kobe Chen (kobe0938), a researcher affiliated with Hugging Face, has published a series of blog posts on the HF Community Blog analyzing KV cache reuse patterns in agentic LLM systems. The centerpiece is a reverse-engineered trace of Claude Code performing a real SWE-bench task, captured as a 92-record JSONL file. This work sits at the intersection of a broader HF ecosystem that includes agent skill generation (upskill), community trace datasets (32K+ records), storage infrastructure suitable for traces (Storage Buckets), and a standardized skill format (huggingface/skills). Together, these form the foundation for a productizable platform around agent trace capture, analysis, and skill distillation.

## Problem Being Addressed

Current agentic coding systems (Claude Code, Codex, Gemini CLI) are black boxes to their users. Developers pay for millions of tokens per task but have no visibility into:
1. **What the agent actually does**, how many LLM calls, what subagents are spawned, what tools are used
2. **How efficiently tokens are spent**, prefix cache reuse rates, redundant file reads, wasted context
3. **What patterns can be extracted**, reusable skills, common workflows, failure modes
4. **How to optimize cost and latency**, which caching strategies work for which architectures

The blog series systematically addresses all four by providing the first public trace-level analysis of Claude Code's multi-agent architecture.

---

## Kobe Chen's Research: Detailed Analysis

### Blog Post 1: Context Engineering & Reuse Pattern Under the Hood of Claude Code

**Published:** December 22, 2025 on HF Community Blog
**Method:** Intercepted Claude Code's LLM traffic locally (possible because client is open-source), captured all 92 API calls into `claude_code_trace.jsonl`

**Experiment Setup:**
- Task #80 from SWE-bench_Verified (Django JSONField admin readonly display fix)
- Django repo at commit `2e0f04507b17362239ba49830d26fec504d46978`
- 92 LLM calls, ~2M input tokens, 13 minutes

**Key Architectural Findings:**

1. **Warm-Up Phase (Calls #1-#5):** Before any reasoning, Claude Code runs 5 warm-up calls to prime the KV cache:
   - #1: Summarization agent (chat title generation)
   - #2: Tool list warm-up (caching the tool definitions)
   - #3: Explore subagent warm-up
   - #4: Plan subagent warm-up
   - #5: New topic agent

2. **Main Agent (#6):** 20,000+ token system prompt including git history, git status, full tool list. Has access to 18 tools including subagent-invocation tools.

3. **Explore Phase (#7-#45):** Three Explore subagents spawned IN PARALLEL, each with:
   - Fresh context (main agent context NOT carried over)
   - Subset of 10/18 tools
   - Own ReAct loop
   - Can invoke 1-3 tools in parallel
   - System prompt: "You are Claude Code... You are a file search specialist"

4. **Plan Phase (#46-#76):** Plan subagent receives ONLY summarized Explore findings (not full context). System prompt: "You are Claude Code... You are a software architect and planning specialist." Context grows from 11,552 to 38,819 tokens over calls #47-#72.

5. **Execution Phase (#77-#92):** Main agent follows the plan as a todo list, crossing out completed items. ReAct pattern with tool calls.

**Prefix Reuse Analysis:**

| Phase | Trace IDs | Total Tokens | Shared Prefix % |
|-------|-----------|-------------|-----------------|
| Warm-up & initial | #1-#6 | 47,177 | 0.22% |
| Explore subagents | #7-#45 | 546,104 | 92.06% |
| Plan subagent | #47-#72 | 528,286 | 93.23% |
| Main execution | #73-#92 | 827,411 | 97.83% |
| **Overall** | **#1-#92** | **~2M** | **92%** |

**Cost Impact:** Without caching: $6.00. With 92% prefix reuse: $1.152 (81% savings / $4.85 saved).

**Beyond Prefix Caching:** Identifies two substring-caching opportunities:
- Tool list reuse (subagent tool lists are subsets of main agent's)
- Repeated file reads across subagents

### What the Multi-Agent Architecture Teaches Us

The trace reveals several architectural patterns worth internalizing for our product design:

**Hierarchical context isolation.** The main agent spawns subagents that get fresh context, not the main agent's context. Explore subagents get 10/18 tools and a role-specific system prompt ("file search specialist"). Plan subagent receives only *summarized* Explore findings, not raw exploration. This is a deliberate information bottleneck, and it's what makes prefix caching work so well (stable prefixes within each subagent's loop).

**Parallel fan-out, serial fan-in.** Three Explore subagents run simultaneously with different search goals (#7-#45), then results are summarized and fed serially to Plan (#46-#76), then Plan output drives serial Execution (#77-#92). The architecture resembles map-reduce.

**Warm-up calls are cache priming, not work.** Calls #1-#5 exist purely to seed the KV cache so later calls hit it. The system was designed with prefix caching as a first-class architectural constraint, not an afterthought.

**ReAct loops within each subagent.** Each subagent independently reasons and acts in a loop with its own tool subset. The main agent doesn't micromanage, it delegates a goal and gets back a summary.

**Plan-as-checklist execution.** The plan is written to a markdown file and items are crossed off during execution. Simple, debuggable, auditable.

**The practical lesson:** Claude Code's architecture is optimized for *caching economics first*, agent capability second. The hierarchy exists partly because it creates stable prefixes. This means our trace format needs to capture the parent-child relationships between calls to reconstruct this hierarchy.

### Blog Post 2: MemGPT Caching Analysis

Analyzes why MemGPT's dynamic memory architecture breaks prefix caching:
- Prefix cache hit rate: ~43.9%
- Substring/block cache hit rate: ~93.4%
- Root cause: Working Context mutations mid-prompt destroy prefix alignment

### Blog Post 3: RepoAgent Non-Prefix Caching

Analyzes RepoAgent (LLM-powered code documentation generator):
- Prefix cache hit rate: ~3.4%
- Non-prefix cache hit rate: >85.9%
- Root cause: Template variables in first 200 chars break prefix alignment

### Blog Post 4: Skill File Caching Strategy

Proposes caching architecture for Claude Code skills using CacheBlend:
- Level 1: Metadata (~100 tokens, always loaded)
- Level 2: SKILL.md (under 5K tokens, loaded when triggered)
- Level 3+: Resources (loaded as needed)
- Cache hit rates: 63-85% with CacheBlend position-invariant concatenation

### Common Thread

The research arc builds a clear thesis: **prefix caching works well for static-prefix agents (Claude Code: 92%), but fails for agents with dynamic mid-prompt content (MemGPT, RepoAgent)**. The solution is non-prefix/substring caching via CacheBlend (arxiv: 2405.16444). The LMCache project (github.com/LMCache/lmcache-agent-trace) provides empirical benchmarks cited across all posts.

---

## The Trace Format (claude_code_trace.jsonl)

### Schema

Each line is a flat JSON object with exactly 4 fields:

```json
{
  "timestamp": 1764900079870,
  "input": "<full LLM prompt string>",
  "output": "<LLM response string>",
  "session_id": "e6b6d1a9-fe8a-4cae-8dff-ad24a59d4337"
}
```

### Field Details

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | integer | Unix milliseconds |
| `input` | string | Full concatenated prompt: System + Tools JSON array + User message + conversation history |
| `output` | string | Raw model response (may include tool call JSON, may be empty) |
| `session_id` | string | UUID identifying the conversation session, or `"0"` for warm-up |

### Input Field Internal Structure

```
System:\n[system prompt]\n\nTools:\n[JSON array of tool definitions]\n\nUser:\n[message + conversation history]
```

### Session IDs Observed

- `e6b6d1a9-...`, 3 records (summarizer, new-topic, warm-up agents)
- `4bb743ff-...`, 88 records (all Explore, Plan, main agent calls)
- `"0"`, 1 record (tool-list warm-up, empty output)

### Embedded Metadata

System prompts contain:
- `gitStatus` block: branch, status, recent commits
- `env` block: working directory, platform, OS version, date
- Tool definitions with JSON Schema input_schema

### What Is NOT In The Trace

- No token counts per call
- No cost/billing data
- No parsed tool call structures (raw strings only)
- No model identifier per call
- No latency/TTFT measurements
- No cache hit/miss indicators

---

## Broader Ecosystem: Related Tools & Datasets

### 1. claude-trace (npm: @mariozechner/claude-trace)

The primary community tool for intercepting Claude Code traffic:
- Injects into Claude Code's fetch() calls
- Logs request/response pairs to `.claude-trace/` as JSONL + standalone HTML
- Filters to `/v1/messages` with >2 messages
- Self-contained HTML viewer for browsing conversations
- Source: Simon Willison covered this at simonwillison.net/2025/Jun/2/claude-trace/

### 2. claude-code-reverse (GitHub: Yuyz0112/claude-code-reverse)

Reverse engineering effort that maps Claude Code's static code using LLMs. Provides architectural breakdowns of multi-agent orchestration, model selection, and context management.

### 3. nlile/misc-merged-claude-code-traces-v1 (HF Dataset)

**32,133 deduplicated Claude API conversation traces** merged from 10 source datasets.

Schema (16 fields):

| Field | Type | Notable |
|-------|------|---------|
| `id` | string | UUID |
| `source_table` | string | Source dataset identifier |
| `source_repo` | string | Original HF repo |
| `messages_json` | string | OpenAI-style role/content array with tool_use blocks |
| `system_prompt` | string | System prompt |
| `model` | string | e.g., `claude-3-5-haiku-20241022`, `claude-sonnet-4-5-20250929` |
| `tools_json` | string | JSON array of tool definitions |
| `gitdiff` | string | Git diff (nullable) |
| `content_hash` | string | MD5 for deduplication |

This is significantly richer than kobe0938's 4-field JSONL format.

### 4. huggingface/upskill

Tool for generating and evaluating agent skills from traces:
- `upskill generate "pattern name" --from ./trace.md`, extracts skills from traces
- Teacher-student model workflow: generate with expensive model, evaluate with cheap model
- Benchmark mode: multi-model comparison with statistical runs
- Skills stored as `./skills/{name}/SKILL.md` directories
- Can run evaluations on HF Jobs (remote execution)

**Critical insight for productification:** upskill already implements the trace-to-skill pipeline. The gap is in trace capture standardization and a managed platform.

### 5. huggingface/skills

11 official HF skills for coding agents (Claude Code, Codex, Gemini CLI, Cursor):
- Standardized SKILL.md format with YAML frontmatter
- Plugin marketplace integration for Claude Code
- Skills cover: Hub operations, dataset queries, model training, Gradio UIs, paper publishing, metrics logging

### 6. HF Storage Buckets (March 2026)

New Hub repository type for mutable, non-versioned object storage. **Important nuance:** Storage Buckets are designed for mutable ML artifacts *generally*, not specifically for agent traces. However, agent traces are explicitly listed as one of the target use cases.

From HF's tweet (March 10, 2026):
> "Git falls short for everything on high-throughput side of AI (checkpoints, processed data, agent traces, logs etc)"

From the blog post:
> "Training pipelines constantly produce families of related artifacts, raw and processed data, successive checkpoints, Agent traces and derived summaries, and Xet is designed to take advantage of that overlap."

Key features:
- Powered by Xet (chunk-based deduplication, related artifacts share storage)
- S3-like API: `hf buckets sync`
- Pricing: $12/TB/month (public), $8/TB/month at 500TB+
- Direct transfers from Buckets to versioned repos planned
- Python SDK (`huggingface_hub` v1.5.0+), CLI, JavaScript, `fsspec` support

Buckets would work well for trace storage (mutable, high-throughput, no git versioning needed), but they are a general-purpose storage primitive, not a trace-specific product.

---

## Design Philosophy: What Kobe Chen's Work Reveals

### 1. Traces as First-Class Research Artifacts

The blog series treats agent traces not as debug logs but as the primary unit of analysis for understanding agentic system behavior. The JSONL format captures the complete prompt/response pair, enabling:
- Architecture reconstruction (subagent spawning, tool selection)
- Cost analysis (token counting, prefix reuse calculation)
- Caching optimization research (prefix vs. substring)

### 2. Observability Over Opacity

The explicit motivation: "Unlike cloud-only agents whose internals remain hidden (e.g., Perplexity, Devin, Manus), Claude Code runs partially locally with an open-sourced client repo", giving researchers the ability to "inject traffic, reverse-engineer, and observe every single LLM call."

### 3. Prefix Reuse as Architecture Signal

The key insight is that prefix reuse rate is not just a cost metric but an architectural fingerprint:
- High prefix reuse (92%, Claude Code) = well-structured multi-agent system with stable context
- Low prefix reuse (3.4%, RepoAgent) = dynamic template-based prompting that defeats caching
- Medium prefix reuse (43.9%, MemGPT) = memory mutation architecture

### 4. Practical Optimization Focus

Every blog post ends with concrete cost calculations and optimization recommendations, not abstract theory. The audience is practitioners who pay for tokens.

### 5. Open Data Ethos

The raw trace file is published alongside analysis. The visualization tool is public. The research builds on open datasets (SWE-bench) and references open-source tools (LMCache, vLLM, SGLang).

---

## HF's Strategic Interest: The Data Flywheel

There is a clear strategic reason for HF to encourage community trace publishing. The 32K+ traces already on Hub contain:
- Complete system prompts from Claude Code, Codex, Gemini CLI
- Full tool definitions with JSON schemas
- Multi-turn reasoning chains showing how agents decompose problems
- Real-world code generation patterns across thousands of tasks
- Tool selection decisions (when to use Bash vs Read vs Grep)

This is essentially a distillation of Anthropic's, OpenAI's, and Google's agent design decisions, captured in the wild from real usage. For an organization building open-source coding agents, this is the most valuable dataset they could collect.

The upskill tool makes the pipeline explicit: teacher model (expensive, Claude/GPT) generates traces -> upskill extracts skills -> student model (cheap, open-source) learns from skills -> HF's open-source ecosystem becomes more competitive.

Making trace publishing easy and free (via Hub/Buckets) is a data flywheel strategy: more traces -> better skills -> better open-source agents -> more users -> more traces.

---

## Competitive Landscape

| Tool/Platform | What It Does | Differentiator | Trade-off |
|--------------|-------------|----------------|-----------|
| **claude-trace** (npm) | Intercepts Claude Code API calls to JSONL | Easy to use, HTML viewer included | Capture only, no analysis or skill extraction |
| **claude-code-reverse** | Static reverse engineering of Claude Code | Architecture mapping | Snapshot analysis, not runtime traces |
| **LangSmith** | Full observability platform for LangChain agents | Rich UI, production monitoring | Tied to LangChain ecosystem |
| **Braintrust** | LLM eval and observability | Eval-focused, multi-provider | Less agent-trace-specific |
| **Weights & Biases Weave** | LLM application tracing | Production monitoring, integrations | General-purpose, not agent-architecture-focused |
| **LMCache** | KV cache optimization for LLM serving | Substring caching, empirical benchmarks | Infrastructure-level, not user-facing |
| **HF upskill** | Trace-to-skill generation | Teacher-student workflow, eval built in | CLI tool, not a platform |

### White Space for Productification

None of the above provides an integrated platform that:
1. **Captures** agent traces in a standardized format
2. **Stores** them on managed infrastructure (HF Hub/Buckets)
3. **Analyzes** them for architecture, cost, caching, and patterns
4. **Extracts** reusable skills (upskill)
5. **Shares** them as community datasets (HF Hub)
6. **Benchmarks** skill effectiveness across models

This is exactly the gap kobe0938's research illuminates and HF's tooling partially fills.

---

## Product Direction: A Trace Publishing Tool with Queryable Output

The goal is a trace publishing tool where the challenge is making published traces directly digestible and usable for analysis, skill formation, and training jobs, including being queryable by agents compiling domain-specific datasets.

### The Core Problem with Existing Formats

Raw traces (Kobe's 4-field, nlile's 16-field, claude-trace's HTTP pairs) are archives, not queryable corpora. An agent trying to find "all traces where Claude Code debugged a Django migration" would have to download and parse every record.

### Two-Layer Trace Architecture

The solution is to separate the **queryable envelope** from the **structured content**.

#### Layer 1: Queryable Metadata (the envelope)

Small, structured, instantly filterable. An agent reads this *before* deciding to download the full trace. Published as Parquet columns in an HF Dataset, queryable via Dataset Viewer API.

```yaml
# Trace-level metadata (one per session)
trace_id: "uuid"
agent: "claude-code"              # which agent produced this
agent_version: "1.0.32"
model: "claude-sonnet-4-5"
task_description: "Fix JSONField readonly display in Django admin"
task_source: "swe-bench-verified/80"   # optional, links to benchmark
domain_tags: ["django", "admin", "json", "python"]
task_type: "bug-fix"              # bug-fix | feature | refactor | test | docs | exploration
outcome: "success"                # success | failure | partial | unknown
duration_seconds: 780
total_calls: 92
total_input_tokens: 1_948_978
total_output_tokens: 45_230
prefix_reuse_rate: 0.92
tools_used: ["Bash", "Read", "Grep", "Edit", "Write", "Glob"]
architecture: "multi-agent"       # single-agent | multi-agent
subagents_spawned: ["explore", "explore", "explore", "plan"]
files_modified: ["django/contrib/admin/utils.py", "tests/admin_utils/test_logentry.py"]
repo: "django/django"
language: "python"
frameworks: ["django"]
```

#### Layer 2: Structured Trace (the content)

The full trace, parsed into a structure that analysis, upskill, and training pipelines can consume without writing their own parser for raw prompt strings.

```json
{
  "trace_id": "uuid",
  "metadata": { "...layer 1 fields..." },
  "calls": [
    {
      "call_index": 7,
      "timestamp": 1764900095000,
      "agent_role": "explore",
      "agent_label": "Explore JSONField implementation",
      "parent_call": 6,
      "system_prompt_hash": "abc123",
      "tools_available": ["Bash", "Read", "Grep", "Glob"],
      "messages": [
        {"role": "user", "content": "Find the JSONField implementation..."},
        {"role": "assistant", "content": "...", "tool_calls": [
          {"tool": "Grep", "input": {"pattern": "class JSONField", "path": "."}, "output_summary": "Found in django/db/models/fields/json.py"}
        ]},
        {"role": "user", "content": "[tool_result]..."}
      ],
      "input_tokens": 11552,
      "output_tokens": 834,
      "prefix_reuse_tokens": 10200
    }
  ],
  "system_prompts": {
    "abc123": "You are Claude Code... file search specialist...",
    "def456": "You are Claude Code... software architect..."
  }
}
```

#### Key Design Decisions

- **`system_prompts` are deduplicated** into a lookup table by hash. They repeat across every call but only need to be stored once. Critical for training jobs (don't want 20K system prompt repeated 92 times).
- **`tool_calls` are parsed out**, not buried in raw strings. An agent can filter for "traces that used Edit on Python files" without parsing prompt text.
- **`parent_call` links** reconstruct the agent hierarchy. You can see that call #7 was spawned by call #6 (the main agent).
- **`agent_role` and `agent_label`** let you filter by agent type without reading system prompts.
- **`output_summary`** on tool results gives a lightweight preview so agents can assess relevance without downloading full file contents.

### How This Serves Each Use Case

**Analysis (Kobe's prefix reuse research):**
- `input_tokens` + `prefix_reuse_tokens` per call = prefix reuse calculation without reparsing
- `agent_role` grouping = phase-level analysis (explore vs plan vs execute)
- `system_prompt_hash` = identify which prompts share prefixes

**Skill extraction (upskill):**
- `tool_calls` parsed out = upskill can pattern-match tool sequences directly
- `domain_tags` + `task_type` = scope skill to specific domains
- `outcome: success` = only learn from traces that worked

**Training jobs:**
- `messages` array = already in chat-turn format, ready for SFT
- Deduplicated system prompts = no wasted tokens in training data
- `agent_role` = can train specialized models (explore-only, plan-only)

**Agent compiling a domain dataset:**
```
1. Query metadata: domain_tags contains "django", task_type = "bug-fix", outcome = "success"
2. Get back 847 trace summaries with task_description
3. Filter further: files_modified contains "migrations/"
4. Download 23 full structured traces
5. Extract tool_calls where tool = "Bash" and input contains "migrate"
```

### The Enrichment Pipeline

The product value lives in the enrichment step that transforms raw JSONL into the two-layer format:

```
[Raw JSONL from claude-trace]
        |
  [Parse Engine]  -- splits raw input strings into system_prompt, tools, messages
        |             parses tool_calls from assistant responses
        |             links parent-child call relationships via session_id
        |
  [LLM Enrichment Pass]  -- generates task_description, domain_tags, task_type
        |                    classifies outcome (success/failure/partial)
        |                    writes output_summary for tool results
        |
  [Token Counter]  -- counts input/output tokens per call
        |              calculates prefix_reuse_tokens
        |
  [Publisher]  -- writes Layer 1 as Parquet columns to HF Dataset
               -- writes Layer 2 as JSON to HF Dataset (or linked Bucket)
               -- deduplicates system_prompts by hash
```

---

## Key Building Blocks Available

1. **Trace Capture:** claude-trace (npm) or custom interceptor (kobe0938's approach)
2. **Trace Storage:** HF Hub (Datasets for queryable Parquet) + Buckets (for large trace content)
3. **Trace Schema:** nlile's 16-field merged schema as starting point, extended with our two-layer design
4. **Analysis Engine:** Kobe Chen's prefix-reuse methodology
5. **Skill Extraction:** upskill CLI (`--from trace.md`)
6. **Skill Distribution:** huggingface/skills marketplace
7. **Visualization:** v0-llm-agent-dashboard.vercel.app (existing)

## Gaps to Fill

1. **Enrichment pipeline**: The Parse Engine + LLM Enrichment Pass + Token Counter that transforms raw traces into the two-layer format. This is the core product.
2. **Multi-agent support**: Claude Code is the most-studied, but Codex, Gemini CLI, Cursor also need trace capture and parsing.
3. **Privacy/security layer**: Traces contain full prompts, code, git history. Need redaction/anonymization options.
4. **Community features**: Trace sharing, domain leaderboards, contribution tracking.

## Open Questions

1. What is the privacy/legal position on storing full agent traces (which contain user code and prompts)?
2. Should trace capture be opt-in (user installs interceptor) or built into agents?
3. How does CacheBlend/substring caching research factor into the product roadmap?
4. What model providers beyond Anthropic should be supported at launch?
5. What is the right granularity for `output_summary` on tool results (too short = useless, too long = defeats the purpose)?
6. Should the LLM enrichment pass be mandatory or optional (some users may want to publish raw traces fast)?

---

## Key Takeaways

1. **Kobe Chen's research provides the analytical blueprint for an agent trace platform**, demonstrating that trace-level analysis reveals architecture, cost optimization opportunities (81% savings), and caching strategies that are invisible from the outside. The 4-blog series progresses from "here's what Claude Code does" to "here's how to optimize any agentic system's caching," building toward a general framework.

2. **HF has most of the infrastructure pieces but they are disconnected**: Hub Datasets for queryable storage, Buckets for mutable artifact storage, upskill for trace-to-skill extraction, skills format for distribution, and 32K+ community traces as seed data. The missing piece is the enrichment pipeline that transforms raw captures into queryable, structured, multi-use trace datasets.

3. **The real product is the enrichment pipeline**, not trace capture (commodity) or storage (HF already provides this). The value is in parsing raw prompt strings into structured calls with tool_calls, parent-child links, and agent roles, then enriching with domain tags, task classification, and outcome labels so that downstream agents, upskill, and training jobs can consume traces without building their own parsers.

4. **HF's strategic interest is a data flywheel**: more structured traces -> better skills via upskill -> better open-source agents -> more users -> more traces. Making trace publishing easy and the output immediately useful accelerates this cycle.

---

## Sources

- [Context Engineering & Reuse Pattern Under the Hood of Claude Code](https://huggingface.co/blog/kobe0938/context-engineering-reuse-pattern-claude-code), Kobe Chen, Dec 2025
- [kobe0938/blog GitHub repository](https://github.com/kobe0938/blog/tree/master/claude-code), trace file + all blog posts
- [Example trace file](https://github.com/kobe0938/blog/blob/master/claude-code/claude_code_trace.jsonl), 92-record JSONL
- [nlile/misc-merged-claude-code-traces-v1](https://huggingface.co/datasets/nlile/misc-merged-claude-code-traces-v1), 32K merged traces
- [huggingface/upskill](https://github.com/huggingface/upskill), skill generation from traces
- [huggingface/skills](https://github.com/huggingface/skills), standardized skill format
- [Introducing Storage Buckets on the Hugging Face Hub](https://huggingface.co/blog/storage-buckets), March 2026
- [HF on X: Storage Buckets announcement](https://x.com/huggingface/status/2031428153948709291), March 10, 2026
- [claude-trace npm package](https://www.npmjs.com/package/@mariozechner/claude-trace), trace interceptor
- [claude-code-reverse](https://github.com/Yuyz0112/claude-code-reverse), architecture reverse engineering
- [LLM Agent Trace Viewer](https://v0-llm-agent-dashboard.vercel.app/), visualization dashboard
- [CacheBlend](https://arxiv.org/abs/2405.16444), non-prefix KV cache reuse
- [LMCache agent trace benchmarks](https://github.com/LMCache/lmcache-agent-trace)
- [SWE-bench_Verified dataset](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)
- [Martin Fowler on Context Engineering](https://martinfowler.com/articles/exploring-gen-ai/context-engineering-coding-agents.html)
