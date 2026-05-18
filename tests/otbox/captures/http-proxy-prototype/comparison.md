# HTTP proxy capture prototype — comparison report

**Branch:** `prototype/http-proxy-capture`  
**Plan:** 078 evidence (HTTP proxy capture layer for opentraces)  
**Fixture:** `tests/otbox/fixtures/sessions/context-tree-via-proxy`

## Setup

Both paths run the same fixture (`session.py`). Path A runs the JSONL 
capture path only; path B runs JSONL + the prototype HTTP proxy 
(stdlib `http.server` + `httpx` forwarding into a canned-response 
fake Anthropic backend on `127.0.0.1:<random>`). Each path uses an 
independent Git project so the canonical event logs don't collide.

Proxy choice: **option B (roll-our-own ~150-line httpx proxy)**. 
LiteLLM's value is in normalising provider differences and adding 
rate-limit/cost tooling; for the prototype's question ('what bytes 
does the proxy see that JSONL doesn't') neither applies and a 
stdlib forwarder gives full control of the capture-log shape.

## Headline numbers

- Path A (JSONL only):   `4` layers, `18` nodes across `1` traces.
- Path B (JSONL+proxy): `12` layers, `21` nodes across `1` traces.

**Completeness by (capture_method, layer_type):**

Path A (JSONL only):

| capture_method            | layer_type    | full | approximated | stub | total |
|---------------------------|---------------|------|--------------|------|-------|
| transcript_reconstruction | messages      | 1    | 0            | 0    | 1     |
| transcript_reconstruction | runtime_state | 1    | 0            | 0    | 1     |
| transcript_reconstruction | system        | 0    | 1            | 0    | 1     |
| transcript_reconstruction | tool_registry | 0    | 1            | 0    | 1     |

Path B (JSONL+proxy):

| capture_method            | layer_type    | full | approximated | stub | total |
|---------------------------|---------------|------|--------------|------|-------|
| proxy                     | messages      | 3    | 0            | 0    | 3     |
| proxy                     | runtime_state | 3    | 0            | 0    | 3     |
| proxy                     | system        | 1    | 0            | 0    | 1     |
| proxy                     | tool_registry | 1    | 0            | 0    | 1     |
| transcript_reconstruction | messages      | 1    | 0            | 0    | 1     |
| transcript_reconstruction | runtime_state | 1    | 0            | 0    | 1     |
| transcript_reconstruction | system        | 0    | 1            | 0    | 1     |
| transcript_reconstruction | tool_registry | 0    | 1            | 0    | 1     |

**Delta:** in path B every `tool_registry` and `system` layer 
the proxy emitted lands as `full`/`proxy` rather than 
`approximated`/`transcript_reconstruction`. The JSONL-emitted 
layers are still present (and still `approximated`); the proxy 
layers are additive content-addressed siblings in the same event 
log. Consumers that want full-fidelity replay filter on 
`capture_method == 'proxy'` and fall back to JSONL when no proxy 
layer is available.

## Worked example — first turn

### `system` layer

- JSONL layer id:  `sha256:853d4b6943281bda70859cf4d028d13674436d2603dd9b7e636867505e9c0c2f`
  - completeness=`approximated`, 
    capture_method=`transcript_reconstruction`
  - field_keys: `['append_system_prompt_hash', 'claude_md_set', 'environment_block', 'memory_md_head_hash', 'static_core_ref']`
- Proxy layer id:  `sha256:1564ed946892cdfad6264d2f1b1171493a8e15bf482eba62aa688ddd3847b575`
  - completeness=`full`, 
    capture_method=`proxy`
  - field_keys: `['assembled_text', 'assembled_text_bytes', 'block_count', 'blocks']`
  - **proxy adds:** `['assembled_text', 'assembled_text_bytes', 'block_count', 'blocks']`
  - **only-in-JSONL:** `['append_system_prompt_hash', 'claude_md_set', 'environment_block', 'memory_md_head_hash', 'static_core_ref']`

  - JSONL `static_core_ref`: `claude_code:otbox-fake-1.0`
  - JSONL `claude_md_set` length: `1`
  - Proxy `assembled_text_bytes`: `632`
  - Proxy `block_count`: `3`
  - Proxy `assembled_text` (first 240 chars): `'You are Claude, an AI assistant integrated into the Claude Code CLI. You help users with software-engineering tasks against a local project tree. Always prefer tool use over commentary, and always verify your work before claiming completion'`

