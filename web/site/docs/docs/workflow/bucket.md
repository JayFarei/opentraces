# Private Bucket

The bucket is the raw capture-time store. It is local-only until you opt into a
private HuggingFace bucket remote.

Buckets are distinct from datasets:

| Layer | Contents | Egress |
|-------|----------|--------|
| Bucket | raw traces, patch history, Trail events, Context Tree events, source events, blobs, manifest | `opentraces bucket remote push` |
| Dataset | workflow-projected rows over one or more traces | `opentraces dataset publish` |

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

## Repair And Prune

```bash
opentraces bucket repair --json
opentraces bucket prune --dry-run --json
opentraces bucket prune --json
```

`bucket repair` re-projects envelopes and the manifest from canonical events
and blobs. `bucket prune` only deletes unreachable blobs and atomic-write temp
files; it never deletes events or `trace.json`.

## Remote Sync

```bash
opentraces setup bucket
opentraces bucket remote status --json
opentraces bucket remote diff --json
opentraces bucket remote push --json
opentraces bucket remote pull --json
```

Sync order is substrate-aware: blobs, then events, then envelopes, then the
manifest. A configured bucket remote does not publish dataset rows.

## Prefetch And Replay

```bash
opentraces bucket prefetch <trace-id> --json
opentraces bucket replay --repo /path/to/git-clone --json
```

`prefetch` warms a cold local bucket from remote before `trace get` or `ctx`
loads blobs. `replay` reconstructs the canonical Trace Trails Git event ref in
a repository from bucket-exported events.
