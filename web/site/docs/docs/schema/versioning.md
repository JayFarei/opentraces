# Schema Versioning

The opentraces schema follows semantic versioning. The version lives in `packages/opentraces-schema/src/opentraces_schema/version.py` as the single source of truth.

## Version Policy

| Change Type | Version Bump | Example |
|-------------|--------------|---------|
| New optional field | Minor | Adding `metrics.p95_latency_ms` |
| New optional model | Minor | Adding a `debugging` block |
| Field rename | Major | Renaming `steps` to `turns` |
| Field removal | Major | Removing `metadata` |
| Type change | Major | Changing `success` from boolean to string |
| Bug fix / docs | Patch | Fixing a validation regex |

## Current Version

```text
0.3.0
```

The `0.x` series means breaking changes may still land between minor versions until `1.0.0`.

### 0.3.0 — additive changes

All new fields are optional; pre-0.3.0 traces deserialize unchanged.

- `TraceRecord.lifecycle` — `"provisional"` | `"final"` (RFC #25).
- `TraceRecord.git_links[]` — new `GitLink` model with `vcs_type`, `revision`, `repo_url`, `branch`, `tier`, `commit_reachable`, `content_alive`. Four evidence tiers: `tool_emitted`, `tool_emitted_with_divergence`, `overlapping`, `orphan`.
- `Attribution.revision` — `{vcs_type, revision}` pin (RFC #5/#25).
- `Attribution.unaccounted_files` — files changed at commit time without a tracked Edit/Write source (RFC #26).
- `AttributionRange.change_type` — `"addition"` | `"modification"` | `"deletion"` (RFC #11).
- `AttributionRange.original` — pre-processing snapshot for divergent ranges (RFC #5).
- `AttributionRange.contributor` — per-range contributor override.
- `AttributionConversation.ids` — provider-native conversation IDs (RFC #9).
- `AttributionConversation.related` — baseline related-resource vocabulary (RFC #16).
- `Task.repository_url` — canonical remote URL (RFC #22).
- `TraceRecord.generation_index` — monotonic per-`session_id` generation counter for replacement snapshots (used by `opentraces pull` and supersedes detection).
- `Metrics.total_cache_read_tokens`, `Metrics.total_cache_creation_tokens` — session-level prompt-cache aggregates.
- `AttributionRange.content_hash` format migrated to `murmur3:<32-hex>` (replaces the prior md5-truncated form) for cross-tool line-range matching. The top-level `TraceRecord.content_hash` is unchanged (still SHA-256 hex of the serialized record, used for cross-contributor dedup).

## Version Checks

There is no public migration workflow today. Version checks happen when configs are normalized and when `TraceRecord` JSONL is loaded. A hidden `opentraces migrate` command still exists for diagnostics, but it only reports the current config and schema versions.

## Rationale Documents

Each schema version ships with a rationale document and a changelog entry in the schema package. See [`VERSION-POLICY.md`](https://github.com/JayFarei/opentraces/blob/main/packages/opentraces-schema/VERSION-POLICY.md) for the full versioning policy and [`CHANGELOG.md`](https://github.com/JayFarei/opentraces/blob/main/packages/opentraces-schema/CHANGELOG.md) for the release history.

## Security Pipeline Version

The security pipeline is versioned independently of the schema, under `SECURITY_VERSION` in `src/opentraces/security/version.py`. It is bumped whenever detection logic changes (regex patterns, entropy thresholds, classifier heuristics, anonymization rules).

```text
SECURITY_VERSION = 0.3.0
```

`opentraces doctor` reports the active value alongside the schema version.

## Field Mappings

The repository keeps downstream mapping tables in `packages/opentraces-schema/FIELD-MAPPINGS.md`.
