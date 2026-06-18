# Dataset Rows

A dataset is a local HuggingFace-shaped row store produced by a workflow. It is
not the raw trace bucket.

## Create

```bash
opentraces workflow create my-workflow --template default
opentraces dataset new my-dataset --workflow ./workflows/my-workflow/
```

Skill-episode datasets can start from observed skill usage without writing a
custom workflow:

```bash
opentraces trace skills --json
opentraces dataset new opentraces-episodes --from-skill opentraces
```

Ad-hoc row seeding is available when you already have JSONL:

```bash
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
```

## Run

```bash
opentraces dataset run my-dataset --dry-run --limit 5 --json
opentraces dataset run my-dataset
opentraces dataset run opentraces-episodes --executor script --json
opentraces dataset run my-dataset --scope trace --trace <trace-id>
opentraces dataset run my-dataset --since-last-run
```

`dataset run` invokes the workflow and appends rows locally. It can read from
Trace Index candidates, a project scope, the current working directory, a
specific trace, or the saved skill query from `dataset new --from-skill`.
The `script` executor runs the workflow package's deterministic
`scripts/build_rows.py` without a live agent.

## Dataset Security Policy

Each dataset carries its own resolved security policy in the manifest
(`DatasetManifest.security`). It is seeded from the source workflow's
front-matter `security:` contract at `dataset new --workflow <path>` and pinned
to that workflow's digest (`source_workflow_digest`). The resolved
`enabled_tools` start as the contract's required tools plus its
`default_enabled_tools`, in canonical registry order.

**The reader's security floor is non-overridable.** Every dataset row is
sanitized by at least `regex`, `entropy`, `business_logic`, and
`path_anonymizer` — the `DATASET_ROW_FLOOR` — regardless of what the source
workflow's contract declares. A third-party workflow contract can only *add*
tools to this floor, never narrow below it; the floor runs even when a contract
enables a smaller set and even at `privacy_tier: off` (which therefore no longer
ships rows verbatim). Because the floor is unioned in as the last resolution
step, no policy flag — including an authored `allow_disable_required` +
`--unsafe-override` — can drop a floor tool from what actually runs (an override
on a floor tool is recorded but does not weaken sanitization). Each row records
its author-declared `requested_tools` distinctly from the floor-resolved
`effective_tools`, and `dataset status --json` surfaces `security.reader_floor`
+ `security.floor_satisfied` so the guarantee is inspectable. This enforces the
promise that the redaction rules that run are the consumer's, not the author's.

The policy is per-dataset, not a global config toggle. Toggling a tool on one
dataset never affects another dataset or the bucket egress policy.

```bash
opentraces dataset security my-dataset
opentraces dataset security my-dataset --json
```

`--json` emits the resolved policy under a `security` block: `source`,
`source_workflow_digest`, `required_tools`, `optional_tools`, `enabled_tools`,
`disallowed_tools`, `overrides`, `scope` (always `dataset`),
`required_satisfied`, and `missing_required_tools`.

Toggle an optional tool on a single dataset:

```bash
opentraces dataset security my-dataset --tool business_logic --enable
opentraces dataset security my-dataset --tool path_anonymizer --disable
```

`--tool` is repeatable and requires `--enable` xor `--disable`. Only optional
tools can be toggled this way. A required tool can be disabled only when the
workflow contract sets `allow_disable_required: true` and you pass
`--unsafe-override` (optionally with `--reason "<text>"`); the opt-out is
recorded in the manifest as an override. If the contract forbids it, the command
exits 2.

```bash
opentraces dataset security my-dataset --tool regex --disable --unsafe-override --reason "rows are synthetic fixtures"
```

This is distinct from `opentraces bucket security`, which governs the
machine-wide bucket egress policy over global tool flags. Dataset security
governs what a dataset's rows carry before dataset publication.

## Review States

| State | Meaning |
|-------|---------|
| `inbox` | Row needs review |
| `approved` | Row is publishable |
| `published` | Row was uploaded upstream |
| `rejected` | Row is kept local only |
| `blocked` | Row needs action before approval |

```bash
opentraces dataset status my-dataset --json
opentraces dataset review my-dataset --json
opentraces dataset review approve my-dataset <row-id>
opentraces dataset review reject my-dataset <row-id>
opentraces dataset review reset my-dataset <row-id>
opentraces dataset review approve my-dataset --all
```

## Remotes

```bash
opentraces dataset remote create my-dataset owner/team-traces --private
opentraces dataset remote add my-dataset owner/existing-traces
opentraces dataset remote list my-dataset --verbose
opentraces dataset remote visibility my-dataset owner/team-traces --public
opentraces dataset remote remove my-dataset owner/team-traces
```

Dataset remotes are independent of bucket remotes. A private bucket remote can
hold raw evidence while a dataset remote holds only approved projected rows.

## Schedules

```bash
opentraces dataset schedule add my-dataset --every 1h --approve-new --publish-check-only
opentraces dataset schedule list
opentraces dataset schedule pause my-dataset
opentraces dataset schedule resume my-dataset
opentraces dataset schedule remove my-dataset
```

Schedules rerun workflows over retained evidence. They do not bypass review or
publication gates unless you explicitly pass approval/publish flags.
