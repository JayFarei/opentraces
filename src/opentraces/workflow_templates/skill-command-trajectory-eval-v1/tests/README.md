# Workflow Tests

Smoke test the deterministic builder:

```bash
python ~/.opentraces/workflows/skill-command-trajectory-eval-v1/scripts/build_rows.py \
  --output /tmp/skill-command-trajectory-eval-v1.jsonl \
  --limit 5
```

Then create a workflow-backed dataset and run it through the deterministic
`script` executor (it subprocess-runs the workflow's `scripts/build_rows.py`):

```bash
opentraces dataset run skill-command-trajectory-eval-v1 \
  --executor script --json
```
