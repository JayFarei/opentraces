# Assess

`opentraces assess` scores traces against five consumer-facing rubrics before you push.
It's optional but recommended: low-quality batches affect downstream training and analytics
pipelines, and the score shows up as badges on your HuggingFace dataset card.

## The recommended workflow

```bash
opentraces commit --all     # move captured traces from inbox to committed
opentraces assess           # score the committed batch, see failures before push
opentraces push             # upload only when you're happy
```

Or do it all at once:

```bash
opentraces push --assess    # upload + score + embed in dataset card in one step
```

## How scoring works

Assessment is **deterministic by default**: every check is a Python function over
the `TraceRecord` fields. No LLM calls, no external requests, no randomness.
The same trace always produces the same score.

Each trace is scored against all five personas. Per-persona score is a weighted
average of its individual checks (0-100%). Batch score is the average across traces.

### The five personas

| Persona | What it checks | Who uses it |
|---------|----------------|-------------|
| **Conformance** | Schema validity: trace IDs, content hash, timestamps, steps present, security scanned | Anyone ingesting opentraces data |
| **Training** | SFT readiness: alternating roles, tool_call/observation pairing, reasoning coverage | Model fine-tuners |
| **RL** | Outcome signals: committed flag or terminal_state, signal confidence, cost, model ID | RLHF / reward modeling |
| **Analytics** | Observability: cache hit rate, cost, duration, per-step timestamps | Infra / cost dashboards |
| **Domain** | Discoverability: language ecosystem, dependencies, task description, VCS info | Dataset search and filtering |

### Conformance

Structural checks that apply to every trace regardless of agent type:

| Check | Description |
|-------|-------------|
| C1: schema_version | Matches current schema version |
| C2: trace_id format | Valid UUID-like string (≥32 chars with dashes) |
| C3: content_hash | 64-character hex, present |
| C4: agent name | Non-empty agent identifier |
| C5: timestamps | Both timestamp_start and timestamp_end present |
| C6: steps present | At least one step recorded |
| C7: security scanned | `security.scanned = True` |

### Training

Grounded in ADP (Agent Data Protocol) empirical requirements for SFT pipelines:

| Check | Description |
|-------|-------------|
| T1: alternating roles | user/agent steps alternate ≥90% of transitions (≥50% for conversation-turn sources) |
| T2: tool_call pairing | Every tool_call_id has a matching observation |
| T3: reasoning coverage | `reasoning_content` present on agent steps |
| T4: data cleanliness | No redaction markers in step content |

### RL

Checks the reward proxy signal appropriate to the agent's execution context:

| Check | Description |
|-------|-------------|
| RL1: outcome signal | `committed=True` for devtime agents; `terminal_state` or `reward` for runtime agents |
| RL2: signal confidence | `signal_confidence` is `derived` or `annotated` (not default) |
| RL3: cost signal | `estimated_cost_usd > 0` (differentiates traces for cost-aware RL) |
| RL4: model identified | `agent.model` populated (needed for per-model policy training) |

### Analytics

Observability checks that differentiate opentraces from trace-level-only sources.
Checks that require per-step data are automatically skipped for `conversation_turn`
fidelity sources (e.g. Hermes imports), which only have session-level timestamps:

| Check | Description |
|-------|-------------|
| A1: cache_hit_rate | Computed and in [0.0, 1.0] (skipped for runtime) |
| A2: estimated_cost | `estimated_cost_usd > 0` |
| A3: total_duration | `total_duration_s > 0` (skipped for runtime) |
| A4: step timestamps | Timestamps on >80% of steps (skipped for conversation_turn) |
| A5: token breakdown | Per-step `input_tokens` and `output_tokens` present |
| A6: token consistency | Step-sum ≈ session total (within 10%) |

### Domain

Checks that enable HuggingFace dataset discovery and filtering:

| Check | Description |
|-------|-------------|
| D1: language_ecosystem | Populated (skipped for runtime with no code-writing tool calls) |
| D2: dependencies | At least one dependency when language detected |
| D3: task description | Meaningful task description (>10 chars) |
| D4: VCS info | `environment.vcs.base_commit` present (skipped for runtime) |
| D5: code snippets | At least one snippet captured (skipped for runtime) |
| D6: attribution | Attribution data present |
| D7: agent identity | Agent name + version OR name alone for runtime sources |

## Fidelity-aware scoring

Some sources (like Hermes imports) provide conversation turns rather than individual
API calls. Checks that require call-level data are automatically marked `skipped`
for these sources and excluded from the weighted average. This prevents penalizing
community datasets for structural limitations of the source format.

The `step_fidelity` field on each trace records this: `"individual_api_call"` (devtime)
vs `"conversation_turn"` (Hermes, other community imports).

## Gate thresholds

The gate blocks push when any persona falls below its threshold:

| Persona | Min (any trace) | Min (batch average) |
|---------|-----------------|---------------------|
| Conformance | 70% | 80% |
| Training | 40% | 45% |
| RL | — | 40% |
| Analytics | 60% | 70% |
| Domain | 45% | 55% |

Gate `FAILING` does not block `push` by default. It's a signal, not a hard
stop — you can push a failing batch and the gate status will be visible in
the dataset card. Use `--gate` to enforce hard blocking (coming soon).

## Dataset card integration

When you run `opentraces push --assess`, quality scores are embedded in the
HuggingFace dataset README as shields.io badges and a scorecard table.

Here's what a dataset card looks like after `push --assess` — live preview
using the actual scores from `OpenTraces/opentraces-runtime`:

[![Overall Quality 78.1%](https://img.shields.io/badge/Overall_Quality-78.1%25-ffc107)](https://opentraces.ai) [![Gate FAILING](https://img.shields.io/badge/Gate-FAILING-dc3545)](https://opentraces.ai) ![Conformance 88.4%](https://img.shields.io/badge/Conformance-88.4%25-28a745) ![Training 89.0%](https://img.shields.io/badge/Training-89.0%25-28a745) ![RL 73.4%](https://img.shields.io/badge/RL-73.4%25-ffc107) ![Analytics 55.7%](https://img.shields.io/badge/Analytics-55.7%25-fd7e14) ![Domain 84.1%](https://img.shields.io/badge/Domain-84.1%25-28a745)

The scorecard table shows per-persona min/max/average with PASS / WARN / FAIL
status per rubric, and the scores are also written to YAML frontmatter as
HuggingFace-searchable keys (`conformance_score`, `training_score`, etc.).

A `quality.json` sidecar is uploaded alongside the data shards for machine consumers.

