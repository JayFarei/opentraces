# Dataset Workflows

Dataset workflows are skill-format packages that turn bucket traces into typed
row streams. They are the boundary between retained evidence and the dataset
you actually want: eval rows, SFT examples, branch summaries, bug capsules, or
custom training objectives.

This is separate from the trace workflow. Capture fills a bucket; dataset
workflows read that bucket and decide what row shape to emit.

## Principles

- **A workflow is purposeful.** It encodes the row schema and training or
  evaluation objective.
- **Discovery stays deterministic.** Workflows use `trace query`, `trace map`,
  `trace slice`, `trail`, and `ctx` commands to find and bound evidence.
- **Rows are projections.** A row may contain summaries, references, scores, or
  a small evidence closure; it is not the raw trace.
- **Security is explicit.** Workflows opt into the security tools they require.

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

## Evidence Inputs

A workflow can use the trace substrates directly:

```bash
opentraces trace query --lex "fix failing test" --cwd --json
opentraces trace map <trace-id> --bursts --json
opentraces trace slice <trace-id> --template bursts --json
opentraces trail track <trace-id> --json
opentraces ctx step <trace-id> 7 --json
opentraces ctx resume <context-node-id> --json
```

Typical row builders do a progressive read: query for candidates, map the
candidate trace, slice the relevant span, then attach Trail and Context evidence
only when the row schema needs it.

## Built-In Templates

| Template | Purpose |
|----------|---------|
| `default` | Minimal scaffold for custom row builders |
| `skill-command-trajectory-eval-v1` | Compact eval rows for command/skill trajectory attribution |
| `pr-intent-summary-v1` | Branch-context rows consumed by `opentraces trail blame pr render/create/update` |

## Everything-Style Workflows

The general pattern is: choose a schema, choose a trace scope, choose the
evidence closure, and emit rows. A workflow can be broad enough to support an
"everything" dataset for one objective, while still keeping the raw bucket
private.

For example, a command-trajectory workflow may include:

- the user intent summary from `trace map --bursts`;
- the bounded step window from `trace slice`;
- patch survival from `trail track`;
- visible context from `ctx step` or `ctx resume`;
- security metadata from an explicit `security sanitize` pass.

## Security Contract

A workflow declares the security posture of the rows it projects in its
`SKILL.md` / `WORKFLOW.md` YAML front matter, under a `security:` block:

```yaml
security:
  required_tools: [regex, entropy]
  optional_tools: [business_logic, path_anonymizer]
  default_enabled_tools: [business_logic]
  disallowed_tools: []
  allow_disable_required: false
```

| Key | Meaning |
|-----|---------|
| `required_tools` | MUST run; cannot be disabled unless the contract allows it |
| `optional_tools` | MAY be toggled per dataset |
| `default_enabled_tools` | On when a dataset is first seeded (subset of required ∪ optional) |
| `disallowed_tools` | Never run |
| `allow_disable_required` | Whether a downstream dataset may disable a required tool at all |

A dataset security contract may only reference the tools that can actually run
over a projected row: `regex`, `entropy`, `privacy_filter`, `business_logic`,
and `path_anonymizer`. The remaining registry tools (`trufflehog`, `llm_pii`,
`capsule_scope`, `classifier`) operate on full `TraceRecord` structure and
cannot sanitize a row dict, so a contract that lists them is rejected at
`opentraces dataset new`. Unknown tool names are also rejected.

When you bind a workflow to a dataset with `opentraces dataset new --workflow
<path>`, this contract seeds the dataset's resolved manifest policy and is
pinned to the workflow digest. After that, the policy is managed per-dataset
with `opentraces dataset security <name>`.

## Security In Workflows

Security tools are optional and default off. A workflow can invoke them
explicitly:

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
and what sanitization are required for its objective.
