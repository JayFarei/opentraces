# Export

`opentraces export` serializes the locally staged traces into another interchange format. It reads from the current project's machine-local trace store and writes a single JSONL file.

```bash
opentraces export --format agent-trace
opentraces export --format agent-trace --output ./my-export.jsonl
opentraces export --format atif
```

| Flag | Default | Description |
|------|---------|-------------|
| `--format` | required | `atif` or `agent-trace`. |
| `--output` | `./opentraces-export.jsonl` | Destination JSONL path. |

## Agent Trace v0.1.0

`--format agent-trace` emits Agent Trace v0.1.0 JSONL, following the Cursor/community [RFCs](https://github.com/cursor/agent-trace). The exporter pulls directly from the fields adopted in schema 0.3.0:

- `Task.repository_url` (RFC #22)
- `TraceRecord.lifecycle` and `git_links[]` (RFC #25, #27)
- `Attribution.revision`, `Attribution.unaccounted_files` (RFC #5, #26)
- `AttributionRange.change_type`, `AttributionRange.original`, `AttributionRange.contributor` (RFC #5, #11)
- `AttributionConversation.ids`, `AttributionConversation.related` (RFC #9, #16)

All Agent Trace `content_hash` values use the `murmur3:<32-hex>` prefix inherited from the opentraces record.

Output is one Agent Trace record per line:

```bash
head -n 1 opentraces-export.jsonl | python3 -m json.tool | head -40
```

## ATIF

`--format atif` is present but still the lighter path. The schema package keeps the mapping tables in `packages/opentraces-schema/FIELD-MAPPINGS.md` as the source of truth for third-party converters.

A public round-trip ATIF converter is tracked on the roadmap.

## What Is Not Ready Yet

- A public `opentraces import` workflow (use `opentraces pull` for HuggingFace sources)
- A full ATIF exporter
- A round-trip converter between opentraces and ATIF
