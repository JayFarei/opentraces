# Changelog

All notable changes to the opentraces-schema package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) with
schema-specific semantics described in VERSION-POLICY.md.

## [Unreleased]

### Removed
- `Attribution.version` field. Redundant with `TraceRecord.schema_version` on the
  parent record. Removed while pre-1.0 makes breaking changes cheap.

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
