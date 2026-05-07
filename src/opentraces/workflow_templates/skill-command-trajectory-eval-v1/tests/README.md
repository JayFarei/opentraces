# Workflow Tests

Smoke test the deterministic builder:

```bash
python ~/.opentraces/workflows/skill-command-trajectory-eval-v1/scripts/build_rows.py \
  --output /tmp/skill-command-trajectory-eval-v1.jsonl \
  --limit 5
```

Then create a workflow-backed dataset and feed generated rows through the
fake headless executor:

```bash
OT_ROWS="$(cat /tmp/skill-command-trajectory-eval-v1.jsonl)" \
OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS="$OT_ROWS" \
opentraces dataset run skill-command-trajectory-eval-v1 \
  --executor claude-code-headless --json
```
