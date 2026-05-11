# Export

Cross-format export in 0.4 is handled by the schema package directly. The legacy `opentraces export` top-level command has been retired; the underlying serializers (`agent-trace`, `atif`) live in `packages/opentraces-schema/` and are invoked by dataset workflows that need them.

## Agent Trace v0.1.0

Agent Trace serialization follows the Cursor/community [RFCs](https://github.com/cursor/agent-trace). The exporter pulls directly from the fields adopted in schema 0.3.0:

- `Task.repository_url` (RFC #22)
- `TraceRecord.lifecycle` and `git_links[]` (RFC #25, #27)
- `Attribution.revision`, `Attribution.unaccounted_files` (RFC #5, #26)
- `AttributionRange.change_type`, `AttributionRange.original`, `AttributionRange.contributor` (RFC #5, #11)
- `AttributionConversation.ids`, `AttributionConversation.related` (RFC #9, #16)

All Agent Trace `content_hash` values use the `murmur3:<32-hex>` prefix inherited from the opentraces record.

To produce Agent Trace JSONL from a dataset, define a workflow that calls the Agent Trace serializer over reviewed rows, or invoke the serializer directly from a script:

```python
from opentraces_schema import TraceRecord
from opentraces.publish.agent_trace import serialize_record_to_agent_trace

# ...load TraceRecord, then:
agent_trace_payload = serialize_record_to_agent_trace(record)
```

## ATIF

`packages/opentraces-schema/FIELD-MAPPINGS.md` is the source of truth for third-party converters between `TraceRecord` and ATIF. The serializer at `src/opentraces/publish/atif.py` performs the conversion.

A public round-trip ATIF converter is tracked on the roadmap.

## What Is Not Ready Yet

- A full ATIF round-trip converter
- A user-facing `opentraces export` verb (was removed in 0.4)
