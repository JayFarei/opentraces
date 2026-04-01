# Standards Alignment

opentraces sits at the intersection of four public standards. It adopts what works from each, and bridges the gap between trajectory (process) and attribution (output).

## ATIF / Harbor (v1.6)

[github.com/laude-institute/harbor](https://github.com/laude-institute/harbor/blob/main/docs/rfcs/0001-trajectory-format.md)

A training trajectory serialization format for agent research. Defines the step-based TAO (Thought-Action-Observation) loop, with fields for token IDs, logprobs, and reward signals designed for RL and SFT pipelines.

**Relationship:** opentraces is a superset of ATIF. We adopt the step-based model, role conventions (`system | user | agent`), and field patterns. We add attribution blocks, per-step token breakdowns, environment metadata, dependency tracking, and security metadata. The downstream field mappings live in `packages/opentraces-schema/FIELD-MAPPINGS.md`; the public export workflow is still experimental.

## ADP (Agent Data Protocol)

[arxiv.org/abs/2410.10762](https://arxiv.org/abs/2410.10762)

An interlingua for normalizing diverse agent trace formats into a common structure for training. Proposes a universal adapter layer so each dataset and each agent only needs one converter, O(D+A), instead of pairwise mappings, O(D*A).

**Relationship:** opentraces' adapter-based normalization follows the same pattern. Per-agent parsers are ADP-style adapters outputting the enriched schema.

## Agent Trace (Cursor/community, v0.1.0 RFC)

[github.com/cursor/agent-trace](https://github.com/cursor/agent-trace)

A code attribution spec (CC BY 4.0) that records which lines of code came from which agent conversation, at file/line granularity. Backed by 10+ sponsors (Cloudflare, Vercel, Google Jules, Cognition).

**Relationship:** opentraces embeds Agent Trace attribution blocks directly in the trace record. Agent Trace focuses on _output_ (code attribution), opentraces bridges that with _process_ (trajectory).

## OTel GenAI Semantic Conventions

[opentelemetry.io/docs/specs/semconv/gen-ai](https://opentelemetry.io/docs/specs/semconv/gen-ai/)

OpenTelemetry's GenAI semantic conventions define standardized span attributes for LLM calls in observability pipelines, covering model names, token counts, and request metadata.

**Relationship:** opentraces' per-step token usage and model fields align with OTel GenAI conventions, enabling cross-referencing between observability spans and training trajectories.

## The Core Insight

Agent Trace preserves _which_ lines came from AI. ATIF/ADP preserve _how_ the agent reasoned. Neither alone tells the complete story. opentraces connects the full conversation trajectory to the specific code output at line granularity.

## Message Taxonomy

opentraces adopts a training-oriented message taxonomy:

| Role | Description |
|------|-------------|
| `system` | System prompt (deduplicated by hash) |
| `user` | User message / prompt |
| `agent` | Agent response, tool calls, or thinking |

Agent steps are further classified by `call_type` (`main`, `subagent`, `warmup`) and `agent_role` (`main`, `explore`, `plan`).
