# Pushing

`opentraces push` uploads staged traces to HuggingFace Hub as sharded JSONL files.

## Basic Push

```bash
opentraces push
```

Uploads to `{username}/opentraces` by default. Creates the dataset repo if it doesn't exist.

## Options

```bash
# Only push reviewed/approved traces
opentraces push --approved-only

# Private dataset
opentraces push --private

# Public dataset
opentraces push --public

# Custom repo name
opentraces push --repo user/custom-dataset

# Enable gated access
opentraces push --gated
```

| Flag | Default | Description |
|------|---------|-------------|
| `--approved-only` | off | Only push approved traces |
| `--private` | off | Force private visibility |
| `--public` | off | Force public visibility |
| `--publish` | off | Change existing private dataset to public |
| `--gated` | off | Enable gated access (auto-approve) |
| `--repo` | `{username}/opentraces` | Target HF dataset repo |

## How Upload Works

Each push creates a new JSONL shard:

```
data/
  traces-0001.jsonl
  traces-0002.jsonl   <- new shard from this push
```

Existing data is never overwritten or appended to. This sharded approach means:

- Each push is atomic (succeeds or fails entirely)
- No merge conflicts between contributors
- Dataset grows incrementally

## Dataset Card

A dataset card (`README.md`) is auto-generated on the first push with:

- Schema version
- Security tier used
- Number of traces
- Agent types
- opentraces tag for HF Hub discovery

## Loading Your Dataset

```python
from datasets import load_dataset
ds = load_dataset("your-name/opentraces")
```

## Visibility

| Setting | Who Can See | Use Case |
|---------|-------------|----------|
| Private | Only you | Testing, sensitive data |
| Public | Anyone | Open-source contributions |
| Gated | Anyone who requests access | Controlled sharing |

## Status After Push

```bash
opentraces status
opentraces log
opentraces remote
```

Check push history, trace counts, and the remote dataset URL.
