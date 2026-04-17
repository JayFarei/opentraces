# publish/

The outbound boundary of opentraces. Everything that projects traces *out* — format serializers and destination publishers — lives here.

This module collapses the former top-level `exporters/` (format projections) and `upload/` (destinations) packages into a single namespace.

## Formats vs destinations

- **Serializers (formats)** transform `TraceRecord` into a wire/file format. They don't care where the bytes go.
  - `atif.py` — ATIF export (standards-aligned superset projection).
  - `agent_trace.py` — Agent Trace v0.1.0 export.
  - `_base.py` — `FormatExporter` protocol.
- **Destinations** take bytes and publish them somewhere.
  - `huggingface/` — HF Hub sharded dataset upload.
    - `upload.py` — `HFUploader` (sharded JSONL, never appends to existing).
    - `dataset_card.py` — README generation with stats.
    - `schema.py` — HF dataset schema declaration.

## Registry

`publish/__init__.py` exposes two lazy registries:

- `SERIALIZERS` — format name → module path (e.g. `"atif" → "opentraces.publish.atif"`).
- `DESTINATIONS` — destination name → module path (e.g. `"huggingface" → "opentraces.publish.huggingface"`).
- `get_serializer(name)` / `get_destination(name)` — `importlib`-backed lazy load.
- `get_exporters()` — legacy exporter-class registry, lazy-registers defaults.

Lazy loading keeps optional deps (e.g. `huggingface_hub`) out of light CLI paths.

## Adding a new serializer

1. Create `publish/<name>.py`.
2. Implement the `FormatExporter` protocol from `_base.py`.
3. Add the module path to `SERIALIZERS` in `publish/__init__.py`.
4. If the serializer should be listed by default in `get_exporters()`, register it in `_register_default_exporters()`.
5. Add tests under `tests/test_exporters_<name>.py`.

## Adding a new destination

1. Create `publish/<dest>/` as a subpackage with `upload.py` + supporting modules.
2. Add the module path to `DESTINATIONS` in `publish/__init__.py`.
3. Hook it into `core/publish_flow.py` (the workflow orchestrator).
4. Add tests.

## See also

- Root `CLAUDE.md` — full project structure.
- `src/opentraces/capture/README.md` — the inbound boundary (symmetric to this one).
