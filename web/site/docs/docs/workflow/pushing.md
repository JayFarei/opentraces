# Dataset Publish

`opentraces dataset publish <name>` uploads approved workflow rows and contract
files for a named dataset to its active HuggingFace remote. It never appends to
an existing shard in place.

```bash
opentraces dataset review approve my-dataset --all
opentraces dataset remote create my-dataset owner/team-traces --private
opentraces dataset publish my-dataset --check-only
opentraces dataset publish my-dataset
```

## Options

```bash
opentraces dataset publish my-dataset
opentraces dataset publish my-dataset --to owner/team-dataset
opentraces dataset publish my-dataset --check-only
opentraces dataset publish my-dataset --min-retention 0.5
opentraces dataset publish my-dataset --exclude-state lost --exclude-state never_committed
```

| Flag | Description |
|------|-------------|
| `--to TEXT` | Remote name or `owner/name` override |
| `--check-only` | Run gates and stage without upload |
| `--resume TEXT` | Resume a previous publication run id |
| `--min-retention FLOAT` | Drop rows whose mean patch retention is below the threshold |
| `--exclude-state TEXT` | Drop rows containing a patch with this survival state; repeatable |
| `--json` | Emit structured JSON |

## Bucket Sync Is Separate

```bash
opentraces bucket remote push
opentraces bucket remote pull
opentraces bucket remote status
```

Bucket sync moves raw retained evidence. Dataset publish moves approved
projected rows. A private bucket remote can exist even when no dataset has
been published.

## Security And Publication Gates

Publication gates operate on dataset rows. If a workflow requires sanitization
or LLM review, it should run those steps before approving rows.

```bash
opentraces security tools list
opentraces security sanitize --tools regex,entropy
opentraces setup llm-review
opentraces dataset publish my-dataset --check-only
```

`dataset publish --check-only` also blocks any row that does not satisfy the
dataset's required security tools (block reason
`required_security_tools_missing`), alongside the existing review,
security-version, and privacy gates. The dataset's required tools come from its
manifest policy; inspect or adjust them with `opentraces dataset security
<name>`.

Rows without an approval state are filtered out. Gate failures surface in the
CLI output and, in JSON mode, in the publication payload.

## Upload Shape

Each publish creates a new shard:

```text
data/
  rows_20260521T142300Z_a1b2c3d4.jsonl
  rows_20260521T151500Z_e5f6a7b8.jsonl
```

The dataset card and schema contract files are regenerated from the local
models and row manifest.
