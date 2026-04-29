# Rationale: opentraces-schema 0.4.0

`0.4.0` introduces the dataset/workflow contract used by Plan 057
(Milestone 2 — local HF-shaped datasets and agent-skill workflows) and the
remote/publication scaffolding that Plan 058 (Milestone 3) layers sync on
top of. The bump is strictly additive over `0.3.0`; `TraceRecord` and the
trace-side enrichment models are unchanged.

## Why a single bump for M2 + M3 scaffolding

Plan 057 ships the local dataset loop (manifest, schema ref, workflow ref,
executor config, identity policy, run record, row index, schedule). Plan
058 builds remote binding, publication review, and append-only publish on
top of the same manifest. Splitting the bump (`0.4.0` for M2, `0.5.0` for
M3) would force two near-back-to-back releases that change `DatasetManifest`
in ways that are hard to migrate cleanly. Adding both surfaces in one
additive bump avoids that churn while staying inside the additive contract
in `VERSION-POLICY.md`.

The Plan 058 fields land defaulted (`remotes={}`, `active_remote=None`,
`remote_schema="refuse_if_newer"`,
`publication_policy=DatasetPublicationPolicy()`), so a Plan 057-only
deployment can ignore them entirely. A local dataset that never connects a
remote behaves exactly as Plan 057 specifies.

## Stricter manifest validation

All dataset models declare `model_config = ConfigDict(extra="forbid")`.
A typo such as `exeuctor:` in `manifest.yaml` would otherwise be silently
dropped, leaving the dataset running with default executor settings. Manifests
are user-written control plane; silent acceptance of unknown keys hides
misconfiguration. `DatasetManifest` retains `populate_by_name=True` so the
on-disk `schema:` alias keeps working alongside the Python attribute name
`schema_ref`.

`DatasetSchedule` now refuses `enabled=True` with `every` unset. Without
this, a dataset could be marked "scheduled" with no interval, causing
silent no-ops in the schedule registry.

`DatasetIdentity` already required a non-empty `fields` list when `mode`
was `"fields"`; that validator is unchanged.

## What is *not* in this bump

- No changes to `TraceRecord`, `Step`, `Attribution`, `GitLink`,
  `CandidatePacket`, `TraceMap`, or the trace-side index models.
- No new enum values added to existing Literal types on existing models.
- No renames, removals, or type narrowings on any existing field.

The `0.3.0` → `0.4.0` upgrade is therefore safe under the additive
contract: a `0.3.0` consumer that only reads `TraceRecord` can ignore
`0.4.0` entirely.

## Migration

None required. CLI auto-migration of older shards remains a no-op for
trace data because trace fields did not change.
