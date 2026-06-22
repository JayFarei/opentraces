# Dataset Row Review

Review applies to dataset rows, not to raw bucket traces. A workflow projects
trace evidence into rows; then those rows are approved, rejected, reset, or
published.

```bash
opentraces dataset status my-dataset
opentraces dataset review my-dataset --json
opentraces dataset review approve my-dataset <row-id>
opentraces dataset review reject my-dataset <row-id>
opentraces dataset review reset my-dataset <row-id>
opentraces dataset review approve my-dataset --all
```

The legacy `--web` and `--tui` flags currently return decommission notices.
Use the CLI row review surface until the next dataset-scoped UI lands.

## Row States

| State | Meaning |
|-------|---------|
| `needs_review` | Awaiting a review decision |
| `publishable` | Approved and ready for publish |
| `published` | Uploaded upstream |
| `rejected` | Kept local only |
| `blocked` | Security gate not satisfied; needs action before approval |

## What To Check

- residual secrets or PII that the workflow did not sanitize;
- internal hostnames, repository paths, or customer identifiers;
- rows that are too short, low quality, or unrelated to the dataset objective;
- stale Trace Trail survival state if the row depends on live code evidence;
- context windows that are too broad or too narrow for the training/eval task.

## Security Tools

Security tools are optional and default off. Workflows can run them before rows
reach review:

```bash
printf '%s\n' '{"row": {...}}' \
  | opentraces security sanitize --tools regex,entropy,path_anonymizer
```

Review is still the final human or workflow gate before publication.
