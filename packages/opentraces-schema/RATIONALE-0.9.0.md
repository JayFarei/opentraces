# Rationale: schema 0.9.0

## Summary

0.9.0 adds the additive schema home for dataset-run metadata scope facets (issue #212). It is a purely additive MINOR bump: one new optional field on `DatasetCandidateQuery` (`facets`) and one new optional field on `DatasetRunRecord` (`facet_resolution`), both absent/empty by default. Every existing dataset manifest and run record validates unchanged, no migration is registered, and the `TraceRecord` wire shape is untouched entirely -- this bump only touches the local dataset-lifecycle models, not the captured-trace spine.

This bump closes a version-policy gap an external review of PR #218 (seal/w4-metadata-scope-facets) found: the `facets` and `facet_resolution` fields were implemented and exercised by tests before this bump landed, with `SCHEMA_VERSION` left at `0.8.0` in violation of `VERSION-POLICY.md`'s "new optional fields = MINOR bump" rule.

## Why these fields belong in the schema

Issue #212 lets `opentraces dataset run` narrow its candidate set by model / harness name / harness version (`--facet model=anthropic/claude-sonnet-4`, `--facet agent.name=codex-cli`, `--facet agent.version=0.31.0`), composing with the existing `--scope`/`--project`/`--trace` narrowing. Two new serialized facts follow directly from that feature:

- A dataset can PERSIST a facet scope at `dataset new` / schedule time (`DatasetCandidateQuery.facets`), so a scheduled run with no CLI flags still narrows consistently -- the same reason `scope`/`args` are already persisted on the candidate query.
- A run's RESOLVED match set is a fact about that run worth recording alongside its other counters (`DatasetRunRecord.facet_resolution`), so callers can assert exactly which traces a faceted run selected without re-deriving it from the bucket manifest after the fact.

## Design decisions

### `facets: dict[str, str]`, not a new `DatasetScope` variant

The existing `DatasetScope` enum (`all-projects` / `project` / `cwd` / `trace`) names *which traces are addressable at all*; facets are an orthogonal *metadata* refinement layered on top (any scope can additionally be facet-narrowed). A flat `dict[str, str]` mirrors the CLI's own `--facet name=value` syntax and the `TraceFacet` name vocabulary `trace query --facet` already exposes, so the same mental model works in the persisted manifest, the CLI, and the run packet.

### `facet_resolution: dict[str, Any] | None`, not a typed model

The resolution result (`{"facets", "matched_count", "matched"}`) is recorded as an untyped dict rather than a new nested `BaseModel` because its `matched` rows are a lightweight, purely-informational manifest-row projection (`project_slug`/`trace_id`/`agent_name`/`agent_version`/`agent_model`) -- not a new domain concept that other code constructs or validates against. Keeping it a dict avoids a second additive nested model for what is, structurally, "one dict-shaped observation about this run," matching how sibling run-record diagnostics (`artefacts`, `scope`) are already represented.

### Absent by default, not an empty dict

`facet_resolution` defaults to `None` (not `{}`), distinctly signaling "this run carried no facet scope" versus "a facet scope was requested but nothing matched" (which is `matched_count: 0` with a real dict). `facets` on the candidate query defaults to an empty dict (rather than `None`) because it composes with other already-dict-shaped fields (`args`) on the same model and "no facets" is naturally "empty predicate," not "absent predicate" -- there is no meaningful distinction between the two for a request-shaped field the way there is for a response-shaped one.

## Additivity against VERSION-POLICY.md

Per `VERSION-POLICY.md`, a MINOR bump is "new optional fields, new models" and must be strictly additive. This bump is exactly that:

- New optional field on `DatasetCandidateQuery`: `facets` (default empty dict).
- New optional field on `DatasetRunRecord`: `facet_resolution` (default `None`).
- No existing field on either model is renamed, moved, removed, narrowed, or restructured.
- `TraceRecord` and every other captured-trace model are completely untouched.

Because the change is strictly additive, the CLI auto-migration contract is satisfied without a registered migration: a pre-0.9.0 dataset manifest or run record simply reads the new fields back as their defaults.

## Honesty boundary -- a scoping mechanism, not a trust signal

`facet_resolution`'s presence records *what was selected*, not *whether the selection was correctly enforced*. The enforcement itself (rejecting rows a workflow emits outside the resolved match set, and reading a persisted facet scope even with no runtime `--facet` flag) is runner behavior in `core/workflow_runner.py` and `core/datasets.py::append_rows`, not a schema concern -- the schema only holds the recorded facts a run produced, exactly as `DatasetRunRecord`'s other counters (`appended_count`, `duplicate_count`) do.

## Compatibility

- Existing dataset manifests (no `facets`): validate unchanged; `candidate_query.facets` reads back as `{}`.
- Existing run records (no `facet_resolution`): validate unchanged; reads back as `None`.
- New dataset manifests / run records: may carry a persisted facet scope and its resolution; round-trip losslessly.
- HuggingFace: the model-driven `dataset_infos.json` features map is unaffected -- these are local dataset-lifecycle models, never uploaded as part of a published dataset's row schema.
