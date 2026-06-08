# Changelog

All notable changes to the opentraces-schema package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) with
schema-specific semantics described in VERSION-POLICY.md.

## [0.7.0] - 2026-06-08

Adds the dataset security policy contract: a workflow declares the security
posture of the rows it projects, and each dataset stores its resolved policy in
the manifest. Additive only; existing parsers continue to work.

See [RATIONALE-0.7.0.md](RATIONALE-0.7.0.md) for design notes.

### Added

**Dataset security policy (plan 092 Track 2)**

- `WorkflowSecurityContract` model, a dataset workflow's declared security
  contract (`required_tools`, `optional_tools`, `default_enabled_tools`,
  `disallowed_tools`, `allow_disable_required`).
- `DatasetSecurityPolicy` model, the resolved per-dataset policy stored on the
  manifest, seeded from a workflow contract and pinned to the source workflow
  digest. Carries `source`, `source_workflow_digest`, `required_tools`,
  `optional_tools`, `enabled_tools`, `disallowed_tools`,
  `allow_disable_required`, and recorded `overrides`.
- `DatasetSecurityOverride` model, an explicit recorded unsafe opt-out of a
  required security tool.
- `DatasetManifest.security: DatasetSecurityPolicy`, an additive optional field
  (defaults to an empty policy) so existing manifests load unchanged.
- `SecurityToolName` Literal and `SECURITY_TOOL_ORDER` tuple, the canonical
  security tool vocabulary kept in sync with the runtime tool registry.

### Changed

- `SCHEMA_VERSION` bumped to `0.7.0` (additive MINOR per VERSION-POLICY.md). The
  `TraceRecord` wire shape is unchanged; only new dataset-control models are
  added, so `migrate_record` is a transparent no-op across the
  `0.6.0 -> 0.7.0` boundary.

## [0.6.0] - 2026-05-21

Promotes trace patches to the authoritative dev-time output spine and removes
the legacy unified-diff field from `Outcome`.

See [RATIONALE-0.6.0.md](RATIONALE-0.6.0.md) for design notes.

### Added

**Trace patch spine (plan 080)**

- `Patch` model — one produced change per Edit/Write-derived trace patch,
  with `patch_id`, file path, step/tool joins, capture methods, snapshot refs,
  optional Git anchor, supersede chain, and capture limitations.
- `GitAnchor` model — evidence and firmness metadata for a patch matched into
  Git history.
- `TraceRecord.patches: list[Patch]` — the authoritative output set for a
  dev-time trace. A trace patch is one tool-produced change/hunk, not one
  file and not necessarily one commit.

### Changed

- `Outcome.committed`, `Outcome.commit_sha`, and `TraceRecord.git_links` are
  compatibility projections derived from `patches[].anchor` and
  `patches[].superseded_by`.
- Full patch, trail, context, and source evidence is resolved through bucket
  companions (`trail.jsonl.gz`, `context.jsonl.gz`, `sources.jsonl.gz`) instead
  of being embedded into `TraceRecord`.

### Removed

- `Outcome.patch` was removed. Consumers should read `TraceRecord.patches[]`
  for the structured output spine and use the trace's Trail companion for full
  patch history/diff content.

### Migration

- `opentraces_schema.migrations.migrate_record` (the previously-reserved
  migrations module, now implemented) upgrades a pre-0.6.0 raw record before
  validation: it reconstructs `patches[]` from the legacy `Outcome.patch`
  unified diff (one `Patch` per file, content-addressed `patch_id`) and
  preserves the raw diff verbatim under `metadata.legacy.patch`. Both the
  HuggingFace shard migration (`HFUploader.migrate_outdated_shards`) and the
  bucket path call it, so the `Outcome.patch` removal is non-lossy on upgrade.
  A bare `TraceRecord.model_validate` of a 0.3.0 record still drops the field
  (Pydantic `extra="ignore"`); always route legacy records through
  `migrate_record` first. See
  [MIGRATION-0.3.3-to-0.4.md](MIGRATION-0.3.3-to-0.4.md) for the full upgrade
  contract.

## [0.5.0] - 2026-05-18

Adds two Context Tree cross-reference fields (plan 077). Strictly additive
on top of `0.4.0`: both fields default to the empty value, existing JSONL
parses unchanged, and pre-0.5.0 readers continue to round-trip the records
they wrote.

See [RATIONALE-0.5.0.md](RATIONALE-0.5.0.md) for design notes.

### Added

**Context Tree cross-reference fields (plan 077)**

- `Step.context_node_id: str | None` — when populated, the `sha256:<hex>`
  id of the Context Tree node corresponding to this step. Lets consumers
  join a `Step` to the model's view at that moment in one hop.
- `TraceRecord.context_tree_summary: dict[str, Any]` — Context Tree
  projection summary stamped at trace time. Shape mirrors
  `ContextTreeProjection.summary()`: typical keys are `node_count`,
  `layer_count`, `active_path_leaf_id`, `capture_limitations`. Empty `{}`
  when Context Tree capture was not run.

## [0.4.0] - 2026-04-29

Adds the local dataset/workflow contract for Plan 057 (Milestone 2), with the
manifest-level scaffolding Plan 058 (Milestone 3) layers remote sync on top of.
Strictly additive on top of `0.3.0`.

See [RATIONALE-0.4.0.md](RATIONALE-0.4.0.md) for design notes.

### Added

