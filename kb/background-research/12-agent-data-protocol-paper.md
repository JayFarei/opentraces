# Agent Data Protocol (ADP): R&D Scouting Brief

> Research date: 2026-03-28
> Source: https://arxiv.org/html/2510.24702v2 (Song et al., CMU/OSU/HKU/Duke/Fujitsu/All Hands AI, March 2026)
> Category: Protocol / schema / dataset for agent trajectory fine-tuning
> Context: Evaluating ADP as the most directly comparable schema to opentraces-schema, already cited in our RATIONALE-0.1.0.md. This brief deepens the analysis from our earlier landscape scans (01, 05) and grounds schema decisions against ADP's empirical results.

---

## Executive Summary

ADP is a Pydantic-based "interlingua" for unifying heterogeneous agent trajectory datasets into a single format that can be converted to any downstream training harness. The paper (v2, March 2026) demonstrates that SFT on 1.3M trajectories unified via ADP yields ~20% average improvement over base models across SWE-Bench, WebArena, AgentBench, and GAIA, with cross-task transfer benefits. ADP validates several of our schema decisions (step-based TAO model, Pydantic v2, action/observation separation) while operating in a complementary scope: ADP optimizes for training-pipeline interop across agent harnesses, opentraces-schema optimizes for community collection with security tiers, attribution, and environment metadata. **The two schemas are not competitors, they are adjacent layers. Our `opentraces export --format adp` path is well-justified.**

---

## Problem It Solves

Agent training data exists in abundance (1.3M+ trajectories across 13 public datasets) but is trapped in incompatible formats. Each dataset has its own action spaces, observation structures, and serialization conventions. Combining datasets for SFT currently requires O(D x A) custom converters (D datasets, A agent frameworks). ADP reduces this to O(D + A) by serving as a hub schema: each dataset converts to ADP once, each harness reads from ADP once.

---

## How It Works

### Core Schema

Implemented as Pydantic models. Each trajectory is a `Trajectory` object:

```
Trajectory:
  id: str
  content: list[Action | Observation]  # alternating sequence
  details: dict[str, Any]              # flexible metadata

Action (discriminated union):
  APIAction:    function, kwargs, description
  CodeAction:   language, content, description
  MessageAction: content

Observation (discriminated union):
  TextObservation: source ("user" | "environment"), content
  WebObservation:  html, axtree, url, viewport_size, image_observation
```

### Conversion Pipeline

Three stages:
1. **Raw -> ADP**: One converter per dataset (avg ~375 LOC each, total 4,892 LOC for 13 datasets)
2. **ADP -> SFT**: One converter per agent harness (avg ~77 LOC each: OpenHands ~150, SWE-Agent ~50, AgentLab ~30)
3. **Quality Assurance**: Automated Pydantic validation, tool call format checks, >=80% thought coverage threshold

### Key Concepts

- **APIAction**: Maps to tool/function calls (browsing, API use, file ops). Closest analog to our `ToolCall`
- **CodeAction**: Code generation and execution. No direct analog in opentraces, we capture code via tool call inputs/outputs
- **MessageAction**: Agent natural language. Maps to our `Step.content` where `role="agent"`
- **TextObservation**: Environment feedback. Maps to our `Observation.content`
- **WebObservation**: Browser state. No analog in opentraces v0.1 (we target code agents, not web agents)

---

## Maturity and Traction

- **License**: Code and data publicly released (license details in Appendix F)
- **Backing**: Carnegie Mellon, Ohio State, HKU, Duke, Fujitsu Research, All Hands AI (OpenHands)
- **Scale**: 1.3M trajectories, 13 datasets, 3 agent frameworks, 3 model sizes (7B/14B/32B)
- **Benchmarks**: SWE-Bench Verified, WebArena, AgentBench OS, GAIA
- **Website**: agentdataprotocol.com
- **Paper versions**: v1 (Oct 2025), v2 (March 2026)

---

## Experimental Results (Why This Matters)

The paper provides the strongest public evidence that **unified trajectory data improves agent SFT**, which is the core thesis behind opentraces:

