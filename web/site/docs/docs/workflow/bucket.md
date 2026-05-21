# Portable Bucket

The bucket is the portable environment for captured trace evidence. It is
local by default, can sync to a private remote, and is self-sufficient enough
for another machine to inspect trace records, replay Trail events, and lazy-load
Context Tree blobs without access to the original workstation.

Buckets are distinct from datasets:

| Layer | Contents | Egress |
|-------|----------|--------|
| Bucket | raw trace envelopes, patch history, Trail events, Context Tree events, source events, blobs, manifest | `opentraces bucket remote push` |
| Dataset | workflow-projected rows over one or more traces | `opentraces dataset publish` |

## Principles

- **Raw evidence stays private first.** Capture writes to the local bucket, not
  to a public dataset.
- **The bucket is replayable.** Trail events and manifests are enough to rebuild
  derived projections and restore the canonical Git event ref.
- **Large evidence is lazy.** Context and raw blobs are content-addressed, so
  readers can inspect manifests first and fetch only what they need.
- **Datasets are projections.** Publishing a dataset row does not publish the
  bucket unless you separately sync the bucket remote.

## Layout

The bucket lives under `~/.opentraces/bucket/` and is organized around
deterministic, replayable pieces:

```text
bucket/
  traces/v1/<project>/<trace>/
    trace.json
    trace_history/
    trail.jsonl.gz
    context.jsonl.gz
    sources.jsonl.gz
  blobs/v1/<project>/
    context/<hh>/<hash>.json.gz
    raw/<hh>/<hash>.blob
  events/v1/
    batches/<seq>-<batch-id>.jsonl.gz
    index.json
  manifest.json
```

`trace.json` is the `TraceRecord` spine. The companion files carry the large
or evolving evidence needed by Trace Trails, Context Tree, and replay.

## Inspect

```bash
opentraces bucket status
opentraces bucket manifest --json
opentraces bucket verify --sample 100 --json
opentraces bucket verify --full --json
```

`bucket status` avoids expensive blob enumeration. `bucket verify` recomputes
blob hashes and checks for dangling references.

## Repair, Rebuild, And Replay

```bash
opentraces bucket repair --json
opentraces bucket rebuild --json
opentraces bucket rebuild --substrate context-tree --json
opentraces bucket replay --repo /path/to/git-clone --json
```

`bucket repair` re-projects envelopes and the manifest from canonical events
and blobs. `bucket rebuild` refreshes one or all derived substrate projections
from canonical state (`trail`, `traces`, `context-tree`, or `all`). `bucket
replay` reconstructs the canonical Trace Trails Git event ref in another Git
repository from bucket-exported events.

## Remote Sync

```bash
opentraces setup bucket
opentraces bucket remote status --json
opentraces bucket remote diff --json
opentraces bucket remote push --json
opentraces bucket remote pull --json
opentraces bucket prefetch <trace-id> --json
```

Sync order is substrate-aware: blobs, then events, then envelopes, then the
manifest. `prefetch` warms one trace's blobs before `trace get` or `ctx` loads
them. A configured bucket remote does not publish dataset rows.

## Cleanup

```bash
opentraces bucket prune --dry-run --json
opentraces bucket prune --json
```

`bucket prune` only deletes unreachable blobs and atomic-write temp files. It
never deletes events or `trace.json`.
