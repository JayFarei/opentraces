# Private Bucket Clients

Private bucket clients need retained trace evidence rather than curated row
streams. Typical clients are teammate agents, local dashboards, replay tools,
debugging scripts, and workflow builders that need raw context.

## Read From A Remote Bucket

The bucket is self-sufficient local-or-remote: connect it once with `bucket
connect`, then every read verb below (`bucket sync`, `trace`, `trail`, `ctx`)
works the same whether the evidence is already local or still on the
configured remote.

```bash
opentraces bucket connect --repo owner/private-bucket  # one-time: bind the remote target
opentraces bucket sync pull --json  # sync remote traces before querying
opentraces trace query --cwd --json
opentraces trace get <trace-id> --remote owner/private-bucket --json
opentraces trail blame commit <sha> --json
opentraces ctx <trace-id> --json
```

`trace query` starts with bounded candidates. `trace get`, `trail`, and `ctx`
then load the precise trace units, Git evidence, and Context Tree nodes the
client needs. `opentraces status` is the safety gate to check first, its
green "safe to sync" verdict is structurally impossible while any trace in
the bucket is unscanned.

## Portable Environment Pattern

A bucket is portable because it carries:

- `trace.json`, the compact `TraceRecord` spine;
- Trail event mirrors for Git evidence replay;
- Context Tree companions and content-addressed layer blobs;
- raw/source blobs referenced by the trace;
- a manifest that makes remote sync and lazy fetch deterministic.

That means a client can pull a bucket remote, inspect a trace, and replay Trail
events into another Git clone without the original machine.

```bash
opentraces bucket sync pull --json
opentraces bucket sync pull --eager --json  # eager: fetch every referenced blob upfront, not just envelopes
opentraces bucket replay --repo /path/to/git-clone --json
```

For warming a single trace's blobs ahead of a cold-cache read, the advanced
`opentraces bucket prefetch <trace-id> --remote owner/private-bucket` verb is
still callable (hidden from `--help`); `bucket sync pull --eager` is the
visible, whole-bucket equivalent.

Bucket evidence can be large and private. Do not treat a bucket remote as a
public dataset unless you intentionally made that storage public.
