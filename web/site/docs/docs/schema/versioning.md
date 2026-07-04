# Schema Versioning

The opentraces schema follows semantic versioning with pre-1.0 compatibility
rules. The version lives in
`packages/opentraces-schema/src/opentraces_schema/version.py` as the single
source of truth.

## Version Policy

| Change Type | Version Bump | Example |
|-------------|--------------|---------|
| New optional field | Minor | Adding `Step.context_node_id` |
| New optional model | Minor | Adding `Patch` |
| Field rename | Major | Renaming `steps` to `turns` |
| Field removal before 1.0 | Minor with rationale | Removing `Outcome.patch` in `0.6.0` |
| Type change | Major | Changing `success` from boolean to string |
| Bug fix / docs | Patch | Fixing a validation regex |

## Current Version

```text
0.8.0
```

`0.8.0` is additive: it gives `Environment` a structured home for exact
dependency pins and runtime identity (issue #200/#155 Part A) without changing
the `TraceRecord` wire shape, so existing trace parsers continue to work
unchanged. `0.7.0` introduced the dataset security policy contract (plan 092);
`0.6.0` made `TraceRecord.patches[]` the authoritative output spine and removed
`Outcome.patch`; consumers should join patch ids to the bucket Trail companion
for full diff/history.

### 0.8.0

- `PinRecord` model, a single resolved dependency pin: `name` (required),
  `version`, `hash`, `marker`, `source` (all optional).
- `Interpreter` model, runtime interpreter identity: `name`, `version` (both
  optional).
- `Environment.resolved_dependencies`, `list[PinRecord] | None`, default
  `None`.
- `Environment.interpreter`, `Interpreter | None`, default `None`.
- `Environment.arch`, `Environment.platform`, `Environment.abi_tag`, optional
  strings, default `None`.
- **Honesty boundary**: these fields are a HOME for dependency pins and
  runtime identity, not a resolver. Their presence never raises `env_tier` or
  any capsule trust ordinal; no model in the schema package carries an
  `env_tier` / `verdict_trust` / `oracle_trust` / `diff_trust` / `sandbox_tier`
  field at all, that trust vocabulary lives entirely outside the schema. A
  future resolver (issue #202) is the out-of-train follow-up that fills these
  fields and lifts `env_tier` from `L0`.
- `TraceRecord` wire shape unchanged; `migrate_record` is a transparent no-op
  across the `0.7.0` -> `0.8.0` bump. See
  [`RATIONALE-0.8.0.md`](https://github.com/JayFarei/opentraces/blob/main/packages/opentraces-schema/RATIONALE-0.8.0.md).

### 0.7.0

- `WorkflowSecurityContract` model — a dataset workflow's declared security
  posture (`required_tools`, `optional_tools`, `default_enabled_tools`,
  `disallowed_tools`, `allow_disable_required`).
- `DatasetSecurityPolicy` model — the resolved per-dataset policy stored on the
  manifest, seeded from a workflow contract and pinned to the source workflow
  digest.
- `DatasetSecurityOverride` model — an explicit recorded unsafe opt-out of a
  required security tool.
- `DatasetManifest.security` — additive optional field defaulting to an empty
  policy, so existing manifests load unchanged.
- `SecurityToolName` Literal and `SECURITY_TOOL_ORDER` tuple — the canonical
  security tool vocabulary mirroring the runtime tool registry.
- `TraceRecord` wire shape unchanged; `migrate_record` is a transparent no-op
  across the `0.6.0` -> `0.7.0` bump.

### 0.6.0

- `Patch` model.
- `GitAnchor` model.
- `TraceRecord.patches[]`.
- `Outcome.patch` removed.
- `Outcome.committed`, `Outcome.commit_sha`, and `TraceRecord.git_links` are
  compatibility projections derived from patch anchors.

### 0.5.0

- `Step.context_node_id`.
- `TraceRecord.context_tree_summary`.

### 0.4.0

- Dataset/workflow manifest models.
- Dataset remotes, publication policy, schedules, and row index entries.
- Trace Index, Trace Map, and Candidate Packet contracts.

### 0.3.0

- `TraceRecord.lifecycle`.
- `TraceRecord.git_links[]`.
- `TraceRecord.generation_index`.
- Richer attribution fields.
- `Task.repository_url`.
- Session-level prompt-cache aggregates.

## Version Checks

HF dataset publication derives `dataset_infos.json` from the Pydantic models on
every push. When the remote schema is newer than the local package, publication
fails rather than overwriting the newer contract.

There is no public migration workflow today. A hidden diagnostic migration
surface exists for local debugging, but schema evolution should be handled by
registered migrations in the schema package when a breaking change is needed.

## Security Pipeline Version

The security pipeline is versioned independently under
`src/opentraces/security/version.py`.

```text
SECURITY_VERSION = 0.8.0
```

`opentraces doctor --security` reports the active value and the enabled state
of each optional tool.

## Rationale Documents

Each schema version ships with a rationale document and a changelog entry in
the schema package. See
[`VERSION-POLICY.md`](https://github.com/JayFarei/opentraces/blob/main/packages/opentraces-schema/VERSION-POLICY.md)
and
[`CHANGELOG.md`](https://github.com/JayFarei/opentraces/blob/main/packages/opentraces-schema/CHANGELOG.md).
