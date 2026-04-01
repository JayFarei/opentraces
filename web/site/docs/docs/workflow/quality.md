# Assess

`opentraces assess` scores committed traces against five consumer-facing rubrics. Run it after committing, before you push:

```bash
opentraces assess
```

Scores are printed to the terminal. Low-scoring traces show which checks failed so you can decide whether to fix or push anyway. Assessment only runs against committed traces — run `opentraces commit` first if your inbox isn't empty.

You can also score and push in one step with `opentraces push --assess`, which uploads and embeds the scorecard in the HuggingFace dataset card. See [Push](/docs/workflow/pushing) for details.

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

When you push with `--assess`, scores are embedded in the HuggingFace dataset card as badges and a scorecard table, and written to YAML frontmatter as searchable keys. See [Push](/docs/workflow/pushing) for details.
