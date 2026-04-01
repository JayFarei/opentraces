# Consume

Once traces are pushed to Hugging Face Hub, how you load them depends on what you're building.

## For developers and ML teams

Use the [HuggingFace `datasets` library](https://huggingface.co/docs/datasets/en/loading) when you want structured access, pandas integration, or a PyTorch DataLoader.

=== "pandas"

    ```python
    from datasets import load_dataset

    ds = load_dataset("your-org/agent-traces")
    df = ds["train"].to_pandas()
    ```

=== "PyTorch"

    ```python
    from datasets import load_dataset

    ds = load_dataset("your-org/agent-traces")
    ds["train"].with_format("torch")
    ```

=== "Streaming"

    ```python
    from datasets import load_dataset

    # Stream without downloading the full dataset
    ds = load_dataset("your-org/agent-traces", streaming=True)
    for trace in ds["train"]:
        print(trace["session_id"])
    ```

## For agents

Use [hf-mount](https://github.com/huggingface/hf-mount) to expose the dataset as a virtual filesystem. The dataset becomes a directory of JSONL files — no Python library required, no full download, readable with any file tool call.

```bash
hf-mount your-org/agent-traces /mnt/traces
```

```python
import json
import pathlib

traces = [
    json.loads(line)
    for p in pathlib.Path("/mnt/traces").glob("*.jsonl")
    for line in p.read_text().splitlines()
    if line.strip()
]
```

The mount approach suits agents because it avoids library overhead and works with standard file reads — the same way the agent reads any local path.

## Schema reference

Each JSONL line is a `TraceRecord`. See the [schema overview](/docs/schema/overview) for field definitions, and [outcome & attribution](/docs/schema/outcome-attribution) for the reward signals used in RL workflows.