| Benchmark | Base (7B) | ADP Fine-tuned (7B) | Gain |
|-----------|-----------|---------------------|------|
| SWE-Bench Verified (SWE-Agent) | 0.4% | 20.2% | +19.8pp |
| SWE-Bench Verified (OpenHands) | 2.8% | 20.4% | +17.6pp |
| WebArena (AgentLab) | 4.5% | 21.0% | +16.5pp |
| AgentBench OS (OpenHands) | 3.5% | 27.1% | +23.6pp |
| GAIA (OpenHands) | 7.3% | 9.1% | +1.8pp |

At 32B scale, ADP-tuned Qwen2.5 reaches 40.3% on SWE-Bench Verified, matching Claude 3.5 Sonnet. Cross-task transfer is also demonstrated: mixed ADP training outperforms single-domain tuning even on the target domain.

**Implication for opentraces**: This empirically validates that community-collected traces, if normalized to a consistent schema, have direct training utility. The "donate traces -> improve agents" value prop is not hypothetical.

---

## Strengths

- **Empirical validation at scale**: 1.3M trajectories, 4 benchmarks, 3 model sizes, 3 frameworks. Most thorough public evaluation of unified agent SFT
- **Simplicity**: 3 action types + 2 observation types cover coding, browsing, tool use, and SWE. Minimal schema surface area
- **Pydantic-native**: Same implementation technology as opentraces-schema, enabling direct interop
- **O(D+A) scaling**: The hub-and-spoke conversion argument is compelling and quantified (489K LOC without ADP vs 12.6K with ADP for 100 harnesses)
- **Cross-task transfer proven**: Diverse training data helps even within a single domain, not just across domains
- **Open release**: Code and data publicly available, unlike most industry agent training efforts

## Limitations and Risks

- **No security/anonymization layer**: ADP assumes datasets are already safe to publish. No concept of security tiers, secret scanning, or PII redaction. This is the gap opentraces fills
- **No attribution**: No concept of which code lines the agent produced. Agent Trace / our Attribution block is entirely absent
- **No environment metadata**: No OS, shell, VCS, language ecosystem, or git context. Training pipelines get trajectories but lose reproducibility signals
- **No token-level data**: Like us, no logprobs or token IDs (they acknowledge this is a future direction)
- **No outcome/reward signals**: No success/failure, no commit state, no signal confidence. Trajectories are treated as uniformly valid demonstrations
- **No system prompt handling**: System prompts are not captured or deduplicated. In multi-agent sessions, this is significant storage and context waste
- **Flat trajectory model**: `content` is a flat list of actions/observations. No hierarchy for sub-agent spawning, no parent_step, no call_type. Multi-agent architectures (main -> explore -> plan -> execute) lose their structure
- **No cost/usage metrics**: No token counts, no duration, no cache hit rates. Cannot be used for cost modeling or efficiency analysis
- **Web-biased observation model**: WebObservation is a first-class type, but there is no TerminalObservation or FileObservation for code agent workflows. Code agent output is flattened into TextObservation
- **No deduplication mechanism**: No content hashing for cross-dataset dedup. With 1.3M trajectories from overlapping sources, this matters

---

## Schema Comparison: ADP vs opentraces-schema v0.1.0