### `messages` layer

- JSONL layer id:  `sha256:98ab70c8d50894153332a6858920cdd4ae2220191a55e8b6926be582a1ceb930`
  - completeness=`full`, 
    capture_method=`transcript_reconstruction`
  - field_keys: `['is_summary', 'messages', 'span_first_uuid', 'span_last_uuid', 'summary_of_span', 'total_token_estimate']`
- Proxy layer id:  `sha256:c98ec8d29214f1b09695188b244e0d3922338fbdd05b19867d8dd5f88eb257bc`
  - completeness=`full`, 
    capture_method=`proxy`
  - field_keys: `['is_summary', 'message_count', 'messages', 'span_first_index', 'span_last_index', 'summary_of_span', 'total_text_chars', 'total_token_estimate']`
  - **proxy adds:** `['message_count', 'span_first_index', 'span_last_index', 'total_text_chars']`
  - **only-in-JSONL:** `['span_first_uuid', 'span_last_uuid']`

### `tool_registry` layer

- JSONL layer id:  `sha256:82972b13b2e53a91616e5de2dd45a1d995c39f9bb0027b7881a9fb957461f2ab`
  - completeness=`approximated`, 
    capture_method=`transcript_reconstruction`
  - field_keys: `['deferred_tools', 'tools']`
- Proxy layer id:  `sha256:ad7372668c09f60e4d24ea7143d067f7f5a1fda290780eab807a45fca1480d6b`
  - completeness=`full`, 
    capture_method=`proxy`
  - field_keys: `['deferred_tools', 'tool_choice', 'tool_count', 'tools']`
  - **proxy adds:** `['tool_choice', 'tool_count']`

  - JSONL tool count: 3; with input_schema: 0
  - Proxy tool count: 3; with input_schema: 3

### `runtime_state` layer

- JSONL layer id:  `sha256:5b1d2d06b283dfa4f455d175904c4c8bd7d5345063f9b3a6a812ca5a9f73bba2`
  - completeness=`full`, 
    capture_method=`transcript_reconstruction`
  - field_keys: `['allowlisted_env', 'claude_code_version', 'cwd', 'effort_level', 'mcp_servers', 'model', 'permission_mode']`
- Proxy layer id:  `sha256:c0517d8c53bb7840678d75dd2ebd1ce3d401e0e8e74605a6900f8077247b90ea`
  - completeness=`full`, 
    capture_method=`proxy`
  - field_keys: `['allowlisted_env', 'anthropic_version', 'captured_at', 'captured_step_index', 'cwd', 'effort_level', 'max_tokens', 'mcp_servers', 'model', 'permission_mode', 'project_slug', 'session_id', 'stop_sequences', 'stream', 'temperature', 'top_k', 'top_p', 'wire_user_agent']`
  - **proxy adds:** `['anthropic_version', 'captured_at', 'captured_step_index', 'max_tokens', 'project_slug', 'session_id', 'stop_sequences', 'stream', 'temperature', 'top_k', 'top_p', 'wire_user_agent']`
  - **only-in-JSONL:** `['claude_code_version']`

## Harness-side provenance gap (what JSONL knows that proxy doesn't)

The proxy only sees the wire. The JSONL pipeline sees the agent's 
local process metadata that never crosses the network. The gap:

- **`cwd`, `permission_mode`, `claude_code_version`** — JSONL 
  reads these from the `system.init` record; proxy can only see 
  them when the client cooperates via `OT-Property-cwd`, 
  `OT-Property-permission-mode`, etc. headers (Helicone convention).
- **MCP server inventory** — the JSONL pipeline can read 
  `~/.claude.json` / `.mcp.json` at capture time; the proxy can't, 
  unless the client probes `tools/list` and forwards.
