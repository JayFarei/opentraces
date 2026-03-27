# opentraces.ai Design Discussion Log

Date: 2026-03-27

## Context

Grill session on `resources/intent.md` to resolve all open design questions before responding to the Hugging Face founder's request for help (deadline: 2026-03-29).

## Key Context Established During Discussion

- The HF founder (Clem Delangue) has requested help, this is demand-pull, not a cold pitch
- opentraces brand must remain independent from HF, HF is infrastructure, not owner
- Business model: open-source protocol + free analytics (no signup, HF user ID lookup), monetize future products built on learnings from trace data
- Protocol maintainer position = platform leverage for future products

## Resolved Design Decisions

### Q1: ATIF Alignment Strategy (intent.md open question #4)

**Decision: Own schema with a structured ATIF export command.**

ATIF v1.6 is still an RFC. Tying the schema to their governance and release cadence blocks shipping fields they haven't agreed on (like the `attribution` block, the strongest differentiator). Own the schema, ship `opentraces export --format atif` as a lossy conversion for training pipelines that already consume ATIF. Schema becomes a superset.

### Q2: v0.1 Agent Scope (intent.md open question #12)

**Decision: Claude Code only for v0.1.**

The differentiator is schema depth, not agent breadth. DataClaw already covers 7 agents with a shallow schema. Ship 1 agent with the richest schema anyone has seen. The adapter contract being ready for multi-agent is enough.

### Q3: DataClaw Import Adapter (intent.md open question #11)

**Decision: Ship it in v0.1.**

Low effort (their format is simple flat JSONL), signals "we're the upgrade path" not "we're a wrapper," captures ~25 existing contributors. `opentraces import --from dataclaw` positions opentraces as the destination.

### Q4: Consent Model (intent.md open question #1)

**Decision: Per-project with per-session override.**

Dominant workflow is "this repo is open-source, always share" or "this repo is a client project, never share." Per-project persistence (`opentraces config set --project . --tier danger`) handles 90% of cases. Per-session override handles the rest. Per-turn is too granular.

### Q5: Annotation Burden (intent.md open question #2)

**Decision: Zero required annotation in v0.1. Derive everything deterministically.**

Schema already captures `outcome.committed` (from git), `outcome.patch` (from diff), `outcome.commit_sha`. Requiring manual `outcome.success` annotation will kill adoption. Make annotation optional via Tier 3 review in v0.2. Never require it.

### Q6: Dataset Governance (intent.md open question #3)

**Decision: Federated-first with periodic merge into canonical dataset.**

Each user publishes to `username/opentraces-claude-code`. Canonical `opentraces/agent-traces-v1` is a periodic merge of all `opentraces`-tagged repos, following the nlile pattern. Low friction to publish, high discoverability for consumers.

### Q7: Sub-Agent Inclusion (intent.md open question #7)

**Decision: Include sub-agent steps inline with `parent_step` links. Defer full transcript capture.**

A trace without sub-agent steps is structurally incomplete since Claude Code spawns them on nearly every session. Security concern handled by the same Tier 1 regex scan.

### Q8: Quality Filtering Threshold (intent.md open question #8)

**Decision: Min 1 tool call + min 2 steps.**

A session with 0 tool calls is just a conversation, not an agent trace. A session with 1 step is just a prompt with no response. These add noise without training value.

### Q9: Warm-Up Call Handling (intent.md open question #9)

**Decision: Include with `call_type: "warmup"` label.**

Filtering loses information. Labeling preserves it while letting consumers filter trivially. Costs almost nothing to implement, useful for caching researchers.

### Q10: Brand / Name

**Decision: Keep opentraces.**

The brand stays independent from HF. Enables future product expansion. HF is infrastructure, opentraces is the product.

### Q11: MCP Tool Naming (intent.md open question #22)

**Decision: Adopt traces.com's `mcp__{server}__{tool}` convention.**

Three independent projects have converged on this naming. Fighting it creates unnecessary friction.

### Q12: Git Notes for Trace-Commit Linking (intent.md open question #24)

**Decision: Defer to v0.2.**

v0.1 already captures `outcome.commit_sha` which gives the link. Git notes are a discovery mechanism, not a storage mechanism. For v0.1, discovery happens on HF Hub via tags and Dataset Viewer.

