# Changelog

All notable changes to the opentraces-schema package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) with
schema-specific semantics described in VERSION-POLICY.md.

## [0.3.0] - 2026-04-12

### Added
- `Intent` model — session-level summary block, parallel to `Task` and `Outcome`.
  Optional fields: `title`, `summary`, `source` (closed enum: `llm_hook` /
  `post_processor` / `user`), `model`. See `RATIONALE-0.3.0.md` for the Task
  vs Intent split.
- `TraceRecord.intent: Intent | None` — absent on traces captured before
  Intent enrichment ran or when `intent.mode=off`.
- `GitLink` model — evidence-graded link between a trace and a commit/revision
  (plan 041). `tier`: `tool_emitted` | `tool_emitted_with_divergence` |
  `overlapping` | `orphan`. Optional lazy-computed `commit_reachable` and
  `content_alive` liveness booleans. Supports `vcs_type="jj"` for Jujutsu.
- `TraceRecord.git_links: list[GitLink]` — many-to-many link between traces
  and commits.
- `TraceRecord.lifecycle: Literal["provisional", "final"]` — RFC #25.
  `provisional` is the default; promoted to `final` once a post-commit hook
  correlates the trace to a revision.
- `Task.repository_url: str | None` — canonical remote URL (RFC #22).
- `Attribution.revision: dict | None` — `{vcs_type, revision}` pin for
  attribution data.
- `Attribution.unaccounted_files: list[str] | None` — surfaces Bash-applied
  edits absent from tool-call attribution.
- `AttributionRange.original: dict | None` — pre-divergence
  `{start_line, end_line, content_hash}` (RFC #5).
- `AttributionRange.change_type: Literal["addition","modification","deletion"]` —
  default `addition` (RFC #11).
- `AttributionRange.contributor: dict | None` — per-range contributor override
  for stamping `mixed` on divergent ranges.
- `AttributionConversation.ids: dict[str, str | list[str]] | None` — provider-
  native conversation identifiers (RFC #9).
- `AttributionConversation.related: list[dict] | None` — `{type, url}` baseline
  vocabulary (RFC #16) for plan/issue/PR links.

### Changed
- `TraceRecord.compute_content_hash()` now excludes `intent` alongside
  `content_hash` and `trace_id`, so re-running summarization does not change
  the identity of an otherwise-unchanged trace.

### Compatibility
- Additive: traces written against 0.2.x load cleanly with `intent=None`.
- Unknown extra fields remain silently ignored (Pydantic v2 default), matching
  prior behavior.

## [0.1.1] - 2026-03-29

### Changed
- `SCHEMA_VERSION` bumped from `0.1.0` to `0.1.1` (patch release aligned with CLI v0.1.1).
  No model changes; version bump only.

## [0.2.0] - 2026-04-01

### Added
- `TraceRecord.execution_context: Literal["devtime", "runtime"] | None` — session-level
  discriminator distinguishing code-editing agents (devtime: Claude Code, Cursor, Codex)
  from task-execution agents (runtime: browser automation, API workflows, RL environments).
  Nullable and backward compatible; existing devtime traces are unaffected.
- `Outcome.terminal_state: Literal["goal_reached", "interrupted", "error", "abandoned"] | None` —
  how the action trajectory ended. Meaningful for runtime agents; null for devtime traces.
- `Outcome.reward: float | None` — numeric reward signal from an RL environment or evaluator.
  Use `signal_confidence="derived"` when set directly from environment output.
- `Outcome.reward_source: str | None` — free string identifying the reward provider.
  Canonical values: `rl_environment`, `judge`, `human_annotation`, `orchestrator`.

### Changed
- `Outcome` docstring updated to describe devtime vs runtime field sets and
  how `execution_context` should guide consumers choosing which fields to read.
- `SCHEMA_VERSION` bumped from `0.1.1` to `0.2.0`.

## [0.1.0] - 2026-03-27

### Added
- Initial schema release with 15 Pydantic v2 models
- `TraceRecord` top-level model: one JSONL line per complete agent session
- `Step` model oriented around TAO (Thought-Action-Observation) loops, not conversational turns
- `Outcome` model with RL-ready signals: `success`, `signal_source`, `signal_confidence` (derived/inferred/annotated)
- `Attribution` block (experimental) bridging trajectory data and code attribution per Agent Trace spec
- Sub-agent hierarchy via `Step.parent_step`, `Step.agent_role`, `Step.subagent_trajectory_ref`
- `Step.call_type` (main/subagent/warmup) for filtering cache-priming calls
- System prompt deduplication via hash-keyed `system_prompts` dict on `TraceRecord`
- `SecurityMetadata` with 3-tier classification (1=open, 2=guarded, 3=strict)
- Content hashing (SHA-256) on `TraceRecord` for cross-upload deduplication
- `AttributionRange.content_hash` using murmur3 for cross-refactor tracking
- `Observation.output_summary` for lightweight filtering without loading full tool results
- `TokenUsage` with `prefix_reuse_tokens`, `cache_read_tokens`, `cache_write_tokens`
- `Metrics` model with session-level aggregates and `estimated_cost_usd`
- `Environment` and `VCS` models for runtime context and reproducibility
- `Task` model with `source`, `repository`, `base_commit`
- `Agent` model using `provider/model-name` convention from models.dev
- `Snippet` model for extracted code blocks linked to source steps

### Design References
- See [RATIONALE-0.1.0.md](RATIONALE-0.1.0.md) for the design basis of each decision in this version
