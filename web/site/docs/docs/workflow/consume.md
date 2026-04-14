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
  "schema_version": "0.3.0",
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
hf auth login
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

## Local lookup: trace → commit, line → trace

Once the post-commit hook is installed (`opentraces setup git`), two local commands close the loop between commits and traces.

### Traces grouped by commit

```bash
opentraces trace list --by-commit
opentraces --json trace list --by-commit
```

Groups every staged/committed/pushed trace by its `git_links[].revision`. Useful for finding every trace that contributed to a specific commit (cherry-picks, squashes, and multi-trace commits all surface here).

### Line-level blame

```bash
opentraces blame src/auth.py:42
opentraces --json blame src/auth.py:42
```

Resolves `path:line` to the trace(s) that authored it, walking `git_links` and attribution ranges locally. Each hit reports the originating `trace_id`, `step`, `revision`, and whether the content is still `alive` at `HEAD`.

## Filtering by evidence tier (training subsets)

Every record carries two orthogonal quality signals added in schema 0.3.0:

- `lifecycle`, `"provisional"` if the trace hasn't been pinned to a commit yet, `"final"` once the post-commit correlator has pinned it.
- `git_links[].tier` — one of `tool_emitted`, `tool_emitted_with_divergence`, `overlapping`, or `orphan`.

For SFT or RL training, start from the strongest tier and widen as needed:

```python
import json, pathlib

WANT = {"tool_emitted"}  # widen to add "tool_emitted_with_divergence", etc.

for path in pathlib.Path("/mnt/traces/data").glob("traces_*.jsonl"):
    for line in open(path):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("lifecycle") != "final":
            continue
        if not any(link.get("tier") in WANT for link in r.get("git_links") or []):
            continue
        yield r
```

Rule of thumb:

- `tool_emitted` — gold standard, safe for SFT and RL reward shaping.
- `tool_emitted_with_divergence` — still high value; pair with `AttributionRange.original` if you need the pre-format bytes the agent actually emitted.
- `overlapping` — weakly linked, prefer for analytics over training.
- `orphan` — keep the trajectory, don't claim authorship.

## Schema reference

Each JSONL line is a `TraceRecord`. See the [schema overview](/docs/schema/overview) for field definitions, and [outcome & attribution](/docs/schema/outcome-attribution) for RL reward signals and the evidence-tier taxonomy.
