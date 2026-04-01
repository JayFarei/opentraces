# Push

`opentraces push` uploads committed traces to Hugging Face Hub as sharded JSONL files. Only committed traces are uploaded — run `opentraces commit` first if needed.

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

`opentraces push --assess` runs quality scoring after upload and embeds the results in the dataset card. Here's what it looks like on a live dataset:

[![Overall Quality 78.1%](https://img.shields.io/badge/Overall_Quality-78.1%25-ffc107)](https://opentraces.ai) [![Gate FAILING](https://img.shields.io/badge/Gate-FAILING-dc3545)](https://opentraces.ai) ![Conformance 88.4%](https://img.shields.io/badge/Conformance-88.4%25-28a745) ![Training 89.0%](https://img.shields.io/badge/Training-89.0%25-28a745) ![RL 73.4%](https://img.shields.io/badge/RL-73.4%25-ffc107) ![Analytics 55.7%](https://img.shields.io/badge/Analytics-55.7%25-fd7e14) ![Domain 84.1%](https://img.shields.io/badge/Domain-84.1%25-28a745)

The scorecard embeds per-persona scores as shields.io badges, a breakdown table with PASS / WARN / FAIL per rubric, and a `quality.json` sidecar for machine consumers. See [Assess](/docs/workflow/quality) for scoring details.

## Visibility

| Setting | Who Can See | Use Case |
|---------|-------------|----------|
| Private | Only you | Sensitive code or private experiments |
| Public | Anyone | Open-source contributions |
| Gated | Anyone who requests access | Controlled sharing |

## Push Behavior by Mode

In `review` mode, you commit and push manually. In `auto` mode, clean traces are committed and pushed automatically after capture.

## Export

Export to other formats is not part of the public workflow yet. The CLI exposes a hidden stub for future automation:

```bash
opentraces export --format atif  # not yet public
```

The schema package documents ATIF, ADP, and OTel field mappings in `packages/opentraces-schema/FIELD-MAPPINGS.md`. If you need to write a converter now, start from the `TraceRecord` / `Step` model definitions there.