| Capability | ADP | opentraces-schema | Notes |
|-----------|-----|-------------------|-------|
| **Core model** | Trajectory (flat action/observation list) | TraceRecord (hierarchical steps) | Both Pydantic v2. opentraces preserves agent hierarchy |
| **Action types** | APIAction, CodeAction, MessageAction | ToolCall (unified) + Step.content | ADP separates code from tool calls, we unify under ToolCall |
| **Observation types** | TextObservation, WebObservation | Observation (linked via source_call_id) | ADP has richer web state capture, we have tighter tool linkage |
| **Hierarchy** | Flat sequence | parent_step, agent_role, call_type, subagent_trajectory_ref | Critical for multi-agent architectures (Claude Code sub-agents) |
| **Security** | None | 3-tier (open/guarded/strict) + SecurityMetadata | Our core differentiator for community collection |
| **Attribution** | None | Embedded Agent Trace block (files, ranges, content_hash) | Bridges trajectory + code output |
| **Environment** | None | OS, shell, VCS (git), language_ecosystem | Reproducibility and filtering |
| **Outcome/RL** | None | success, signal_source, signal_confidence, committed, commit_sha | Enables RL/DPO pipelines without annotation |
| **Token usage** | None | Per-step: input, output, cache_read, cache_write, prefix_reuse | Cache-aware cost modeling |
| **Metrics** | None | Session-level aggregates (total_tokens, duration, cost) | Analytics and cost modeling |
| **System prompts** | Not captured | Hash-keyed dedup dict | Significant storage savings for multi-agent |
| **Tool definitions** | Not captured | tool_definitions list | Schema for available tools |
| **Content dedup** | None | SHA-256 content_hash | Sharded upload dedup |
| **Web state** | First-class (html, axtree, viewport) | Not in v0.1 | ADP covers browsing agents, we focus on code agents |
| **Metadata** | `details: dict` | `metadata: dict` | Same extensibility pattern |

---

## Cross-Task Transfer: What It Means for opentraces

ADP's cross-task transfer findings (Table 6 in the paper) are directly relevant:

- SWE-smith-only training on OpenHands 7B: 1.0% on SWE-Bench. ADP mixed: 10.4%. **10x improvement from diversity alone**
- Go-Browse-only on AgentLab 7B: 16.0% on WebArena. ADP mixed: 20.1%. **25% relative improvement**
- AgentInstruct-only on OpenHands Qwen-3-8B: 21.5% on AgentBench. ADP mixed: 25.7%. **20% relative improvement**

**Implication**: Even if opentraces initially collects only Claude Code coding traces, mixing them with other trajectory types (browsing, tool use) at training time improves coding performance. This strengthens the case for our adapter contract and future multi-agent support.

---

## Integration Analysis: opentraces-schema

### Fit Assessment

**Strong Fit** as an export target, not a replacement.

ADP and opentraces-schema are complementary layers:
- **opentraces-schema** is a *collection* format: captures everything about a real user session (security, environment, attribution, cost, outcome) for community sharing
- **ADP** is a *training* format: strips away metadata to produce clean action/observation sequences for SFT pipelines

The relationship is: `agent session -> opentraces (collect + share) -> ADP (train)`.

### Integration Points

1. **`opentraces export --format adp`**: Convert TraceRecord to ADP Trajectory
   - `Step` with tool_calls -> `APIAction` (function=tool_name, kwargs=input)
   - `Step` with code in content -> `CodeAction` (language inferred from tool context)
   - `Step` with role=agent, no tools -> `MessageAction`
   - `Observation` -> `TextObservation` (source="environment")
   - `Step` with role=user -> `TextObservation` (source="user")

2. **`opentraces import --format adp`**: Ingest ADP datasets into opentraces
   - `APIAction` -> Step with ToolCall
   - `CodeAction` -> Step with ToolCall (tool_name="code_execution")
   - `TextObservation` -> Observation
   - `details` dict -> metadata dict
   - Security tier defaults to 1 (open) since ADP data is already public

3. **Schema validation bridge**: Both use Pydantic v2, so we can directly instantiate ADP models from opentraces data for validation

### Effort Estimate

