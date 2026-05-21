# Workflow Templates

Workflows are skill-format packages that turn bucket traces into typed row
streams. They are the boundary between raw retained evidence and datasets.

## Manage Workflows

```bash
opentraces workflow templates --json
opentraces workflow create my-workflow --template skill-command-trajectory-eval-v1
opentraces workflow create my-workflow --template default --description "Curate bug fixes"
opentraces workflow list --json
opentraces workflow remove my-workflow --yes
```

Generated workflows live under the local workflows directory and can be bound
to datasets:

```bash
opentraces dataset new my-dataset --workflow ./workflows/my-workflow/
opentraces dataset run my-dataset --dry-run --limit 5
opentraces dataset run my-dataset
```

## Runtime Contract

The script executor runs:

```bash
<workflow.path>/scripts/build_rows.py
```

with:

| Env var | Meaning |
|---------|---------|
| `OT_RUN_PACKET` | JSON packet describing scope, trace candidates, dataset, and workflow metadata |
| `OT_DATASET_OUTPUT` | JSONL path the script must write |

The dataset-free primitive is `execute_workflow(workflow_name, scope,
output_path)`. Dataset runs wrap that primitive with manifest, cursor, review,
and publication state.

## Built-In Templates

| Template | Purpose |
|----------|---------|
| `default` | Minimal scaffold for custom row builders |
| `skill-command-trajectory-eval-v1` | Compact eval rows for command/skill trajectory attribution |
| `pr-intent-summary-v1` | Branch-context rows consumed by `opentraces trail blame pr render/create/update` |

## Security In Workflows

Security tools are optional. A workflow can invoke them explicitly:

```bash
printf '%s\n' '{"row": {...}}' \
  | opentraces security sanitize --tools regex,entropy,path_anonymizer
```

or use the loaded config:

```bash
printf '%s\n' '{"record": {...}}' \
  | opentraces security sanitize --use-config
```

This keeps the dataset contract explicit: the workflow decides what row shape
and what sanitization are required for its training objective.
