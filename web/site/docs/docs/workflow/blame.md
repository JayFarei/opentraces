# Trace Trails

Trace Trails answer: what did this trace change, and where does that change
live in Git now?

The canonical substrate is an append-only event log under:

```text
refs/opentraces/local/events/v1
```

Bucket exports mirror that event log in `bucket/events/v1/`. Snapshot refs and
CLI projections are rebuildable from the canonical events.

## Mental Model

| Concept | Meaning |
|---------|---------|
| Trace Patch | One tool-produced change/hunk from a trace |
| Git Anchor | Evidence that a Trace Patch landed in a commit |
| Survival State | Current status of that patch at `HEAD` |
| Trail Event | Append-only fact used to rebuild projections |
| `ot://` resource | Stable resolver path for a trace patch, Git anchor, file line, or context node |

Schema `0.6.0` stores compact patch refs in `TraceRecord.patches[]`. Full
patch history and survival observations live in the Trail companion
(`trail.jsonl.gz`) and the Git event log.

## Commands

```bash
opentraces trail blame commit <sha>
opentraces trail blame commit c:<sha> src/main.py --lines
opentraces trail blame commit t:<trace-id> --include-overlapping
opentraces trail blame pr render --base main
opentraces trail blame pr create --base main
opentraces trail blame pr update --base main
opentraces trail graph
opentraces trail graph --trace <trace-id>
opentraces trail track <trace-id>
opentraces trail track --patch <trace-patch-id>
opentraces trail track --anchor <git-anchor-id>
opentraces trail track --since 12h --json
```

`trail blame` is a group. `trail blame commit` resolves attribution.
`trail blame pr` consumes branch-context workflow rows and renders a PR body.

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

## Blame A Commit

```bash
opentraces trail blame commit ac019172
opentraces trail blame commit ac019172 src/auth.py --lines
opentraces trail blame commit ac019172 --json
```

Commit mode answers which traces contributed to the commit. `--lines` and
`--entities` are commit-mode only.

## Blame A Trace

```bash
opentraces trail blame commit t:<trace-id>
opentraces trail blame commit t:<trace-id> --include-overlapping
opentraces trail blame commit t:<trace-id> --json
```

Trace mode answers which commits carry the trace's output. Weak overlap links
are hidden unless `--include-overlapping` is passed.

## PR Consumer

```bash
opentraces trail blame pr render --base main
opentraces trail blame pr create --base main
opentraces trail blame pr update --base main
```

The PR consumer is workflow-based. It runs the bundled
`pr-intent-summary-v1` workflow over the branch context and renders one row per
commit with deterministic trace lineage and intent summaries.

## Repair And Replay

Bucket replay reconstructs the canonical Trail event ref in a Git repository:

```bash
opentraces bucket replay --repo /path/to/git-clone --json
```

If projections drift, run:

```bash
opentraces bucket repair --json
opentraces bucket verify --json
```

Substrate/debug trail commands remain callable for scripts, but the public
front door is `trail blame`, `trail graph`, `trail track`, and `bucket replay`.
