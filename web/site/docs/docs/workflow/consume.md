# Consume

Once traces are on Hugging Face Hub, you can read them back as files or through the `datasets` library.

## File-Oriented Access

[hf-mount](https://github.com/huggingface/hf-mount) exposes a dataset as a virtual filesystem. That works well for agents that prefer normal file operations.

Install:

```bash
curl -fsSL https://raw.githubusercontent.com/huggingface/hf-mount/main/install.sh | sh
```

Mount and inspect:

```bash
hf-mount start repo datasets/your-org/agent-traces /mnt/traces
ls /mnt/traces/data/
head -n 1 /mnt/traces/data/traces_*.jsonl
```

For private or gated datasets, authenticate first:

```bash
hf auth login
```

Unmount when done:

```bash
hf-mount stop /mnt/traces
```

## Structured Access

Use Hugging Face `datasets` for notebooks, analysis, or training pipelines.

```python
from datasets import load_dataset

ds = load_dataset("your-org/agent-traces")
print(ds["train"][0]["trace_id"])
```

For streaming:

```python
from datasets import load_dataset

ds = load_dataset("your-org/agent-traces", streaming=True)
for trace in ds["train"]:
    print(trace["trace_id"])
```

## Record Shape

Each JSONL line is a `TraceRecord`. A representative subset looks like:

```json
{
  "schema_version": "0.4.0",
  "trace_id": "tr_01abc...",
  "agent": {
    "name": "claude-code",
    "model": "..."
  },
  "task": {
    "description": "Fix failing tests in auth module"
  },
  "metrics": {
    "total_steps": 14,
    "estimated_cost_usd": 0.031
  },
  "steps": ["..."]
}
```

See the [schema overview](/docs/schema/overview) for the full contract.

## Local Lookup: Traces, Commits, And Lines

Once you install the git correlator with `opentraces setup git`, local commands can resolve code history back to traces.

### Search retained traces

```bash
opentraces trace query --since 7d
opentraces trace query --cwd --candidate-kind bug_fix
opentraces --json trace query --files "src/**/*.py"
```

### Resolve a commit back to traces

```bash
opentraces trail blame abc1234
opentraces trail blame abc1234 src/auth.py
opentraces trail blame abc1234 src/auth.py --lines
opentraces --json trail blame abc1234
```

`trail blame` takes a commit SHA (bare or `c:<sha>`) and an optional path to scope output to one file. Use `--lines` for git-blame-style per-line output. This is useful for provenance, code archaeology, and dataset filtering by evidence quality.

## Choosing An Access Pattern

- Use `hf-mount` when the consumer wants to browse files or let an agent inspect shards directly
- Use `datasets` for notebooks, analysis jobs, and training pipelines
- Use local `trace query` and `trail blame` for repo-specific provenance work
