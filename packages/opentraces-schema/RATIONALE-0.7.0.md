# Rationale: schema 0.7.0

## Summary

0.7.0 adds the dataset security policy contract (plan 092 Track 2). It is a
purely additive MINOR bump: new optional dataset-control models plus one new
optional field on `DatasetManifest`. The published `TraceRecord` wire shape is
unchanged.

## Why dataset security belongs in the schema

Datasets are workflow-driven row projections (plan 057). The security posture of
a dataset's published rows is therefore a property of the *workflow* that
projects them and the *dataset manifest* that pins that workflow, not of
machine-global config. Encoding the contract in the schema lets every surface
(CLI, run packet, publication gate, remotes) agree on one validated shape.

Two models capture the two layers:

- `WorkflowSecurityContract` is what a workflow author declares in front matter:
  which tools are `required` (must always run), `optional` (may be toggled per
  dataset), `default_enabled` (on when a dataset is first seeded), and
  `disallowed` (never run), plus `allow_disable_required`.
- `DatasetSecurityPolicy` is the resolved policy stored on the manifest. It is
  seeded from the contract via `DatasetSecurityPolicy.from_contract(...)`,
  pinned to `source_workflow_digest`, and edited only through
  `dataset security <name>`. Disabling a required tool requires recording an
  explicit `DatasetSecurityOverride`.

## Validation guarantees

`DatasetSecurityPolicy` enforces, at construction time:

- `enabled_tools` is a subset of `required_tools ∪ optional_tools`.
- `disallowed_tools` are never enabled.
- the required-tools subset invariant: every required tool is enabled unless an
  explicit override records the unsafe opt-out.
- `overrides` may only target required tools.

`WorkflowSecurityContract` rejects incoherent contracts (a tool both required
and disallowed, a default-enabled tool outside the declared set, etc.). Tool
names are constrained by the `SecurityToolName` Literal, so unknown tools are
rejected by the model rather than slipping through.

## Why additive / MINOR

Per VERSION-POLICY.md, new optional fields and new models are a MINOR bump.
`DatasetManifest.security` defaults to an empty `DatasetSecurityPolicy`, so
manifests written by 0.6.0 clients load unchanged. No `TraceRecord` field
changed, so `migrate_record` is a transparent no-op across `0.6.0 -> 0.7.0`; the
existing 0.5→0.6 patch reconstruction is the only registered transform.

## Tool vocabulary sync

`SECURITY_TOOL_ORDER` / `SecurityToolName` mirror the runtime tool registry
(`opentraces.security.tools._registry`). The schema package stays standalone
(no runtime import); a test in the CLI repo asserts the two stay in sync so the
vocabulary cannot drift silently.
