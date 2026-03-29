# Export

Export support is not part of the public workflow yet.

The current repository keeps field-mapping references in `packages/opentraces-schema/FIELD-MAPPINGS.md`, and the CLI exposes a hidden `opentraces export --format atif` stub for future automation.

## What Is Ready Today

- The schema package documents ATIF, ADP, and OTel mappings
- The CLI can already serialize traces to the opentraces JSONL schema
- The downstream converter tables are the source of truth for third-party exporters

## What Is Not Ready Yet

- A public `opentraces export` workflow
- A public `opentraces import` workflow
- A round-trip converter between opentraces and ATIF

If you need to write a converter now, start with the schema package field mappings and the `TraceRecord` / `Step` model definitions.
