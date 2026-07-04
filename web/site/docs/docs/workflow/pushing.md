# Dataset Publish

`opentraces dataset publish <name>` uploads approved workflow rows and contract
files for a named dataset to its active HuggingFace remote. It never appends to
an existing shard in place.

```bash
opentraces dataset review approve my-dataset --all
opentraces dataset remote create my-dataset owner/team-traces --private
opentraces dataset publish my-dataset --check-only
opentraces dataset publish my-dataset
```

## Options

```bash
opentraces dataset publish my-dataset
opentraces dataset publish my-dataset --to owner/team-dataset
opentraces dataset publish my-dataset --check-only
opentraces dataset publish my-dataset --min-retention 0.5
opentraces dataset publish my-dataset --exclude-state lost --exclude-state never_committed
```

| Flag | Description |
|------|-------------|
| `--to TEXT` | Remote name or `owner/name` override |
| `--check-only` | Run gates and stage without upload |
| `--resume TEXT` | Resume a previous publication run id |
| `--min-retention FLOAT` | Drop rows whose mean patch retention is below the threshold |
| `--exclude-state TEXT` | Drop rows containing a patch with this survival state; repeatable |
| `--json` | Emit structured JSON |

## One Clearance Predicate

Per [ADR-0008](https://github.com/JayFarei/opentraces/blob/main/docs/adr/0008-seal-family-contract.md)
§3, exactly one predicate decides whether a trace's bytes may leave the private
bucket. `bucket sync push`, `dataset publish`, and `capsule share --publish` /
`capsule issue --publish` all evaluate the SAME three-way clearance
(`cleared` / `not_cleared` / `unknown`) instead of each re-implementing their
own lock, and egress is never on by default for any of them. The check is
evaluated against a push-time snapshot, not a check-then-copy race: a publish
run indexes one manifest snapshot up front and every row in that run is
authorized against it, so a trace cannot slip from cleared to not-cleared
mid-run. Absence of a recorded clearance (`unknown`) is never coerced to
"safe to leave", only a positive `cleared` state permits egress. A refusal
moves zero bytes.

## Bucket Sync Is Separate

```bash
opentraces bucket sync push
opentraces bucket sync pull
opentraces bucket sync status
```

Bucket sync moves raw retained evidence. Dataset publish moves approved
projected rows. A private bucket remote can exist even when no dataset has
been published. `opentraces bucket sync push` is the gated egress seal for
the bucket: it recomputes the push-time pushed/withheld partition and, if
any trace is not cleared for sync, REFUSES outright — zero bytes egressed,
non-zero exit — rather than pushing a partial or unscanned bucket. Preview
the partition first with `opentraces bucket sync push --dry-run`, which
reports the same `pushed[]` / `withheld[]` (each withheld entry carries a
`reason`/`sub_reason`) split without touching the remote. Run `opentraces
status` beforehand as the pre-egress safety gate — it is the fleet-wide
scanned/unscanned dashboard, and its "safe to sync" verdict is structurally
impossible to show green while any trace remains unscanned. (`bucket remote
push|pull|diff|status` still work as the old spelling; `bucket sync` is the
current one.)

## Security And Publication Gates

Publication gates operate on dataset rows. If a workflow requires sanitization
or LLM review, it should run those steps before approving rows.

```bash
opentraces security tools list
opentraces security sanitize --tools regex,entropy
opentraces dataset review my-dataset --json
opentraces dataset review approve my-dataset --all
opentraces dataset publish my-dataset --check-only
```

LLM-assisted row review now runs through `dataset review` /
`dataset publish`, not a standalone `setup llm-review` step (that command
still works but is hidden — the canonical surface for clearing rows before
publish is the review/approve/publish lifecycle above).

`dataset publish --check-only` also blocks any row that does not satisfy the
dataset's required security tools (block reason
`required_security_tools_missing`), alongside the existing review,
security-version, and privacy gates. This check is keyed on per-row execution
evidence: each row records the tools that actually ran over it
(`tools_applied`, in row provenance), and the gate blocks the row if that set
does not cover the required tools. So a row appended while a required tool was
disabled stays blocked even if the tool is re-enabled afterward. The dataset's
required tools come from its manifest policy; inspect or adjust them with
`opentraces dataset security <name>`.

Rows without an approval state are filtered out. Gate failures surface in the
CLI output and, in JSON mode, in the publication payload.

## Upload Shape

Each publish creates a new shard:

```text
data/
  rows_20260521T142300Z_a1b2c3d4.jsonl
  rows_20260521T151500Z_e5f6a7b8.jsonl
```

The dataset card and schema contract files are regenerated from the local
models and row manifest.