### Q13: Server-Side AI Enrichment (intent.md open question #23)

**Decision: Defer entirely.**

Adding LLM dependency contradicts the "passive, deterministic" principle. Adds cost, latency, API key dependency. Let the community build discovery tools on top of clean structured data.

### Q14: Dashboard Timing

**Decision: Ship the dashboard as part of v0.1, not v0.1.1.**

The dashboard IS the contributor incentive. If someone publishes their first trace and there's no dashboard, the activation moment is missed. It's a Gradio app reading from HF, small scope. No auth needed, just HF username lookup.

### Q15: Tier 2 Classifier Pipeline

**Decision: Rethink entirely. Drop LLM classifier.**

"De-anonymisation risk scoring" and "stylometric signals" are research problems, not engineering problems. Realistic middle ground: regex scan + heuristic flagging (internal hostnames, AWS account IDs, DB connection strings) + escalation to human review. Drop "small LLM classifier" and "embedding-based" language.

### Q16: Community Tag Strategy (intent.md open question #13)

**Decision: Both `opentraces` and `agent-traces`.**

`opentraces` is the brand tag, `agent-traces` is the community discovery tag that encompasses DataClaw and other datasets. Rising tide for the ecosystem.

### Q17: Competitive Framing vs DataClaw (intent.md open question #15)

**Decision: Neutral infrastructure positioning. Never mention DataClaw's "protest art" framing.**

Audience is ML researchers and training teams who don't care about protests. Position as "open infrastructure for the training community."

### Q18: Attribution Block Accuracy

**Decision: Ship as best-effort in v0.1, not guaranteed-accurate.**

Build from the unified diff (`outcome.patch`) rather than individual edit operations. The diff is ground truth. Map diff hunks back to conversation steps via timestamp correlation. Accept approximate attributions for overlapping ranges. Label with a confidence field.

### Q19: Cost Estimation (`metrics.estimated_cost_usd`)

**Decision: Static pricing table in the package, versioned alongside the schema.**

Users can override with `opentraces config set --pricing-file custom.json`. Make clear it's an estimate. Update the table with each release.

### Q20: Launch Strategy

**Decision: Responding to HF founder's request for help by 2026-03-29.**

This is demand-pull, the strongest launch position. HF provides distribution (blog, featured status) and infrastructure sponsorship. opentraces provides the protocol, CLI, and dashboard.

### Q21: Dashboard Authentication

**Decision: No auth. Public data, public dashboard.**

Data is already public on HF Hub (CC-BY-4.0). Looking up anyone's stats is a feature, not a bug. "See how top contributors use agents" drives adoption via social proof.

### Q22: Protocol Legitimacy

**Decision: Position the schema as something HF co-stewards (not co-owns).**

opentraces maintains it, HF blesses it as the recommended format for agent traces on their platform. Similar to Cursor's relationship with Agent Trace spec.

## Remaining Questions Not Yet Resolved

- **intent.md Q10 (`output_summary` length)**: How long should tool result summaries be? Deferred, implementation detail.
- **intent.md Q17 (CASS license)**: License clarification needed before any integration. Blocks v0.2 multi-agent options.
- **intent.md Q18 (`franken_agent_detection` standalone)**: Whether to approach Jeff Emanuel about publishing the crate. v0.2 decision.
- **intent.md Q19 (CASS robot API)**: Whether to implement `introspect`/`capabilities` in v0.1. Leaning yes per intent.md scope.
- **intent.md Q20 (traces.com import)**: Whether to support importing from traces.com. Low priority.
- **intent.md Q14 (Schema migration for DataClaw users)**: Whether to ship `opentraces migrate --from dataclaw`. Covered by the import adapter decision (Q3).
- **What exactly Clem asked for**: The specific framing of his request determines emphasis in the response.

## Brand & Business Model

- opentraces is the product, HF is the infrastructure
- Free forever: CLI + dashboard + open protocol, no signup, HF user ID is identity
- Future products built on aggregate learnings from trace corpus
- Protocol maintainer + largest open trace corpus = compounding data advantage
- Relationship model: opentraces builds ON HF, not a project OF HF
