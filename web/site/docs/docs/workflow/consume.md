# Consume

How you load traces depends on what you're building.

## Agents

[hf-mount](https://github.com/huggingface/hf-mount) exposes any HuggingFace dataset as a virtual filesystem. The dataset appears as a directory of JSONL files — no library required, no full download. An agent can `ls`, `grep`, and read individual files the same way it would explore any local directory, which makes it well-suited for discovery: browsing shards, sampling traces, or writing code against the data without knowing its structure upfront.

**Install:**

```bash
curl -fsSL https://raw.githubusercontent.com/huggingface/hf-mount/main/install.sh | sh
```

**Mount and explore:**

```bash
hf-mount start repo datasets/your-org/agent-traces /mnt/traces
ls /mnt/traces/data/
# traces_20240101_abc123.jsonl  traces_20240102_def456.jsonl  ...
```

Once mounted, read a single record to understand the schema:

```bash
head -n 1 /mnt/traces/data/traces_20240101_abc123.jsonl | python3 -m json.tool | head -40
```

Which returns a `TraceRecord` — a representative subset of fields looks like:

```json
{
  "schema_version": "0.2.0",
  "trace_id": "tr_01abc...",
  "session_id": "sess_xyz...",
  "execution_context": "devtime",
  "agent": { "name": "claude-code", "model": "anthropic/claude-sonnet-4-20250514" },
  "task": { "description": "Fix failing tests in auth module", "repository": "org/repo" },
  "outcome": { "success": true, "committed": true, "commit_sha": "a1b2c3d" },
  "metrics": { "total_steps": 14, "total_input_tokens": 48200, "estimated_cost_usd": 0.031 },
  "steps": [ "..." ]
}
```

Stream shards line by line — don't slurp whole files into memory:

```python
import json, pathlib

for path in pathlib.Path("/mnt/traces/data").glob("traces_*.jsonl"):
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            outcome = record.get("outcome") or {}
            if outcome.get("success") and record.get("execution_context") == "devtime":
                print(record["trace_id"], record["metrics"]["total_steps"])
```

For private or gated datasets, authenticate first:

```bash
huggingface-cli login
```

**Unmount when done:**

```bash
hf-mount stop /mnt/traces
```

## Developers and ML teams

Use the [HuggingFace `datasets` library](https://huggingface.co/docs/datasets/en/loading) for structured access, pandas, or PyTorch.

=== "pandas"

    ```python
    from datasets import load_dataset

    ds = load_dataset("your-org/agent-traces")
    df = ds["train"].to_pandas()

    # Filter to successful devtime traces — outcome is a dict column, guard for nulls
    good = df[
        df["execution_context"] == "devtime"
    ].copy()
    good = good[good["outcome"].apply(lambda o: bool(o) and o.get("success"))]
    ```

=== "PyTorch"

    ```python
    from datasets import load_dataset

    ds = load_dataset("your-org/agent-traces")
    # Note: nested fields like steps and outcome are not tensors.
    # Extract the scalar signals you need before formatting.
    flat = ds["train"].map(lambda x: {"success": (x["outcome"] or {}).get("success", False)})
    flat.with_format("torch", columns=["success"])
    ```

=== "Streaming"

    ```python
    from datasets import load_dataset

    ds = load_dataset("your-org/agent-traces", streaming=True)
    for trace in ds["train"]:
        print(trace["trace_id"], trace["metrics"]["total_steps"])
    ```

## Choosing an approach

Use hf-mount for free-form exploration or when the consumer reads files with standard tool calls. Use the datasets library for notebooks or training pipelines.

## Schema reference

Each JSONL line is a `TraceRecord`. See the [schema overview](/docs/schema/overview) for field definitions, and [outcome & attribution](/docs/schema/outcome-attribution) for RL reward signals.
