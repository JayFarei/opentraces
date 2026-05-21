# Dataset Rows

A dataset is a local HuggingFace-shaped row store produced by a workflow. It is
not the raw trace bucket.

## Create

```bash
opentraces workflow create my-workflow --template default
opentraces dataset new my-dataset --workflow ./workflows/my-workflow/
```

Ad-hoc row seeding is available when you already have JSONL:

```bash
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
```

## Run

```bash
opentraces dataset run my-dataset --dry-run --limit 5 --json
opentraces dataset run my-dataset
opentraces dataset run my-dataset --scope trace --trace <trace-id>
opentraces dataset run my-dataset --since-last-run
```

`dataset run` invokes the workflow and appends rows locally. It can read from
Trace Index candidates, a project scope, the current working directory, or a
specific trace.

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
