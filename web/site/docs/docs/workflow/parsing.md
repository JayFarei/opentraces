# Capture

Capture turns agent sessions into retained trace evidence. The retained output
lands in the private bucket first; dataset rows are produced later by
workflows.

## Capture Path

When capture hooks fire, opentraces:

1. discovers the agent session artifact;
2. parses it into a `TraceRecord` spine;
3. extracts tool calls, observations, steps, metrics, and environment data;
4. records Trace Patch and Context Tree events where available;
5. writes the trace envelope and companion files into the private bucket;
6. updates search projections used by `trace query`, `trace skills`,
   `trace map`, and `trace slice`.

The raw bucket is local-only unless you run `opentraces setup bucket` and sync
with `opentraces bucket remote push`.

## Supported Live Sources

```bash
opentraces setup claude-code
opentraces setup codex-cli
opentraces setup pi
opentraces init --agent claude-code
opentraces init --agent codex-cli
opentraces init --agent pi
```

Claude Code, Codex CLI, and Pi are live capture sources. Hermes remains an
import adapter consumed by workflows/schema tooling, not a direct public capture
command.

Codex CLI capture requires both machine setup and repo enrollment:

```bash
opentraces setup codex-cli
opentraces init --agent codex-cli
```

`setup codex-cli` registers native Codex hook commands in
`~/.codex/hooks.json` and copies hook scripts under
`~/.codex/hooks/opentraces/`. The hooks write sidecar JSONL under
`.opentraces/codex-cli/hooks/` in the active repo and trigger a bounded ingest
on Stop. They are passive observers; permission requests are recorded but not
approved or denied.

Pi capture is extension-backed: `pi install npm:opentraces-pi` loads the package,
`opentraces init --agent pi` enrolls the repo, and native sessions live under
`~/.pi/agent/sessions/--<cwd>--/*.jsonl`. Sidecars land under
`.opentraces/pi/events/`. Provider/context sidecars use `capture_method =
live_capture` when available; transcript fallback records explicit limitations.
The exported TraceRecord follows the active branch in v1; inactive branch/tree
transition details remain in `metadata.pi` and Context Tree events. Raw provider
bodies are default-off and local/security-gated; opt in with
`OPENTRACES_PI_RETAIN_RAW_PROVIDER_BODIES=1` only when the retained local blobs
are acceptable.

Codex Context Tree capture is reconstructed from rollout JSONL plus hook
sidecars with `capture_method = transcript_reconstruction`. It produces useful
session-level Context Tree nodes, but it is not the Claude Code OTLP raw-body
capture path and it does not decrypt encrypted Codex reasoning. Snapshot-backed
`--at-step` resume remains Claude Code only.

## Optional OTLP Context Capture

For higher-fidelity Claude Code Context Trees:

```bash
opentraces setup capture-otlp
opentraces capture-otlp start
opentraces capture-otlp status --json
opentraces capture-otlp flush --session <session-id> --project <repo> --trace-id <trace-id>
opentraces capture-otlp flush --from-raw-bodies --session <session-id> --project <repo> --trace-id <trace-id>
```

The OTLP receiver feeds OTel events and raw API-body files into the same
Context Tree substrate. If the receiver is down, agent traffic is not blocked.
`flush` lands the captured context in the bucket and joins it to the trace
spine, so `ctx show` / `ctx step` can read it; `--from-raw-bodies`
reconstructs an already-captured session per-step from the raw bodies with no
live receiver.

## Enrichment

| Enrichment | Where it surfaces |
|------------|-------------------|
| Git signals | `TraceRecord.git_links`, `patches[].anchor`, Trace Trails |
| Trace patches | `TraceRecord.patches[]`, `trail.jsonl.gz` |
| Context Tree | `steps[].context_node_id`, `context_tree_summary`, `context.jsonl.gz` |
| Dependencies | `TraceRecord.dependencies`, Trace Index facets |
| Metrics | `TraceRecord.metrics` |
| Security metadata | `metadata.security.tools_applied` when tools are run |

Security tools are optional. Capture does not imply automatic sanitization.
A bucket flow or workflow can run `opentraces security sanitize --tools ...`
or `--use-config` when it needs redaction.

## Existing Sessions

```bash
opentraces init --agent claude-code --import-existing
```

`--import-existing` currently imports existing Claude Code sessions for the
current repo. Other sources should be handled by their capture adapter or by a
workflow/import path.

## Next Step

```bash
opentraces bucket status
opentraces trace query --since 1d
opentraces trace map <trace-id> --bursts
opentraces dataset run my-dataset --dry-run
```
