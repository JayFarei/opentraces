# `capture/http_proxy/` — HTTP proxy capture (prototype, plan 078 evidence)

**Status: prototype.** This module exists to answer one question for
plan 078 ("HTTP proxy capture layer for opentraces"): when we add an
HTTP proxy capture source alongside our existing JSONL-parser source
for the Context Tree substrate, what bytes/fields does it give us that
JSONL parsing doesn't, on the same Claude Code session?

It is NOT wired into `core/ingest.py`, `opentraces ctx`, or the
production Context Tree capture path. The branch
`prototype/http-proxy-capture` keeps the prototype atomic; the
evidence report lives at
`tests/otbox/captures/http-proxy-prototype/comparison.md`.

## Layout

| File              | Role                                                                 |
|-------------------|----------------------------------------------------------------------|
| `proxy.py`        | Pure-stdlib + httpx forward proxy. Listens on `127.0.0.1:<port>`, forwards POSTs to the upstream backend, writes one JSONL record per request to `capture_log_path`. |
| `fake_backend.py` | Canned-response Anthropic backend (port 9876) so the prototype never burns real API tokens. Echo + scripted modes. |
| `capture.py`      | Reads the proxy log and emits `context_layer_captured` / `context_node_observed` / `context_tree_reconciled` events into the canonical Trail event log. |
| `compare.py`      | Runs both Context Tree pipelines (JSONL-only and JSONL+proxy) against the same session, diffs the resulting projections, writes the comparison report. |

## Capture-method tagging

The contract change at `src/opentraces/core/context_tree/contract.py`
adds `proxy` to `CAPTURE_METHOD_VALUES`. Every `ContextLayer` this
module emits carries `capture_method: proxy` and `completeness: full`
because the proxy sees the actual bytes the model was sent.

## Running the prototype

The fixture at
`tests/otbox/fixtures/sessions/context-tree-via-proxy/` drives both
pipelines end-to-end against a synthetic Claude Code session that also
issues real HTTP POSTs to the proxy. The comparison script renders the
report:

```bash
source .venv/bin/activate
python -m opentraces.capture.http_proxy.compare \
    --fixture tests/otbox/fixtures/sessions/context-tree-via-proxy \
    --output tests/otbox/captures/http-proxy-prototype/comparison.md
```

(`compare.py` is the prototype's own entry point; it does not register
as a Click subcommand on `opentraces`.)

## Honest non-goals

- **No TLS interception.** The proxy is plain HTTP. Real Claude Code
  POSTs to `api.anthropic.com` over HTTPS; production would need
  either mitmproxy-style MITM or an env-var override on a plain
  HTTP upstream (LiteLLM's gateway pattern).
- **No streaming response coalescing.** The proxy reads the full
  response body before forwarding it. Streaming responses (`stream:
  true`) will buffer and the latency report will be wrong; the
  Context Tree layers will still be correct because the request body
  is what feeds the layers.
- **No auth.** The proxy redacts `Authorization`, `x-api-key`, and
  `anthropic-api-key` request headers in the capture log so credentials
  never end up alongside the request bodies; it does NOT add its own
  auth. Local-loopback only.
- **No retry / backpressure / rate-limit handling.** If the upstream
  is down, the request fails and the capture log records the error.
- **No integration with `opentraces ctx`.** The CLI's `ctx tree` /
  `ctx show` / etc. read from the canonical event log, so they WILL
  see proxy-captured layers and nodes if the events get appended.
  The prototype just doesn't introspect them via the CLI; the
  comparison script reads the projection directly.

## Top-level integration sketch (out of scope for the prototype, captured here for plan 078)

```text
Claude Code session
       |
       v
+----------+         +----------+         +-----------------+
| HTTP POST|-------->|  proxy   |-------->| api.anthropic   |
|          |         |          |         |     .com        |
+----------+         +----+-----+         +-----------------+
                          |
                          | (one JSONL line per request)
                          v
              tests/.../http_capture.jsonl
                          |
                          | emit_context_tree_events_from_http_log()
                          v
            refs/opentraces/local/events/v1
                          ^
                          |
                          | emit_context_tree_events_from_record()
                          |
                JSONL parser (existing)
```

Two independent capture paths, one canonical event log. The query
projection at `core/context_tree/query.py` doesn't care which path
emitted an event; the comparison script reads it the same way `ctx
tree` would.