- **CLAUDE.md set with content hashes** — JSONL walks user / 
  project / local / managed scope folders; proxy sees only the 
  bytes that were assembled into the system prompt (which is 
  the actual question replay cares about, but you lose the 
  per-file provenance).
- **`parentUuid` graph for rewinds / sub-agents / compaction** — 
  those are JSONL-record concepts; the proxy sees only the 
  resulting wire traffic and cannot reconstruct branch_type.
- **Transcript offsets** for `ctx prune` to write a new session 
  file — the JSONL pipeline is authoritative.

## Gotchas hit during the prototype

1. **Capture-method vocabulary is closed.** Adding `proxy` 
   required a contract change at 
   `core/context_tree/contract.py::CAPTURE_METHOD_VALUES` plus a 
   Pydantic `Literal[...]` widening at 
   `core/context_tree/models.py::ContextLayer.capture_method`. 
   Two pre-existing tests used `"proxy"` as the sentinel for 
   'not in vocabulary' and had to switch to 
   `"not_a_real_capture_method"`. MINOR additive change but 
   not zero-cost.
2. **TLS is not solved by this prototype.** Real Claude Code 
   POSTs to `api.anthropic.com` over HTTPS. The prototype uses 
   plain HTTP against a fake backend; production would need 
   either mitmproxy-style MITM (cert trust on the client) or an 
   `ANTHROPIC_BASE_URL` override pointing at a plain-HTTP proxy. 
   The cleaner option for opentraces is the env-var override: 
   ship the proxy as a sidecar process and tell users to set 
   `ANTHROPIC_BASE_URL=http://127.0.0.1:4000` when starting 
   `claude`. No cert manipulation required.
3. **Provenance for `cwd` / `permission_mode` requires client 
   cooperation.** Adopting the Helicone `OT-Property-*` header 
   convention (the prototype uses `OT-Property-cwd`, 
   `OT-Property-step-index`, `OT-Property-session-id`) gets us 
   the missing fields, but it requires a tiny Claude Code hook 
   patch (or a SessionStart hook that exports an env var the 
   client's HTTP layer picks up). Plan 078 needs to call this 
   out as a hard dependency.
4. **Streaming responses (`stream: true`) are not captured 
   correctly by a naive forward proxy** — the SSE event 
   stream needs reassembly. The prototype dodges by using a 
   non-streaming fake backend; production must address this.
5. **The proxy is a single point of failure.** If it's down, 
   requests fail. The prototype handles this by writing the 
   error into the capture log and 500ing the client. 
   Production needs either: (a) async-mode where the proxy 
   fires-and-forgets the log and never blocks the client, or 
   (b) bypass mode where a missing proxy degrades to JSONL-only 
   capture. Both are >1 day of work; flag in plan 078.

## Recommendation for plan 078

- **Substrate: roll our own.** LiteLLM is overkill for the 
  capture use case and adds ~80 transitive dependencies. A 
  ~300-line production proxy (this prototype + retry + streaming 
  + structured logging + sidecar lifecycle) keeps the install 
  footprint minimal. Revisit only if we want OpenAI/Gemini 
  capture too.
- **Wiring: env-var override, not MITM.** Document 
  `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`. Avoid cert trust 
  and keep the install instructions to one line.
- **Default to bypass mode.** Proxy goes down → client traffic 
  goes straight to Anthropic. Capture is best-effort; never 
  block the agent. JSONL-only capture remains the floor.
- **Adopt `OT-Property-*` headers as the substrate convention.** 
  Steal Helicone's convention verbatim; bind opentraces-specific 
  fields under one prefix so a sniffer can't collide with 
  Helicone's own. Bonus: existing Helicone customers' tooling 
  works on opentraces data unchanged.
- **Keep the JSONL pipeline.** The proxy is additive, not 
  replacement. The harness-side provenance gap (cwd, MCP, 
  CLAUDE.md set, parentUuid branching) is irreducible from the 
  wire alone.
- **`capture_method: proxy` as the layer-level join key.** Two 
  layers for the same step (one JSONL, one proxy) live 
  side-by-side in the event log; downstream consumers pick the 
  preferred source per layer_type, falling back when missing. 
  This is exactly what `core/context_tree/query.py::layer_diff` 
  already supports.
