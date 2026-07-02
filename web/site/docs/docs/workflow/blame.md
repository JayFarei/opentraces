# Trace Trails

Trace Trails answer: what did this trace change, and where does that change
live in Git now?

They are the Git evidence layer for traces. The trace says what happened during
the agent session. Trails preserve the patch history of that trace against Git
history: the original patch, the commit anchor, and whether the authored content
still survives at `HEAD`.

## Principles

- **Trace patches are the unit of authorship.** A patch is one tool-produced
  change or hunk, not one whole file and not one final diff.
- **Git anchors are evidence, not guesswork.** The post-commit path matches
  trace patches into commits and records the confidence of that match.
- **Survival can change over time.** A patch may be alive, transformed,
  reverted, moved, partially preserved, or lost as Git history evolves.
- **The log is canonical.** Snapshot refs and CLI projections are rebuildable
  from append-only Trail events.

The canonical substrate is an append-only event log under:

```text
refs/opentraces/local/events/v1
```

Bucket exports mirror that event log in `bucket/events/v1/`, which makes the
bucket portable across machines.

## Mental Model

| Concept | Meaning |
|---------|---------|
| Trace Patch | One tool-produced change/hunk from a trace |
| Git Anchor | Evidence that a Trace Patch landed in a commit |
| Survival State | Current status of that patch at `HEAD` |
| Trail Event | Append-only fact used to rebuild projections |
| `ot://` resource | Stable resolver path for a trace patch, Git anchor, file line, or context node |

Schema `0.7.0` stores compact patch refs in `TraceRecord.patches[]`. Full
patch history and survival observations live in the Trail companion
(`trail.jsonl.gz`) and the Git event log.

## Commands

```bash
opentraces trail blame <sha>
opentraces trail blame commit <sha>
opentraces trail blame commit c:<sha> src/main.py --lines
opentraces trail blame commit t:<trace-id> --include-overlapping
opentraces trail pr render --base main
opentraces trail pr create --base main
opentraces trail pr update --base main
opentraces trail graph
opentraces trail graph --trace <trace-id>
opentraces trail track <trace-id>
opentraces trail track --patch <trace-patch-id>
opentraces trail track --anchor <git-anchor-id>
opentraces trail track --since 12h --json
opentraces trail <trace-id>
opentraces trail <trace-id>:<step>
```

`trail blame` is a group; a bare `trail blame <sha>` dispatches to `trail blame commit` directly, which resolves attribution. `trail pr` — render/create/update — was lifted to a top-level `trail` verb since it is the family's one gated GitHub write, separate from the pure read verbs (`blame` / `track` / `graph`); it consumes branch-context workflow rows and renders a PR body. The old `trail blame pr render|create|update` path stays callable but hidden from `--help`. A bare `trail <trace-id>` (and `trail <trace-id>:<step>`) resolves a per-trace lineage card / step world without naming a subcommand, mirroring `ctx`'s addressing.

## Survival States

| State | Meaning |
|-------|---------|
| `alive_on_path` | Authored content survives at the anchored path |
| `alive_transformed` | Content survives with formatter/refactor divergence |
| `reverted` | The change was explicitly reverted |
| `lost` | The anchored content no longer survives |
| `unknown` | The walker cannot prove current state |
| `alive_moved` | Content survived after a move/rename |
| `partially_preserved` | Some authored lines survive |
| `repaired` | A later change repaired/touched the anchored range |

## Common Reads

```bash
opentraces trail blame commit ac019172
opentraces trail blame commit ac019172 src/auth.py --lines
opentraces trail blame commit t:<trace-id> --include-overlapping
opentraces trail track <trace-id> --json
```

Commit mode answers which traces contributed to a commit. Trace mode answers
which commits carry a trace's output. `trail track` follows one trace, patch, or
anchor through the current Git state.

## PR Consumer

```bash
opentraces trail pr render --base main
opentraces trail pr create --base main
opentraces trail pr update --base main
```

The PR consumer is workflow-based. It runs the bundled
`pr-intent-summary-v1` workflow over the branch context and renders one row per
commit with deterministic trace lineage and intent summaries.
