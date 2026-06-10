# Private Bucket Clients

Private bucket clients need retained trace evidence rather than curated row
streams. Typical clients are teammate agents, local dashboards, replay tools,
debugging scripts, and workflow builders that need raw context.

## Read From A Remote Bucket

```bash
opentraces bucket remote pull --json  # sync remote traces before querying
opentraces trace query --cwd --json
opentraces trace get <trace-id> --remote owner/private-bucket --json
opentraces trail blame commit <sha> --json
opentraces ctx tree <trace-id> --json
opentraces bucket prefetch <trace-id> --remote owner/private-bucket
```

`trace query` starts with bounded candidates. `trace get`, `trail`, and `ctx`
then load the precise trace units, Git evidence, and Context Tree nodes the
client needs.

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
opentraces bucket remote pull --json
opentraces bucket prefetch <trace-id> --json
opentraces bucket replay --repo /path/to/git-clone --json
```

Bucket evidence can be large and private. Do not treat a bucket remote as a
public dataset unless you intentionally made that storage public.
