# opentraces-schema

Pydantic v2 models for the opentraces.ai JSONL trace format.

## Install

```bash
pip install opentraces-schema
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

The schema version (`0.7.0`) lives in `src/opentraces_schema/version.py` as the
single source of truth. See [VERSION-POLICY.md](VERSION-POLICY.md) for semver
semantics and the bump checklist.

`0.7.0` adds the additive dataset security policy contract
(`WorkflowSecurityContract`, `DatasetSecurityPolicy`, `DatasetManifest.security`);
the `TraceRecord` wire shape is unchanged from `0.6.0`.

`0.6.0` makes `TraceRecord.patches[]` the authoritative output spine for
dev-time traces and removes the legacy unified-diff `Outcome.patch` field.
`Outcome.committed`, `Outcome.commit_sha`, and `TraceRecord.git_links` remain
reader-compatible projections derived from patch anchors. Full patch, trail,
context, and source evidence lives in the private bucket companions
(`trail.jsonl.gz`, `context.jsonl.gz`, `sources.jsonl.gz`) rather than being
embedded into the JSONL row.

`0.5.0` added Context Tree cross-reference fields:
`Step.context_node_id` and `TraceRecord.context_tree_summary`.

`0.4.0` introduced the local dataset/workflow contract used by the CLI's
`dataset` and `workflow` commands plus the trace-index contract used by
`trace query`, `trace map`, and `trace slice`.

`0.3.0` added the commit-correlation surface: `GitLink`,
`TraceRecord.lifecycle`, `TraceRecord.generation_index`,
`TraceRecord.git_links`, `Task.repository_url`,
`Attribution.revision`, `Attribution.unaccounted_files`,
`AttributionRange.original`, `AttributionRange.change_type`,
`AttributionRange.contributor`, and `AttributionConversation.ids` /
`.related`.

## Schema Rationale

Every version of the schema ships with a rationale document explaining why each
model and field exists, grounded in public standards (ATIF, Agent Trace, ADP, OTel)
and empirical observations from real agent traces.

The current rationale is [RATIONALE-0.6.0.md](RATIONALE-0.6.0.md). Each version
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
- [FIELD-MAPPINGS.md](FIELD-MAPPINGS.md) - Field maps to ATIF, ADP, and OTel GenAI
- [RATIONALE-0.6.0.md](RATIONALE-0.6.0.md) - Current rationale for v0.6.0
- [RATIONALE-0.5.0.md](RATIONALE-0.5.0.md) - Context Tree cross-reference rationale
- [RATIONALE-0.4.0.md](RATIONALE-0.4.0.md) - Dataset/workflow and trace-index rationale
- [RATIONALE-0.3.0.md](RATIONALE-0.3.0.md) - Rationale for v0.3.0
- [RATIONALE-0.2.0.md](RATIONALE-0.2.0.md) - Design rationale for v0.2.0
- [RATIONALE-0.1.0.md](RATIONALE-0.1.0.md) - Design rationale for v0.1.0
