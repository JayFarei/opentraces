# Schema Rationale: opentraces-schema v0.1.0

Why this schema is what it is. Each section connects a design decision to the
standards, empirical observations, and constraints that motivated it.

Standards referenced:
- [Agent Trace spec](https://github.com/nichochar/agent-trace) (Cursor RFC, CC BY 4.0)
- [ATIF v1.6](https://github.com/harbor-ai/agent-trajectory-interchange-format) (Agent Trajectory Interchange Format)
- [ADP](https://arxiv.org/abs/2410.10762) (Agent Data Protocol)
- [OTel GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)


## TraceRecord (Top-Level)

### Why an independent schema, not ATIF-native

Three existing standards each serve a different purpose:
- **ATIF** optimizes for training pipelines (token IDs, logprobs)
- **Agent Trace** captures attribution only (who wrote which lines)
- **OTel** captures observability only (latency, error rates)

No single standard covers trajectory + attribution + security + environment.
This schema bridges all three as a superset, with `opentraces export --format atif`
planned for standards-compatible output.

### Why content_hash (SHA-256)

With sharded JSONL upload (one file per push, never append to existing shards),
dedup must happen at record level. Existing community trace datasets on HuggingFace
use content hashing for deduplication at upload time.

SHA-256 chosen over cheaper hashes because traces are large enough that hash
computation time is negligible vs I/O. The `compute_content_hash()` method excludes
`content_hash` and `trace_id` so re-parsing identical source data produces the same
hash regardless of the random UUID assigned.

### Why session_id + trace_id (two IDs)

- `session_id`: the agent's native session identifier, stable across re-parsing
- `trace_id`: random UUID for database-level uniqueness

Agent sessions group many API calls under a single session ID, but a given session
may be re-ingested multiple times (e.g. re-exported after a schema upgrade). Separate
IDs allow re-ingestion without collision while preserving join keys to the original
agent session.


## Steps: TAO-Loop Oriented

### Why `steps` not `turns`

Each Step represents one LLM API call (request + response), not a conversational turn.
Multi-agent coding sessions routinely involve 50-100+ API calls spanning multiple
parallel sub-agents within a single user-visible "conversation." Conversational turns
would collapse this hierarchy into a flat sequence, losing the architectural signal
needed for caching analysis and training data segmentation.

Both ATIF and ADP use step-based models. The TAO (Thought-Action-Observation) loop
has converged as the canonical trajectory primitive across agent frameworks
(OpenHands, SWE-Agent, AgentLab) and community datasets.

### Why `role: "agent"` not `"assistant"`

ATIF and the broader agent community convention use `system | user | agent`. "Agent"
is semantically accurate for autonomous coding agents that reason, act, and observe
in loops, as opposed to chat assistants that respond to single prompts.

### Why parent_step + agent_role + subagent_trajectory_ref

Multi-agent coding systems exhibit a hierarchical phase structure:

1. **Warm-up**: cache priming calls with no reasoning
2. **Main agent**: full system prompt, full tool set
3. **Explore**: parallel sub-agents with fresh context, reduced tool sets, role-specific prompts
4. **Plan**: receives only summarized explore findings, not raw context
5. **Execute**: main agent follows plan as checklist

Sub-agents receive fresh context (not the parent's) and a subset of tools. This
hierarchical context isolation creates stable prefixes within each sub-agent loop,
which is why multi-agent architectures achieve high prefix cache reuse rates.

Three fields capture this hierarchy:
- `parent_step`: tree edge (which main-agent step spawned this sub-agent)
- `agent_role`: phase label (main, explore, plan) for filtering without reading system prompts
- `subagent_trajectory_ref`: links to a separate TraceRecord when sub-agent trajectories are stored independently

ATIF provides the `subagent_trajectory_ref` pattern for multi-agent delegation.

### Why call_type: main | subagent | warmup

Multi-agent systems include warm-up calls that exist purely to seed the KV cache.
They contain no reasoning and produce empty or minimal output, but are architecturally
significant: they explain why later calls achieve high prefix reuse.

For training data, warm-up calls should be filterable (they add noise to SFT datasets).
For caching analysis, they are essential. `call_type` enables both use cases.

### Why system_prompt_hash + top-level system_prompts dict

In multi-agent sessions, system prompts can be 20K+ tokens and repeat identically
across every call within a sub-agent phase. Storing inline would multiply storage
dramatically for long sessions.

Hash-keyed deduplication: store each unique system prompt once in a top-level dict,
reference by hash in each step. This separates the queryable step metadata from
the bulk content.

### Why reasoning_content as explicit field

Extended thinking and chain-of-thought content is returned in a separate field by
LLM APIs that support it. Benchmark evaluations suggest that including hidden
reasoning in training data improves downstream task performance.

A dedicated `reasoning_content` field preserves the API-level separation for training
pipelines that may want to include or exclude chain-of-thought independently of the
main content.

### Why output_summary on Observation

In multi-agent architectures, downstream sub-agents (e.g. plan phase) receive only
summarized findings from upstream sub-agents (e.g. explore phase), not raw results.
This is an information bottleneck pattern.

`output_summary` serves the same purpose for trace consumers: scan summaries to assess
relevance without loading full tool outputs (which can be megabytes for file reads or
grep results over large codebases).


## ToolCall and Observation: Separated

### Why tool calls and observations are separate lists

Training pipelines (SFT, RLHF) depend on clean tool_call / tool_result separation
for learning tool selection and result interpretation as distinct capabilities.
ATIF and ADP both maintain this separation. Observations link back via `source_call_id`
for 1:1 matching. Dangling tool calls (agent requested a tool but no result was
recorded) are marked with `error: "no_result"` rather than dropped.


## TokenUsage: Cache-Aware

### Why prefix_reuse_tokens, cache_read_tokens, cache_write_tokens

Prefix reuse is the dominant cost driver for multi-agent architectures. Empirical
measurements show that well-structured hierarchical agents achieve 90%+ prefix reuse
(representing 80%+ cost savings), while agents with dynamic mid-prompt mutations
achieve under 50%, and template-based agents under 5%.

| Architecture pattern | Typical prefix reuse | Implication |
|---------------------|---------------------|-------------|
| Hierarchical multi-agent (stable prefixes) | 90%+ | Well-structured, cache-friendly |
| Dynamic memory mutation | ~40-50% | Memory updates break prefix alignment |
| Template-based prompting | <5% | Variable insertion near prompt start destroys caching |

Per-step cache breakdown enables phase-level cost analysis and cross-architecture
comparisons.


## Outcome: RL-Ready Signals

### Why success + signal_source + signal_confidence

The training community needs trajectory-level reward signals for RLHF, DPO, and RLVR.
Most existing community trace datasets lack outcome fields entirely, making them
unsuitable for reward modeling without manual annotation.

Three confidence tiers communicate trustworthiness:
- **derived**: deterministic extraction (e.g. `committed` from git state)
- **inferred**: heuristic-based (e.g. success from test output patterns)
- **annotated**: human or CI label

This lets training pipelines filter by confidence: use only `derived` signals for
high-confidence reward, include `inferred` for larger but noisier datasets.

### Why committed + commit_sha

The cheapest deterministic quality signal available: did the agent's changes get
committed to git? Derivable from git state with zero annotation cost, zero LLM
enrichment. Aligns with the project principle: zero required annotation, all
enrichment in v0.1.0 is deterministic.


## Attribution: Embedded Agent Trace Block

### Why embed attribution rather than link externally

The Agent Trace spec stores attribution in a separate file from trajectories.
This schema bridges both: trajectory (process) AND attribution (output) in a single
record. Embedding keeps traces self-contained for dataset consumption, where each
JSONL line should be independently useful without external file lookups.

Marked `experimental: true` because attribution confidence varies by session
complexity: single-file edits produce high-confidence attribution, multi-file
refactors with interleaved tool calls produce lower confidence.

### Why murmur3 for AttributionRange.content_hash

The Agent Trace spec uses `algorithm:value` format (e.g. `murmur3:9f2e8a1b`) for
position-independent content tracking. murmur3 is fast and non-cryptographic,
sufficient for detecting code movement across refactors and file renames.

SHA-256 is used at the trace level (collision resistance needed for dedup integrity),
murmur3 at the line-range level (speed needed, no security requirement).


## SecurityMetadata: 3-Tier

### Why a 3-tier security model

- **Tier 1** (open): full content, suitable for public repos and open-source projects
- **Tier 2** (guarded): redacted secrets, anonymized paths/usernames
- **Tier 3** (strict): structural metadata only, no content

Existing trace-sharing tools typically offer a single redaction mode: everything is
processed the same way. A tier system enables per-project configuration with
per-session override, so a user working on both open-source and proprietary code
can publish traces from both with appropriate protection levels.


## Environment and VCS

### Why capture OS, shell, language_ecosystem

Reproducibility: the same task on macOS vs Linux may produce different agent behavior
(different shell commands, different tool availability, different file paths).
Filtering: researchers can select traces by ecosystem (Python, TypeScript, Rust)
to build domain-specific training datasets without parsing file extensions from
tool call arguments.


## Deliberate Exclusions

### No token IDs or logprobs

ATIF v1.6 includes `prompt_token_ids`, `completion_token_ids`, and `logprobs` per
step, enabling RL without retokenization drift. These fields are not available from
agent CLI tools (Claude Code, Cursor, Codex CLI, Gemini CLI) because they intercept
at the application layer, not the inference layer.

Planned for inclusion when agent APIs expose them. Until then, ATIF export
(`opentraces export --format atif`) will bridge this gap for training pipelines
that need token-level data from other sources.

### No OTel span IDs

OTel provides distributed tracing metadata (trace_id, span_id, parent_span_id)
designed for production observability across microservices. Our `trace_id` is
session-scoped, not request-scoped. OTel interop is planned via export, not
native embedding, because the primary consumers of this schema are training
pipelines and researchers, not production monitoring dashboards.

### No AGENTS.md content

AGENTS.md is a project-level instruction file for agents, not trace data. While
it provides context for understanding agent behavior, embedding it in every trace
record would be wasteful. It may be referenced via the `metadata` dict in future
versions if there is demand.

### No LLM enrichment fields

Fields like `task_description`, `domain_tags`, and `task_type` are not in v0.1.0
because they require LLM inference to generate. The project principle is zero
required annotation: all enrichment in v0.1.0 is deterministic. LLM-enriched
metadata may be added as optional fields in a future minor version.
