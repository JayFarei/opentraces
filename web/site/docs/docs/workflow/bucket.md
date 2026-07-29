# Portable Bucket

The bucket is the portable environment for captured trace evidence. It is
local by default, can sync to a private remote, and is self-sufficient enough
for another machine to inspect trace records, replay Trail events, and lazy-load
Context Tree blobs without access to the original workstation.

Buckets are distinct from datasets:

| Layer | Contents | Egress |
|-------|----------|--------|
| Bucket | raw trace envelopes, patch history, Trail events, Context Tree events, source events, blobs, manifest | `opentraces bucket sync push` |
| Dataset | workflow-projected rows over one or more traces | `opentraces dataset publish` |

## Principles

- **Raw evidence stays private first.** Capture writes to the local bucket, not
  to a public dataset.
- **The bucket is replayable.** Trail events and manifests are enough to rebuild
  derived projections and restore the canonical Git event ref.
- **Large evidence is lazy.** Context and raw blobs are content-addressed, so
  readers can inspect the inventory first and fetch only what they need.
- **Datasets are projections.** Publishing a dataset row does not publish the
  bucket unless you separately sync the bucket remote.
- **Egress is a gated seal, reads are pure.** `bucket sync push` is one of the
  few commands in the CLI that writes anywhere outside the local bucket, and
  it refuses outright — zero bytes egressed, non-zero exit — while any trace
  is not cleared for sync. Every inspection command below (`status`, `list`,
  `verify`) is a derive-on-demand read that never persists anything.

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

## Fleet Safety Dashboard

```bash
opentraces status
opentraces status --short
opentraces status --full
opentraces status --project my-repo
opentraces status --json
```

`opentraces status` is the top-level, O(1) answer to "is my private trace
bucket safe to sync?" — scanned/unscanned counts across the whole fleet and a
green "safe to sync" verdict that is structurally impossible while any trace
is still unscanned. `--short` prints a one-line, stable, scriptable summary;
`--full` adds debugger-level detail; `--project` scopes every count and the
verdict to one project slug. The envelope is `opentraces.bucket.status.v1`.

For the lower-level per-bucket health readout that this dashboard's verdict
points back to, use `opentraces bucket status`.

## Inspect

```bash
opentraces bucket status
opentraces bucket status --json
opentraces bucket list --json
opentraces bucket list --unscanned --json
opentraces bucket list --unsynced --unfiltered --security-stale --json
opentraces bucket list --project my-repo --since 2026-06-01 --json
opentraces bucket list --count --json
opentraces bucket verify --sample 100 --json
opentraces bucket verify --full --json
```

`bucket status` is the lower-level, per-bucket health readout (avoids
expensive blob enumeration). `bucket list` is the bounded, paginated,
accelerator-backed per-trace inventory — it reads the security/sync status
accelerator once and applies filters before projection, so it stays fast even
on a large fleet bucket; use `--limit`/`--cursor` to page, `--count` to get
just a match count without materializing rows, and the facet flags
(`--unsynced`, `--unfiltered`, `--security-stale`, `--unscanned`) to slice by
sync/security state. `bucket verify` recomputes blob hashes and checks for
dangling references.

`bucket status` and `bucket list` are side-effect-free reads: they never
write under the bucket. A full rebuild of the manifest and per-trace
envelopes from canonical state is explicit via `bucket repair`.

## Repair And Replay

```bash
opentraces bucket repair --json
opentraces bucket replay --repo /path/to/git-clone --json
```

`bucket repair` re-projects envelopes and the manifest from canonical events
and blobs — the documented crash-recovery primitive, idempotent and safe to
re-run. `bucket replay` reconstructs the canonical Trace Trails Git event ref
in another Git repository from bucket-exported events.

These are the operations that may consume the historical bucket events mirror
as a fallback. Ordinary live session ingest does not use the aggregate mirror
to backfill one trace when its live Trail slice is empty; it preserves the last
authoritative per-trace companion and anchor projection instead.

## Remote Sync

```bash
opentraces bucket connect
opentraces bucket sync status --json
opentraces bucket sync diff --json
opentraces bucket sync push --dry-run --json
opentraces bucket sync push --json
opentraces bucket sync pull --json
```

Sync order is substrate-aware: blobs, then events, then envelopes, then the
manifest. A configured bucket remote does not publish dataset rows.

`bucket sync push` is the gated egress seal: it computes an auditable
`pushed[]`/`withheld[]` partition and refuses — zero bytes egressed,
non-zero exit — if any trace is not cleared for sync. Run `--dry-run` first
to preview the partition without egressing anything; if traces are withheld,
clear them with `opentraces bucket security run --all` and re-check.

`bucket connect` requires authentication: run `opentraces auth login` first,
or it exits with a `run 'opentraces auth login'` hint. The wizard then
prompts for a bucket security policy (recommended / basic / strict / off /
custom) before configuring remote sync.

## Bucket Security Policy

Bucket security protects raw captured evidence before `bucket sync push`. The
policy is a named bundle over the same `cfg.security.<tool>.enabled` flags that
`setup <tool>` and `config set security.<tool>.enabled` flip, scoped to the
bucket.

```bash
opentraces auth login
opentraces bucket connect
opentraces bucket security status
opentraces bucket security policy --policy recommended
opentraces bucket security policy --tool regex --enable
opentraces bucket security policy --tool entropy --disable
opentraces bucket security status --json
```

`bucket security status` is a read-only inspector: it prints the active policy and
enabled tools without writing config. `bucket security policy --policy` applies an
exact bundle and accepts only `off|basic|recommended|strict`. `bucket security
policy --tool ... --enable` or `--tool ... --disable` (repeatable, needs exactly
one of enable/disable) edits one tool at a time. `bucket security run [--all |
--trace <id>]` applies the configured filter to existing records. `bucket security
policy --json` emits
`{status, security:{enabled, tools, scope:"bucket", policy, available_policies}, changes:{enabled,disabled}}`.

Policy bundles:

| Policy | Tools |
|--------|-------|
| `off` | (nothing) |
| `basic` | regex, entropy |
| `recommended` | regex, entropy, business_logic, path_anonymizer, classifier |
| `strict` | regex, entropy, trufflehog, privacy_filter, business_logic, path_anonymizer, classifier |

Bucket security flags are machine-global (the same `cfg.security.<tool>.enabled`
flags capture-time sanitization reads), so applying a policy can turn OFF a tool
you enabled for another purpose; the CLI prints a warning naming any tool it
disables. When `bucket connect` runs non-interactively (for example with `--json`,
in CI, or any non-TTY), it applies the `recommended` policy by default so a
remote-syncing private bucket is never left with zero redaction; pass explicit
`--enable-security-tool` / `--disable-security-tool` flags to override.

## Cleanup

```bash
opentraces bucket reclaim --json
opentraces bucket reclaim --apply --json
```

`bucket reclaim` lists (and, with `--apply`, removes) leaked Trace Trails
cruft under `.git/**/opentraces/` — stray temp files and orphan accelerator
pickles. It is dry-run by default and never touches events or `trace.json`.
Orphan-blob cleanup and single-trace blob warming (the old `bucket prune` /
`bucket prefetch`) are hidden-but-callable advanced ops now superseded by
`verify` / `repair` / `reclaim` for everyday maintenance.
