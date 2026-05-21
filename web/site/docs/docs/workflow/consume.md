# Consume

There are two consumer paths:

1. **Published datasets:** reviewed workflow rows on Hugging Face Hub.
2. **Private bucket evidence:** raw trace envelopes and companions synced to a
   private bucket remote.

## Published Dataset Rows

```python
from datasets import load_dataset

ds = load_dataset("owner/team-traces", split="train")
print(ds[0])
```

For streaming:

```python
from datasets import load_dataset

ds = load_dataset("owner/team-traces", streaming=True)
for row in ds["train"]:
    print(row)
```

Rows are workflow-specific. A command-trajectory eval dataset and a PR intent
summary dataset will not have the same row schema, even if they came from the
same bucket traces.

## Private Bucket Lookup

Use the CLI when the consumer needs raw trace evidence, Git anchors, or Context
Tree nodes:

```bash
opentraces trace get <trace-id> --remote owner/private-bucket --json
opentraces trace query --remote-bucket --cwd --json
opentraces trail blame commit <sha> --json
opentraces ctx tree <trace-id> --json
opentraces bucket prefetch <trace-id> --remote owner/private-bucket
```

Bucket evidence can be large and private. Do not treat a bucket remote as a
public dataset unless you intentionally made that storage public.

## File-Oriented Access

For published Hugging Face datasets, `hf-mount` can expose shards as files:

```bash
hf-mount start repo datasets/your-org/agent-traces /mnt/traces
ls /mnt/traces/data/
head -n 1 /mnt/traces/data/*.jsonl
hf-mount stop /mnt/traces
```

For private/gated datasets, authenticate with Hugging Face first.
