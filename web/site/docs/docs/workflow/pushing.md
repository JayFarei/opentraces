# Push

`opentraces push` uploads staged traces to Hugging Face Hub as a new JSONL shard. It never appends to an existing shard in place.

If nothing is staged yet, review first and run:

```bash
opentraces add --all
```

## Options

```bash
opentraces push
opentraces push --private
opentraces push --public
opentraces push --publish
opentraces push --gated
opentraces push --no-assess
opentraces push --repo user/custom-dataset
opentraces push --llm-review
opentraces push --no-trufflehog
```

| Flag | Default | Description |
|------|---------|-------------|
| `--private` | off | Force private visibility |
| `--public` | off | Force public visibility |
| `--publish` | off | Change an existing private dataset to public |
| `--gated` | off | Enable gated access on the dataset |
| `--assess / --no-assess` | on | Run quality scoring and include badges in the dataset card |
| `--llm-review` | off | Require a clean Tier 2 LLM verdict on every staged trace before upload |
| `--no-trufflehog` | off | One-shot override: skip Tier 1.5 TruffleHog for this push only |
| `--repo` | `{username}/opentraces` | Target HF dataset repo |
| `--migrate-remote / --no-migrate-remote` | prompt | Auto-migrate older schema shards on the remote |
| `-y, --yes` | off | Skip interactive prompts, including migration confirmation |

`--approved-only` is not part of the 0.3 CLI.

## Security Gates

Two optional gates can run at push time:

- `--llm-review` blocks the upload unless every staged trace carries a clean completed Tier 2 verdict in `metadata.llm_review`.
- `--no-trufflehog` is a one-shot escape hatch for projects where Tier 1.5 TruffleHog is enabled in config but you want to skip it just for this push. It does not change the persisted config.

When `--llm-review` aborts, the CLI exits `3` and prints `opentraces llm-review` as the hint.

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

Quality scoring is enabled by default during push. The resulting scorecard is embedded into the dataset card with badges, a persona breakdown, and a `quality.json` sidecar.

Use `--no-assess` if you want to skip that pass for a particular upload.

Here's what the scorecard looks like on a live dataset:

[![Overall Quality 78.1%](https://img.shields.io/badge/Overall_Quality-78.1%25-ffc107)](https://opentraces.ai) [![Gate FAILING](https://img.shields.io/badge/Gate-FAILING-dc3545)](https://opentraces.ai) ![Conformance 88.4%](https://img.shields.io/badge/Conformance-88.4%25-28a745) ![Training 89.0%](https://img.shields.io/badge/Training-89.0%25-28a745) ![RL 73.4%](https://img.shields.io/badge/RL-73.4%25-ffc107) ![Analytics 55.7%](https://img.shields.io/badge/Analytics-55.7%25-fd7e14) ![Domain 84.1%](https://img.shields.io/badge/Domain-84.1%25-28a745)

The scorecard embeds per-persona scores as shields.io badges, a breakdown table with PASS / WARN / FAIL per rubric, and a `quality.json` sidecar for machine consumers. See [Assess](/docs/workflow/quality) for scoring details.

## Visibility

| Setting | Who Can See | Use Case |
|---------|-------------|----------|
| Private | Only you | Sensitive code or private experiments |
| Public | Anyone | Open-source contributions |
| Gated | Anyone who requests access | Controlled sharing |

## Push Behavior by Mode

In `review` mode, every trace waits in Inbox until a human stages it.

In `auto` mode, clean traces are auto-approved into the `staged` set. Push is still explicit.

## Remotes

Use `opentraces remote` to manage which Hugging Face dataset this repo pushes to:

```bash
opentraces remote list
opentraces remote add owner/dataset
opentraces remote create owner/team-traces --private
opentraces remote visibility owner/dataset --public
opentraces remote remove owner/dataset
```

`push --repo owner/dataset` is a one-shot override for the destination. The project's active remote remains unchanged unless you update it through `opentraces remote`.

## Export

Export is now part of the public CLI for staged traces:

```bash
opentraces export --format agent-trace
opentraces export --format atif
```

`agent-trace` emits Agent Trace JSONL. `atif` is present but still a lighter path. Start from the schema docs if you need a custom converter.
