# Dataset Consumers

Published datasets are workflow-projected rows on Hugging Face Hub. Use this
path for evaluation jobs, teacher/student training, SFT/RL pipelines,
dashboards, and public or private dataset sharing.

## Load Rows

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

## File-Oriented Access

For published Hugging Face datasets, `hf-mount` can expose shards as files:

```bash
hf-mount start repo datasets/your-org/agent-traces /mnt/traces
ls /mnt/traces/data/
head -n 1 /mnt/traces/data/*.jsonl
hf-mount stop /mnt/traces
```

For private or gated datasets, authenticate with Hugging Face first.

## Resolve Back To Evidence

Rows should carry enough trace references for a consumer to retrieve bucket
evidence when it is allowed:

```bash
opentraces trace get <trace-id> --remote owner/private-bucket --json
opentraces trail track <trace-id> --json
opentraces ctx step <trace-id> <step-index> --json
```

That lookup is separate from dataset loading. A public dataset can reference a
private bucket without exposing the bucket itself.
