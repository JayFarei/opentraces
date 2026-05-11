# Publish

In 0.4 publication is dataset-scoped. `opentraces dataset publish <name>` uploads reviewed rows and contract files for a named dataset to its active HuggingFace remote as a new JSONL shard. It never appends to an existing shard in place.

If nothing is approved yet, review first:

```bash
opentraces dataset review my-dataset approve --all
```

## Options

```bash
opentraces dataset publish my-dataset
opentraces dataset publish my-dataset --to owner/team-dataset
opentraces dataset publish my-dataset --check-only
opentraces dataset publish my-dataset --min-retention 0.5
opentraces dataset publish my-dataset --exclude-state lost --exclude-state never_committed
```

| Flag | Default | Description |
|------|---------|-------------|
| `--to TEXT` | bound remote | Remote name or `owner/name` override |
| `--check-only` | off | Run all gates and stage without uploading |
| `--resume TEXT` | off | Resume a previous publication run id |
| `--min-retention FLOAT` | off | Drop rows whose mean `retention_fraction` across `patches_with_survival` is below this threshold (0.0-1.0) |
| `--exclude-state TEXT` | off | Drop rows that have any patch with this `survival_state`. Repeatable |
| `--json` | off | Emit structured JSON |

Under `--check-only` the drop counts surface in the JSON `publish.filter` block without uploading.

## Security Gates

Two optional gates can run at publish time:

- **Tier 1.5 TruffleHog** runs automatically when enabled via `opentraces setup trufflehog`. Findings are redacted in place and force review before the row can be approved.
- **Tier 2 LLM review** runs out-of-band via `opentraces setup llm-review` and the dataset workflow. Approved rows carry a clean verdict; rows without one are not eligible for publication when the dataset's publication policy requires it.

When a gate aborts, the CLI exits `3` and prints a remediation hint.

## How Upload Works

Each publish creates a new JSONL shard. Existing data is never overwritten or appended to.

```text
data/
  rows_20260329T142300Z_a1b2c3d4.jsonl
  rows_20260401T091500Z_e5f6a7b8.jsonl   <- new shard from this publish
```

That means:

- Each publish is atomic
- No merge conflicts between contributors
- Dataset history grows by shard

## Dataset Card

`dataset publish` generates or updates a `README.md` dataset card on every successful upload. The card aggregates statistics across **all** shards in the repo, not just the current batch, so counts are always accurate.

The card records:

- schema version
- row counts, steps, and tokens
- model and agent distribution
- date range
- average cost and success rate (when available)

A machine-readable JSON block is embedded for programmatic consumers:

```html
<!-- opentraces:stats
{"total_rows":1639,"avg_steps_per_session":42,...}
-->
```

### Quality scorecard

Quality scoring is part of the workflow. The resulting scorecard is embedded into the dataset card with badges, a persona breakdown, and a `quality.json` sidecar.

Here's what the scorecard looks like on a live dataset:

[![Overall Quality 78.1%](https://img.shields.io/badge/Overall_Quality-78.1%25-ffc107)](https://opentraces.ai) [![Gate FAILING](https://img.shields.io/badge/Gate-FAILING-dc3545)](https://opentraces.ai) ![Conformance 88.4%](https://img.shields.io/badge/Conformance-88.4%25-28a745) ![Training 89.0%](https://img.shields.io/badge/Training-89.0%25-28a745) ![RL 73.4%](https://img.shields.io/badge/RL-73.4%25-ffc107) ![Analytics 55.7%](https://img.shields.io/badge/Analytics-55.7%25-fd7e14) ![Domain 84.1%](https://img.shields.io/badge/Domain-84.1%25-28a745)

The scorecard embeds per-persona scores as shields.io badges, a breakdown table with PASS / WARN / FAIL per rubric, and a `quality.json` sidecar for machine consumers.

## Visibility

| Setting | Who Can See | Use Case |
|---------|-------------|----------|
| Private | Only you | Sensitive code or private experiments |
| Public | Anyone | Open-source contributions |
| Gated | Anyone who requests access | Controlled sharing |

Set visibility via `opentraces dataset remote create <name> <repo> --private/--public` or change after the fact with `opentraces dataset remote visibility`.

## Publish Behavior by Mode

In `review` mode, every trace waits in Inbox until a human approves it (capture-time policy, set with `opentraces config set review_policy review --project`).

In `auto` mode, clean traces are auto-approved at capture time. Dataset publication is still explicit.

## Dataset Remotes

Use `opentraces dataset remote` to manage which HuggingFace datasets a local dataset publishes to:

```bash
opentraces dataset remote list my-dataset
opentraces dataset remote add my-dataset owner/dataset
opentraces dataset remote create my-dataset owner/team-traces --private
opentraces dataset remote visibility my-dataset owner/dataset --public
opentraces dataset remote remove my-dataset owner/dataset
```

`dataset publish --to owner/dataset` is a one-shot override for the destination. The dataset's bound remotes remain unchanged unless you update them through `dataset remote`.

## Bucket Sync vs Dataset Publish

The local trace bucket (your private workspace state, retained traces, Trace Trails, attribution cache) syncs separately from dataset publication:

```bash
opentraces bucket remote push
opentraces bucket remote pull
opentraces bucket remote status
```

Configure the bucket remote up front with `opentraces setup bucket`. Dataset publication is independent: a published dataset is the curated output, while the bucket is the working substrate.
