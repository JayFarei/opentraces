# Schema Versioning

The opentraces schema follows semantic versioning. The version lives in `packages/opentraces-schema/src/opentraces_schema/version.py` as the single source of truth.

## Version Policy

| Change Type | Version Bump | Example |
|-------------|-------------|---------|
| New optional field | Minor | Adding `metrics.p95_latency_ms` |
| New optional model | Minor | Adding a `debugging` block |
| Field rename | Major | Renaming `steps` to `turns` |
| Field removal | Major | Removing `metadata` |
| Type change | Major | Changing `success` from boolean to string |
| Bug fix / docs | Patch | Fixing a regex in validation |

## Current Version

```
0.1.0
```

The `0.x` series signals that the schema is in active development. Breaking changes may occur between minor versions until `1.0.0`.

## Rationale Documents

Every schema version ships with a rationale document explaining why each model and field exists:

- [RATIONALE-0.1.0.md](https://github.com/opentraces/opentraces/blob/main/packages/opentraces-schema/RATIONALE-0.1.0.md) - Design decisions for v0.1.0

Each new version will have its own rationale file linked from the [CHANGELOG](https://github.com/opentraces/opentraces/blob/main/packages/opentraces-schema/CHANGELOG.md).

## Migration

When the schema version changes, run:

```bash
opentraces migrate
```

This checks staged traces against the current schema version and applies any necessary transformations.

## Field Mappings

The [FIELD-MAPPINGS.md](https://github.com/opentraces/opentraces/blob/main/packages/opentraces-schema/FIELD-MAPPINGS.md) document tracks how opentraces fields map to ATIF, ADP, OTel, and Agent Trace fields.
