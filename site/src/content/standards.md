# Standards Alignment

open traces positions itself at the intersection of three existing standards:

## ATIF / Harbor (v1.6)

A training trajectory serialization format. Defines the step-based TAO (Thought-Action-Observation) loop structure. ATIF is the closest thing to a standard for what open traces' schema is trying to be.

**Relationship:** open traces' schema is a superset of ATIF. We adopt ATIF's step-based model, role conventions (`system | user | agent`), and field patterns. We add: attribution blocks, per-step token breakdowns, environment metadata, dependency tracking, and security metadata. Export to ATIF via `opentraces export --format atif`.

## ADP (Agent Data Protocol)

An interlingua for normalizing diverse agent trace formats into a common structure for training. Core argument: O(D+A) adapters instead of O(D*A). ADP has been used to normalize 1.3M+ trajectories.

**Relationship:** open traces' adapter-based normalization layer follows the same pattern. Our per-agent parsers are ADP-style adapters that output our enriched schema.

## Agent Trace spec (Cursor/community, v0.1.0 RFC)

Solves the adjacent problem of attributing which lines of code came from which agent conversation, at file/line granularity. Backed by 10+ corporate sponsors (Cloudflare, Vercel, Google Jules, Cognition).

**Relationship:** open traces embeds Agent Trace attribution blocks directly in the trace record. Agent Trace focuses on the _output_ (code attribution), while ATIF/ADP focus on the _process_ (trajectory). open traces bridges both, the complete record of process + output.

## AAIF (Agentic AI Foundation)

Launched Dec 2025 by Anthropic, OpenAI, Block/Linux Foundation. Governs AGENTS.md, MCP, and Goose. open traces aligns with AAIF standards from the start.

## The Core Insight

Agent Trace preserves _which_ lines came from AI. ATIF/ADP preserve _how_ the agent reasoned. Neither alone tells the complete story. open traces connects the full conversation trajectory to the specific code output at line granularity.

## Message Taxonomy

open traces adopts a training-oriented message taxonomy that is a superset of traces.com's sharing-oriented 5-type classification:

| traces.com type | open traces fields |
|---|---|
| `user_message` | `role: "user"` |
| `agent_text` | `role: "agent"`, no tool_calls |
| `agent_thinking` | `role: "agent"`, `reasoning_content` populated |
| `tool_call` | `role: "agent"`, `tool_calls` array |
| `agent_context` | `role: "agent"`, `call_type: "warmup"` |

The `mcp__{server}__{tool}` naming convention for MCP tools is adopted directly from traces.com, aligning with the AAIF/MCP ecosystem.
