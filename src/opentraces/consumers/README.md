# consumers/

Workflow **consumers**. A consumer reads the typed JSONL row stream a workflow
projects from bucket traces and renders or acts on exactly one destination.

## The contract (`contract.py`)

```text
CLI verb (unchanged: `trail blame pr`, `workflow optimize`, ...)
  -> consumer builds a scope dict   {"kind": "...", ...consumer fields}
  -> run_workflow_rows(workflow_name, scope=..., output_path=...)
       - ensures the bundled workflow is installed (workflow_templates/<name>)
       - execute_workflow() subprocess-runs scripts/build_rows.py
       - parses JSONL rows
  -> consumer reads rows -> ConsumerArtifact (path/text/metadata)
  -> CLI renders the artifact / performs the destination side effect
```

Rules:

- **Workflows never import consumers.** Row generation stays a pure projection;
  rendering and destination logic live here.
- **Consumers own** scope construction, output/cache paths, row parsing, and the
  destination side effect.
- **Bundled workflow packages stay in `opentraces.workflow_templates/`** (data
  dirs shipped in the wheel, discovered via `importlib.resources`). They are not
  moved under `consumers/`; only their Python consumer code lives here.
- **CLI surfaces do not move.** `consumers/` is an internal boundary, not a new
  `opentraces consumer ...` command group.

## Consumers

| Package | Workflow | Destination | CLI verb |
|---------|----------|-------------|----------|
| `skill_opt/` | `skill-opt-v1` | optimized `best_skill.md` + audit | `workflow optimize` |
| `branch_pr/` | `pr-intent-summary-v1` | GitHub PR body | `trail blame pr` |

`skill_opt/` is the reference implementation of the contract: `engine.py` (the
SkillOpt edit-engine), `proposers.py` (edit proposers), `runner.py` (the
`run` entrypoint that wires workflow rows to the optimizer and exports/promotes
the skill).