**Dataset / workflow models (Plan 057)**

- `DatasetSchemaRef`, `WorkflowRef`, `ExecutorConfig`, `DatasetIdentity`,
  `DatasetCandidateQuery`, `DatasetSchedule`, `DatasetDiscoverability`,
  `DatasetManifest`, `DatasetRunRecord`, `DatasetRowIndexEntry` — local
  HF-shaped dataset contract.
- `ExecutorName`, `DatasetScope`, `DatasetIdentityMode`, `DatasetRunStatus`
  Literal aliases.
- `DatasetIdentity` validates that `mode="fields"` requires a non-empty
  `fields` list; `DatasetSchedule` validates that `enabled=True` requires a
  non-empty `every` interval string.

**Plan 058 scaffolding (eager evaluation surface only)**

- `DatasetRemote`, `DatasetPublicationPolicy`, `DatasetPublicationStateEntry`,
  `DatasetPublicationState`, plus `DatasetRemoteVisibility`,
  `DatasetRemoteSchemaPolicy`, `DatasetPublicationReviewPolicy`,
  `DatasetPublicationSecurityPolicy`, `DatasetPublicationLLMReviewPolicy`,
  `DatasetPublicationStatus` Literal aliases. Carrying these in `0.4.0` lets
  Plan 058 land remote sync without bumping the schema again.

**Trace index / Trace Map / Candidate Packet (Plan 056)**

- `TraceUnit`, `TraceFacet`, `TraceSignal`: addressable, bounded search
  documents derived from trace evidence (the public document model for
  `opentraces trace query` and `opentraces trace index`).
- `TraceMap`, `TraceMapNode`, `TraceMapEdge`: workflow-neutral evidence
  graph backing `opentraces trace map`.
- `CandidatePacket`: bounded search-result envelope returned by
  `opentraces trace query`.

### Changed

- All dataset models now declare `extra="forbid"` so unknown manifest keys
  fail validation instead of being silently ignored.
- `DatasetManifest` keeps `populate_by_name=True` so the on-disk
  `schema:` alias and the in-Python `schema_ref` attribute name both work.

### Compatibility

- Strictly additive wrt `0.3.0`. `TraceRecord` is unchanged; existing
  traces load without any field defaults shifting.

## [0.3.0] - 2026-04-16

First public schema release since `0.2.0`. A coherent single bump that folds
together the commit-correlation and richer-attribution work from this cycle.

### Added

**Commit correlation (plan 041)**

- `GitLink` model — evidence-graded link between a trace and a
  commit/revision. Tiers: `tool_emitted` | `tool_emitted_with_divergence`
  | `overlapping` | `orphan`. Optional lazy-computed `commit_reachable`
  and `content_alive` liveness booleans. Supports `vcs_type="jj"` for
  Jujutsu.
- `TraceRecord.git_links: list[GitLink]` — many-to-many link between
  traces and commits. A trace can link to many commits (rebase, squash,
  long session); a commit can link to many traces (cherry-pick,
  composition).
- `TraceRecord.lifecycle: Literal["provisional", "final"]` — RFC #25.
  `provisional` is the default; promoted to `final` once a post-commit
  hook correlates the trace to a revision.

**Richer attribution**

- `Attribution.revision: dict | None` — `{vcs_type, revision}` pin for
  attribution data.
- `Attribution.unaccounted_files: list[str] | None` — surfaces
  Bash-applied edits absent from tool-call attribution, at low confidence.
- `AttributionRange.original: dict | None` — pre-divergence
  `{start_line, end_line, content_hash}` (RFC #5). Populated when a
  formatter or human rewrote the agent's output after the fact.
- `AttributionRange.change_type: Literal["addition", "modification", "deletion"]`
  — default `addition` (RFC #11).
- `AttributionRange.contributor: dict | None` — per-range contributor
  override for stamping `mixed` on divergent ranges.
- `AttributionConversation.ids: dict[str, str | list[str]] | None` —
  provider-native conversation identifiers (RFC #9).
- `AttributionConversation.related: list[dict] | None` — `{type, url}`
  baseline vocabulary (RFC #16) for plan / issue / PR links.

**Task, identity, metrics**

- `Task.repository_url: str | None` — canonical remote URL alongside
  `owner/repo` (RFC #22).
- `TraceRecord.generation_index: int` — monotonic generation counter
  per `session_id`. Generations are replacement snapshots, not stitchable
  supersets; later generations may have different redactions, enrichments,
  or security-pipeline output. Consumers resolving "latest" should group
  by `session_id` and take `max(generation_index)`.
- `Metrics.total_cache_read_tokens: int` — session-level aggregate.
- `Metrics.total_cache_creation_tokens: int` — session-level aggregate.

### Changed

- `Attribution.experimental` semantics clarified: now `True` when any
  range is low-confidence or a fallback line resolution was used;
  `False` when every range is high-confidence (hook- or diff-sourced).
  Previously blanket-`True` across `0.1.x` / `0.2.x`.

### Compatibility

- Additive wrt `0.2.x`: traces written against `0.2.x` load cleanly with
  `lifecycle="provisional"`, `generation_index=0`, `git_links=[]`, and
  null-valued new Attribution fields.

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

## [0.1.1] - 2026-03-29

### Changed
- `SCHEMA_VERSION` bumped from `0.1.0` to `0.1.1` (patch release aligned with CLI v0.1.1).
  No model changes; version bump only.

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
