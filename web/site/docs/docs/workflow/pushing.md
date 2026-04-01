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
opentraces push --assess
opentraces push --repo user/custom-dataset
```

| Flag | Default | Description |
|------|---------|-------------|
| `--private` | off | Force private visibility |
| `--public` | off | Force public visibility |
| `--publish` | off | Change an existing private dataset to public |
| `--gated` | off | Enable gated access on the dataset |
| `--assess` | off | Run quality assessment after upload and embed scores in dataset card |
| `--repo` | `{username}/opentraces` | Target HF dataset repo |

`--approved-only` is not part of the current CLI. The supported path is `commit -> push`.

## How Upload Works

Each push creates a new JSONL shard. Existing data is never overwritten or appended to.

```text
data/
  traces_20260329T142300Z_a1b2c3d4.jsonl
  traces_20260401T091500Z_e5f6a7b8.jsonl   <- new shard from this push
```

That means:

- Each push is atomic
- No merge conflicts between contributors
- Dataset history grows by shard

## Dataset Card

`push` generates or updates a `README.md` dataset card on every successful upload. The card aggregates statistics across **all** shards in the repo, not just the current batch, so counts are always accurate.

The card records:

- schema version
- trace counts, steps, and tokens
- model and agent distribution
- date range
- average cost and success rate (when available)

A machine-readable JSON block is embedded for programmatic consumers:

```html
<!-- opentraces:stats
{"total_traces":1639,"avg_steps_per_session":42,...}
-->
```

### Quality scorecard (`--assess`)

`opentraces push --assess` runs quality scoring after upload and embeds the results:

- Shields.io badges at the top of the card (overall utility, gate status, per-persona scores)
- A per-persona breakdown table with PASS / WARN / FAIL status per rubric
- A `quality.json` sidecar file for machine consumers

The gate is `PASSING` when overall utility is above the configured threshold (default 60%). Use `--assess` on any push to keep the card up to date.

## Visibility

| Setting | Who Can See | Use Case |
|---------|-------------|----------|
| Private | Only you | Sensitive code or private experiments |
| Public | Anyone | Open-source contributions |
| Gated | Anyone who requests access | Controlled sharing |

## Push Behavior by Mode

In `review` mode, you commit and push manually. In `auto` mode, clean traces are committed and pushed automatically after capture.
