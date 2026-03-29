# Pushing

`opentraces push` uploads committed traces to Hugging Face Hub as sharded JSONL files.

## Current Flow

```bash
opentraces session commit <trace-id>
opentraces commit --all
opentraces push
```

`push` only uploads committed traces. If you have inbox traces that have not been committed yet, run `opentraces commit` first.

## Options

```bash
opentraces push --private
opentraces push --public
opentraces push --publish
opentraces push --gated
opentraces push --repo user/custom-dataset
```

| Flag | Default | Description |
|------|---------|-------------|
| `--private` | off | Force private visibility |
| `--public` | off | Force public visibility |
| `--publish` | off | Change an existing private dataset to public |
| `--gated` | off | Enable gated access on the dataset |
| `--repo` | `{username}/opentraces` | Target HF dataset repo |

`--approved-only` is not part of the current CLI. The supported path is `commit -> push`.

## How Upload Works

Each push creates a new JSONL shard. Existing data is never overwritten or appended to.

```text
data/
  traces-0001.jsonl
  traces-0002.jsonl   <- new shard from this push
```

That means:

- Each push is atomic
- No merge conflicts between contributors
- Dataset history grows by shard

## Dataset Card

`push` generates or updates a `README.md` dataset card on the first successful upload. It records:

- schema version
- trace counts
- model and agent distribution
- date range

## Visibility

| Setting | Who Can See | Use Case |
|---------|-------------|----------|
| Private | Only you | Sensitive code or private experiments |
| Public | Anyone | Open-source contributions |
| Gated | Anyone who requests access | Controlled sharing |

## Push Behavior by Mode

In `review` mode, you commit and push manually. In `auto` mode, clean traces are committed and pushed automatically after capture.
