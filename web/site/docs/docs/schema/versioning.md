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
0.1.0
```

The `0.x` series means breaking changes may still land between minor versions until `1.0.0`.

## Version Checks

There is no public migration workflow today. Version checks happen when configs are normalized and when `TraceRecord` JSONL is loaded. A hidden `opentraces migrate` command still exists for diagnostics, but it only reports the current config and schema versions.

## Rationale Documents

Each schema version ships with a rationale document and a changelog entry in the schema package. See [`VERSION-POLICY.md`](https://github.com/JayFarei/opentraces/blob/main/packages/opentraces-schema/VERSION-POLICY.md) for the full versioning policy and [`CHANGELOG.md`](https://github.com/JayFarei/opentraces/blob/main/packages/opentraces-schema/CHANGELOG.md) for the release history.

## Field Mappings

The repository keeps downstream mapping tables in `packages/opentraces-schema/FIELD-MAPPINGS.md`.
