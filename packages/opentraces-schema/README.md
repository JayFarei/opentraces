# opentraces-schema

Pydantic v2 models for the opentraces.ai JSONL trace format.

## Install

```bash
pip install -e packages/opentraces-schema
```

## Usage

```python
from opentraces_schema import TraceRecord, SCHEMA_VERSION

record = TraceRecord(
    trace_id="abc-123",
    session_id="sess-456",
    agent={"name": "claude-code", "version": "1.0.32"},
)
line = record.to_jsonl_line()
```

## Version

The schema version (`0.3.0`) lives in `src/opentraces_schema/version.py` as the
single source of truth. See [VERSION-POLICY.md](VERSION-POLICY.md) for semver
semantics and the bump checklist.

`0.3.0` adds the `Intent` block (session-level summary) and the commit-correlation
surface: `GitLink` (evidence-graded trace→commit links), `TraceRecord.lifecycle`
(`provisional` / `final`), `TraceRecord.git_links`, `Task.repository_url`,
`Attribution.revision`, `Attribution.unaccounted_files`,
`AttributionRange.original`, `AttributionRange.change_type`,
`AttributionRange.contributor`, and `AttributionConversation.ids` / `.related`.
`AttributionRange.content_hash` now uses the `murmur3:<32-hex>` format for
cross-tool line-range matching (per Agent Trace v0.1.0). The top-level
`TraceRecord.content_hash` remains SHA-256 for cross-contributor dedup at
upload time. All additive; 0.2.x traces load cleanly.

## Schema Rationale

Every version of the schema ships with a rationale document explaining why each
model and field exists, grounded in public standards (ATIF, Agent Trace, ADP, OTel)
and empirical observations from real agent traces.

The current rationale is [RATIONALE-0.3.0.md](RATIONALE-0.3.0.md). Each version
has its own rationale file linked from the [CHANGELOG](CHANGELOG.md).

## Contributing

Schema feedback, questions, and proposals are welcome via
[GitHub Issues](https://github.com/JayFarei/opentraces/issues). When suggesting
a schema change, please include:

- **What** field or model you would add, change, or remove
- **Why** it matters for your use case (training, analytics, attribution, etc.)
- **How** it relates to existing standards (ATIF, Agent Trace, ADP, OTel) if applicable

Breaking changes (field renames, removals, type changes) require a major version bump.
New optional fields and models are minor bumps. See [VERSION-POLICY.md](VERSION-POLICY.md)
for details.

## Documentation

- [CHANGELOG.md](CHANGELOG.md) - What changed in each version
- [VERSION-POLICY.md](VERSION-POLICY.md) - What version numbers mean for a schema package
- [RATIONALE-0.3.0.md](RATIONALE-0.3.0.md) - Design rationale for v0.3.0 (Intent block + commit-linked evidence tiers)
- [RATIONALE-0.2.0.md](RATIONALE-0.2.0.md) - Design rationale for v0.2.0
- [RATIONALE-0.1.0.md](RATIONALE-0.1.0.md) - Design rationale for v0.1.0