**Quick (hours)**: Export converter is straightforward field mapping. Import converter is slightly more work due to hierarchy reconstruction (ADP's flat list -> our step tree), but the loss is acceptable for imported data.

### Open Questions

1. **CodeAction mapping**: ADP separates code execution from tool calls. Should our export preserve this distinction, or unify everything under APIAction? The paper shows CodeAction is 24% of all actions, a significant category
2. **WebObservation**: If we ever expand beyond code agents, should we adopt ADP's WebObservation model or design our own?
3. **Quality thresholds**: ADP requires >=80% of tool calls paired with reasoning text. Should opentraces adopt a similar quality gate at upload time?

---

## Competitive Positioning

ADP is **not a competitor** to opentraces. It is a potential downstream consumer. The paper explicitly identifies the data collection bottleneck: "the bottleneck is not lack of underlying data but fragmentation across heterogeneous formats." ADP solves the fragmentation problem for *existing* datasets. opentraces solves the *collection* problem for new community-contributed data.

The ideal ecosystem path is:
1. Users donate traces via opentraces CLI -> HuggingFace dataset
2. Researchers convert the HF dataset to ADP format
3. ADP converts to their preferred agent harness (OpenHands, SWE-Agent, AgentLab)
4. SFT on unified data improves agents

This is explicitly the pipeline ADP's paper envisions but does not build. The paper's own datasets are all researcher-curated or synthetic, none are community-donated real-world sessions. opentraces fills that gap.

---

## Validation of Our Schema Decisions

The ADP paper validates several choices we made in opentraces-schema v0.1.0:

| Our Decision | ADP Evidence |
|-------------|-------------|
| **Step-based TAO model** (not turns) | ADP uses alternating action/observation. Confirmed as universal primitive across 13 datasets |
| **Pydantic v2 implementation** | ADP also chose Pydantic. The ML Python ecosystem has converged |
| **Action/observation separation** | ADP's core structure. 53% API actions, 24% code, 23% message across 1.3M trajectories |
| **Reasoning content as explicit field** | ADP's `description` field on actions serves the same purpose. 83.8% of dataset actions include reasoning text |
| **Superset schema with export** | ADP's hub model validates the "collect rich, export lean" approach |
| **Claude Code first, adapter contract for multi-agent** | ADP covers 4 task domains (coding, SWE, browsing, tool use). Cross-task transfer proves value of eventual multi-agent support |

And the paper implicitly validates our *additions* beyond ADP:

| Our Addition | Why ADP Confirms the Need |
|-------------|--------------------------|
| **Security tiers** | ADP has zero anonymization. Cannot handle real user sessions with secrets |
| **Attribution** | ADP tracks process only, not output. Cannot answer "which code did the agent write" |
| **Outcome signals** | ADP trajectories are treated as uniformly valid. No mechanism to weight by quality |
| **Hierarchy (parent_step, agent_role)** | ADP's flat list loses multi-agent structure. 13 datasets may not need it, but real Claude Code sessions do |
| **Token usage + cache metrics** | ADP ignores cost. Community contributors will care about cost per trace |

---

## Key Takeaways

1. **ADP empirically proves that unified trajectory schemas improve agent SFT by ~20%**. This is the strongest public evidence for the core opentraces value proposition: community traces have training utility
2. **ADP and opentraces are complementary layers, not competitors**. ADP is a training interlingua, opentraces is a collection protocol. `opentraces export --format adp` is the bridge
3. **Our schema's additions (security, attribution, hierarchy, outcome, metrics) are justified by ADP's gaps**: ADP works for curated research datasets but cannot handle community-donated real-world sessions without the protections opentraces provides
4. **Cross-task transfer is real**: Even single-domain traces become more valuable when mixed with diverse data at training time, validating our adapter contract for future multi-agent support
5. **The 80% reasoning coverage threshold is worth adopting**: ADP's quality gate (>=80% of tool calls paired with reasoning text) is a practical quality signal we should consider for upload validation

## Sources

- [Agent Data Protocol paper (v2)](https://arxiv.org/html/2510.24702v2) - Song et al., March 2026
- [ADP website](https://agentdataprotocol.com)
- [opentraces-schema models.py](../packages/opentraces-schema/src/opentraces_schema/models.py)
- [opentraces-schema RATIONALE-0.1.0.md](../packages/opentraces-schema/RATIONALE-0.1.0.md)
- [Prior research: Code Agent Trace Formats (01)](01-code-agent-trace-formats-and-standards.md)
- [Prior research: Community Trace Sharing Landscape (05)](05-community-trace-sharing-landscape.md)
