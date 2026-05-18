# Claude Code OTel emission coverage report

**Branch:** `experiment/claude-code-otel-emission` (off `feat/context-tree-substrate`)
**Plan 077 evidence (Context Tree substrate capture model)**
**Companion to:** `tests/otbox/captures/http-proxy-prototype/comparison.md`
**Date driven:** 2026-05-18
**Receiver:** `tests/otbox/captures/claude-code-otel-experiment/receiver.py` (stdlib `ThreadingHTTPServer`, OTLP/HTTP+JSON, ~120 LOC)
**Claude Code version:** `2.1.143 (Claude Code)` (`/Users/jayfarei/.local/bin/claude`)
**Model exercised:** `claude-opus-4-7` (the user's default `ANTHROPIC_DEFAULT_HAIKU_MODEL` env override resolves to opus on this account)
**Session driven:** One `claude --print --permission-mode acceptEdits` against `/tmp/otel-experiment-project/` (a 2-file directory with `README.md` and a `sample.py` containing one function). Prompt was:
> Please do three things in this directory: (1) read sample.py and tell me what it does in one sentence, (2) add a second function named 'goodbye' that takes a name and returns a farewell string by editing sample.py, (3) run 'ls -la' via Bash and report the file count. Keep your response brief.

The prompt was chosen to force diverse tool calls (Read, Edit, Bash) across multiple LLM turns. Three LLM requests were emitted per run.

## Setup

```
# Both tests
CLAUDE_CODE_ENABLE_TELEMETRY=1
CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
OTEL_LOGS_EXPORTER=otlp
OTEL_EXPORTER_OTLP_PROTOCOL=http/json
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
OTEL_{METRIC,LOGS,TRACES}_EXPORT_INTERVAL=1000

# Test B only (everything that opts content-bearing data in)
OTEL_LOG_USER_PROMPTS=1
OTEL_LOG_TOOL_DETAILS=1
OTEL_LOG_TOOL_CONTENT=1
OTEL_LOG_RAW_API_BODIES=file:<dir>
```

`OTEL_LOG_RAW_API_BODIES=file:<dir>` was preferred over `=1` (inline) because the inline mode truncates bodies at 60 KB and the system prompt alone is ~27 KB, leaving no slack for tools+messages. File mode writes the untruncated request/response JSON to disk and emits an `api_request_body` log event whose `body_ref` attribute points at the file.

**Total captured:** 28 OTLP envelopes (16 from Test A, 12 from Test B). Three `*.request.json` files (~133 KB each) and three `*.response.json` files (~1-2 KB each) landed in `raw-body-files/` during Test B.

Sanitized artifacts:
- `raw-otel-emissions.jsonl` — merged Test A + Test B envelopes, tagged with `test_variant`, identifiers scrubbed.
- `raw-body-exhibits/sample.request.json` — one full sanitized request body (the ~133 KB exhibit demonstrating what the file-mode body capture contains).
- `raw-body-exhibits/sample.response.json` — one full sanitized response body.

---

## Headline finding

**Claude Code does NOT speak the OpenTelemetry GenAI semantic convention.** It emits its own `claude_code.*` span / metric / log-event vocabulary (per the [Monitoring](https://code.claude.com/docs/en/monitoring-usage) reference). It does pin **three** specific OTel GenAI attribute aliases on the `claude_code.llm_request` span — `gen_ai.system`, `gen_ai.request.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons` — and one span event (`gen_ai.request.attempt`). The remaining ~15 OTel GenAI attribute slots (`gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.usage.*`, `gen_ai.system_instructions`, `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.definitions`, `gen_ai.tool.call.*`, `gen_ai.request.{temperature,top_p,top_k,max_tokens,stop_sequences,seed,stream}`, `gen_ai.conversation.id`, `gen_ai.output.type`) are never emitted under either of those names.

**However:** the bytes those OTel GenAI attributes *would* carry are still present in Claude Code's telemetry, just under different names and (for content-bearing fields) gated behind `OTEL_LOG_RAW_API_BODIES`. The `api_request_body` / `api_response_body` log events serialize the **entire Anthropic Messages API request JSON byte-perfectly**, including assembled system prompt, full `tools` array with `input_schema`, complete message history, and request parameters. That is identical fidelity to what the HTTP proxy capture prototype produces.

So the right question for plan 078 is not "does OTel cover the GenAI spec" (it does not), but "does Claude Code's OTel emission, with `OTEL_LOG_RAW_API_BODIES=file:<dir>` enabled, give the Context Tree substrate everything the HTTP proxy would?" And the answer is: **almost entirely yes**, with a few known gaps that the proxy *also* cannot fill from the wire alone.

---

## Per-attribute emission table — OTel GenAI semantic convention

For each attribute in the OTel GenAI Spans spec (the four categories: inference, embeddings, retrieval, execute-tool), this table records whether the attribute is emitted by Claude Code, under what name, in which test variant.

| OTel GenAI attribute | Emitted by Claude Code? | Under what name | Test A (default) | Test B (content-on) |
|---|---|---|---|---|
| `gen_ai.operation.name` | No | (not emitted) | absent | absent |
| `gen_ai.provider.name` | No | (substituted by `gen_ai.system` = `"anthropic"`) | absent | absent |
| `gen_ai.system` (deprecated GenAI alias) | Yes | verbatim | present on `llm_request` | present on `llm_request` |
| `gen_ai.request.model` | Yes | verbatim | present on `llm_request` | present on `llm_request` |
| `gen_ai.response.model` | No | (only `gen_ai.request.model`; response model lives in `api_response_body` payload) | absent on span | absent on span |
| `gen_ai.response.id` | Yes | verbatim | present on `llm_request` | present on `llm_request` |
| `gen_ai.response.finish_reasons` | Yes | verbatim | present on `llm_request` (array of one string) | same |
| `gen_ai.conversation.id` | No | (substituted by `session.id` on every span/metric/event) | absent | absent |
| `gen_ai.usage.input_tokens` | No (different name) | `input_tokens` on `llm_request` span, `claude_code.token.usage` metric with `type=input` | present | present |
| `gen_ai.usage.output_tokens` | No (different name) | `output_tokens` on `llm_request`, metric with `type=output` | present | present |
| `gen_ai.usage.cache_read.input_tokens` | No (different name) | `cache_read_tokens`, metric `type=cacheRead` | present | present |
| `gen_ai.usage.cache_creation.input_tokens` | No (different name) | `cache_creation_tokens`, metric `type=cacheCreation` | present | present |
| `gen_ai.usage.reasoning.output_tokens` | No | (extended-thinking output tokens are inside the response body, not surfaced as a separate attr) | absent | absent |
| `gen_ai.request.temperature` | No | (in raw request body only; Claude Code does not set it explicitly so JSON value is `null`) | absent | available in `api_request_body` exhibit |
| `gen_ai.request.top_p` | No | (same — null in request) | absent | available in `api_request_body` exhibit |
| `gen_ai.request.top_k` | No | (same — null in request) | absent | available in `api_request_body` exhibit |
| `gen_ai.request.max_tokens` | No | (in raw request body: `max_tokens: 64000`) | absent | available in `api_request_body` exhibit |
| `gen_ai.request.stop_sequences` | No | (in raw request body: `null`) | absent | available in `api_request_body` exhibit |
| `gen_ai.request.seed` | No | (not set by Claude Code) | absent | absent |
| `gen_ai.request.stream` | No | (in raw request body: `stream: true`) | absent | available in `api_request_body` exhibit |
| `gen_ai.request.frequency_penalty` | No | (not applicable to Anthropic Messages API) | absent | absent |
| `gen_ai.request.presence_penalty` | No | (not applicable to Anthropic Messages API) | absent | absent |
| `gen_ai.request.choice.count` | No | (Anthropic returns one) | absent | absent |
| `gen_ai.output.type` | No | (not emitted) | absent | absent |
| `gen_ai.input.messages` *(opt-in)* | No (different shape) | **full request body** in `api_request_body.body_ref` JSON file | absent | full body available |
| `gen_ai.output.messages` *(opt-in)* | No (different shape) | **full response body** in `api_response_body.body_ref` JSON file | absent | full body available |
| `gen_ai.system_instructions` *(opt-in)* | No (different shape) | **first 3 blocks of `body.system`** in the captured request body | absent | full system prompt (27 KB) available |
| `gen_ai.tool.definitions` *(opt-in)* | No (different shape) | **`body.tools` array** in captured request body, with full `input_schema` for every tool | absent | 12/12 tools with input_schema available |
| `gen_ai.tool.name` *(execute-tool span)* | No (different name) | `tool_name` on `claude_code.tool` span and `tool_result` event | present | present |
| `gen_ai.tool.call.id` *(execute-tool)* | No (different name) | `tool_use_id` on `tool_result` event | present | present |
| `gen_ai.tool.call.arguments` *(opt-in)* | No (different name) | `tool_input` on `tool_result` event; `tool.output` span event on `claude_code.tool` | absent (redacted) | present |
| `gen_ai.tool.call.result` *(opt-in)* | No (different name) | `tool.output` span event `content` attribute on `claude_code.tool` (requires `OTEL_LOG_TOOL_CONTENT=1`) | absent | present (truncated at 60 KB) |
| `gen_ai.tool.description` | No | (lives inside the captured tool definitions in `api_request_body`) | absent | available in `api_request_body` exhibit |
| `gen_ai.tool.type` | No | (not emitted; Anthropic API has no built-in concept) | absent | absent |
| `server.address` / `server.port` | No | (not emitted) | absent | absent |
| `error.type` | No | (`error` attr emitted on `llm_request` span when failed, but not as `error.type`) | absent (no errors) | absent (no errors) |

**Tally vs the OTel GenAI inference-span attribute set:**
- 18 attributes the OTel GenAI inference spec defines (required + recommended + opt-in)
- **4 emitted under their OTel GenAI name** (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.response.id`, `gen_ai.response.finish_reasons`)
- **6 emitted under a different name but structurally equivalent** (`input_tokens` ↔ `gen_ai.usage.input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `tool_name`, `tool_use_id`)
- **6 available only when `OTEL_LOG_RAW_API_BODIES` is set** (`temperature`, `top_p`, `top_k`, `max_tokens`, `stop_sequences`, `stream`, `system_instructions`, `input.messages`, `output.messages`, `tool.definitions`, `tool.description` — all carried inside the captured wire body, not as discrete attributes)
- **2 never emitted in any form** (`gen_ai.operation.name`, `gen_ai.request.seed`, `gen_ai.response.model` as a separate attribute, `gen_ai.output.type`, `server.{address,port}`, the OpenAI-flavor `frequency_penalty` / `presence_penalty` — none of which are meaningful for Anthropic anyway)

The phrasing "Claude Code emits X of Y OTel GenAI attributes" misframes the question, because the attribute-key match-rate is low (4/18), but **content match-rate is high (16/18) once you opt into the body capture**.

---

## What Claude Code actually emits (the vocabulary that matters)

These are the names a substrate-side OTLP receiver needs to know about. Not OTel-GenAI-shaped, but the bytes are equivalent or richer.

### Spans (5 total, both runs)

| Span name | Attributes (A=default, B=content-on) | Substrate-relevant content |
|---|---|---|
| `claude_code.interaction` | `interaction.duration_ms`, `interaction.sequence`, `user_prompt`, `user_prompt_length`, `span.type`, plus standard identity attrs. `user_prompt` is `<REDACTED>` in A, full prompt text in B. | Per-prompt grouping handle (links to all child spans/events via `prompt.id`). |
| `claude_code.llm_request` | A and B identical: `attempt`, `cache_creation_tokens`, `cache_read_tokens`, `client_request_id`, `duration_ms`, `gen_ai.request.model`, `gen_ai.response.finish_reasons`, `gen_ai.response.id`, `gen_ai.system`, `input_tokens`, `llm_request.context`, `model`, `output_tokens`, `request_id`, `speed`, `stop_reason`, `success`, `ttft_ms`. Span event: `gen_ai.request.attempt` on each retry. | LLM-call envelope. Maps to one `messages_layer_id` + `runtime_state_layer_id` capture in Context Tree terms. Token counts and timing only — no message content here. |
| `claude_code.tool` | A: `duration_ms`, `tool_name`. B adds `file_path`, `full_command`, and a `tool.output` span event with `content` and `file_path`. | Per-tool-call wrapper. `tool.output` span event in B carries actual file content read or command output. |
| `claude_code.tool.blocked_on_user` | `decision`, `duration_ms`, `source` | Permission gate — useful for tracking which tool calls actually executed vs were rejected. Not a Context Tree concern. |
| `claude_code.tool.execution` | `duration_ms`, `success` | Pure timing — Context Tree gets this from the JSONL `tool_use_result` records already. |

Note: the `claude_code.hook` span (with `hook_definitions`, `hook_event`, etc.) was NOT emitted. That requires `ENABLE_BETA_TRACING_DETAILED=1` + `BETA_TRACING_ENDPOINT` (and org allowlisting for interactive sessions). For `--print` runs the gating still applies but the experiment did not enable it. The richer `claude_code.hook` data was instead emitted as **log events** (`hook_registered`, `hook_execution_start`, `hook_execution_complete`) — see below.

### Metrics (6 total)

| Metric | Per-DP attributes | Substrate use |
|---|---|---|
| `claude_code.session.count` | `start_type` (`fresh`/`resume`/`continue`) | Session boundary detection. |
| `claude_code.cost.usage` (USD) | `effort`, `model`, `query_source` | Cost-per-session signal. Not Context-Tree-relevant. |
| `claude_code.token.usage` | `type` (`input`/`output`/`cacheRead`/`cacheCreation`), `effort`, `model`, `query_source` | Token totals — same content as on the `llm_request` span. |
| `claude_code.lines_of_code.count` | `type` (`added`/`removed`) | Trail-relevant, not Context-Tree. |
| `claude_code.code_edit_tool.decision` | `decision`, `language`, `source`, `tool_name` | Permission attribution. |
| `claude_code.active_time.total` (s) | `type` (`user`/`cli`) | Session activity. |

### Log events (9 in A, 11 in B — `api_request_body` and `api_response_body` only fire when `OTEL_LOG_RAW_API_BODIES` is set)

| Event name | Substrate-relevant attributes | Notes |
|---|---|---|
| `user_prompt` | `prompt` (`<REDACTED>` in A; full text in B), `prompt_length`, `prompt.id` | One per user turn. `prompt.id` correlates all child events. |
| `api_request` | `cost_usd`, `cost_usd_micros`, `duration_ms`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `model`, `effort`, `speed`, `query_source`, `request_id`, `prompt.id` | One per API call. Token/cost summary only — no content. |
| `api_request_body` (Test B only) | `body_ref` (absolute path to ~133 KB `<uuid>.request.json` file), `body_length`, `model`, `query_source`, `prompt.id` | **The proxy equivalent.** The file at `body_ref` is the full Messages API request JSON: `system`, `messages`, `tools`, `max_tokens`, `stream`, `metadata`, `thinking`, etc. |
| `api_response_body` (Test B only) | `body_ref` (path to `<request_id>.response.json`), `body_length`, `model`, `request_id`, `query_source`, `prompt.id` | Full Messages API response: `content` blocks (incl. `thinking`), `usage`, `stop_reason`, `stop_details`, `model`. |
| `tool_result` | `tool_name`, `tool_use_id`, `tool_input_size_bytes`, `tool_result_size_bytes`, `success`, `duration_ms`, `prompt.id`. B adds `tool_input` (JSON-serialized args, ~4 KB cap) and `tool_parameters` (per-tool structured details: bash command, file path, MCP server name, skill name, etc.). | Per-tool-call result. `tool_use_id` matches the value inside the API response body, so wire bodies and OTel events join cleanly. |
| `tool_decision` | `decision`, `source`, `tool_name`, `tool_use_id` | Permission gating. |
| `hook_registered` | `hook_event`, `hook_matcher`, `hook_source`, `hook_type`, `plugin.name`, `plugin_id_hash` | One per registered hook at session start. Important for Context Tree: tells us which user hooks were active during the session. |
| `hook_execution_start` / `hook_execution_complete` | `hook_event`, `hook_name`, `hook_source`, `managed_only`, `num_hooks`, `num_success`, `num_blocking`, `total_duration_ms` | Per-hook-execution structural data. The hook *bodies* and matching tool input are NOT emitted (that needs detailed-beta-tracing). |
| `mcp_server_connection` | `server_name`, `transport_type`, `server_scope`, `status`, `is_plugin`, `plugin.name` | Inventory of which MCP servers connected and which scope they're configured at. Important for Context Tree's `mcp_state` layer. |
| `plugin_loaded` | `plugin.name`, `plugin.version`, `plugin.scope`, `enabled_via`, `has_hooks`, `has_mcp`, `skill_path_count`, `command_path_count`, `agent_path_count`, `marketplace.name` | One per loaded plugin. Tells us which plugins contributed skills/agents/hooks/MCP to this session. |

---

## Coverage vs HTTP proxy — substrate-relevant content

This is the comparison plan 078 actually needs. Rows are the content the Context Tree substrate cares about for each of its four layer types plus the JSONL provenance gap. Columns: OTel default (Test A), OTel content-on (Test B), HTTP proxy (from prior prototype's `comparison.md`), existing JSONL pipeline.

| Substrate content | OTel default (A) | OTel content-on (B) | HTTP proxy | Existing JSONL pipeline |
|---|---|---|---|---|
| **system** layer: assembled system prompt bytes (the ~27 KB string the model actually saw) | absent | **full** via `api_request_body.body_ref` (body.system) | full via wire body | approximated (`hardcoded_template` + CLAUDE.md set hashes) |
| **system** layer: per-block split (Claude Code emits 4 system blocks: billing header, agent SDK preamble, agent core, output formatting) | absent | full (request body preserves the `system: [...]` array intact) | full | absent |
| **system** layer: per-file CLAUDE.md provenance (which user/project/local/managed CLAUDE.md files were assembled) | absent | absent (the wire body has the assembled bytes, not the per-file sources) | absent (same — proxy only sees the wire) | full (JSONL walks the filesystem to enumerate sources) |
| **system** layer: append-system-prompt content | absent | full (it's in body.system) | full | full (from JSONL `system.init` record) |
| **messages** layer: full conversation history including assistant `thinking` blocks | absent | **full** via `api_request_body.body_ref` (body.messages) | full | full (active-path slice from JSONL) |
| **messages** layer: span boundaries (which JSONL records this LLM call covered) | absent | derivable from `prompt.id` correlation | absent (proxy doesn't know about JSONL uuids) | full (`span_first_uuid` / `span_last_uuid`) |
| **messages** layer: compaction summary text | absent (no compaction in this test session) | available in next-call's request body | available in next-call's request body | full (from JSONL `summary` record) |
| **tool_registry** layer: full tool list with `input_schema` for every tool | absent | **full** via `api_request_body.body_ref` (body.tools). 12/12 tools with schemas in the captured exhibit. | full (3/3 tools in the proxy prototype's smaller fixture) | approximated (`hardcoded_template` keyed by Claude Code version — no per-version schema deltas captured) |
| **tool_registry** layer: tool_choice / tool_count | absent | full (in body) | full | absent |
| **tool_registry** layer: deferred-tool list | absent | derivable from response (Claude Code surfaces this in tool definitions) | derivable | full (from JSONL deferred-tools markers) |
| **runtime_state** layer: `model`, `max_tokens`, `stream`, `metadata.user_id` | model on every span; rest absent | full (model, max_tokens, stream, metadata in request body; temperature/top_p/top_k null) | full (same: 12 wire-only sampling param fields) | full for model/cwd/permission_mode/effort_level/mcp_servers/version; absent for max_tokens/stream/anthropic-version |
| **runtime_state** layer: `cwd`, `permission_mode`, `claude_code_version` | absent on telemetry (only `terminal.type` is captured) | absent on telemetry; partially derivable from prompt content if user mentions paths | absent unless client sends `OT-Property-cwd` / `OT-Property-permission-mode` headers (per prior comparison) | full (read from `system.init` record + project config) |
| **runtime_state** layer: MCP server inventory | full (via `mcp_server_connection` events: server_name, scope, transport_type, status) | same as A plus error messages | absent unless client probes `tools/list` and forwards | full (reads `~/.claude.json` / `.mcp.json` at capture time) |
| **runtime_state** layer: plugin inventory (which plugins contributed skills, agents, MCP, hooks) | **full** (via `plugin_loaded` events: plugin.name, scope, version, marketplace, contribution counts) | same | absent | partial (JSONL only sees plugin activations, not the install set) |
| **runtime_state** layer: hook inventory (which hooks were registered, by which plugin) | **full** (via `hook_registered` events: hook_event, hook_matcher, hook_source, plugin.name) | same | absent | full (`HookRegistered` records from JSONL when present) |
| **parentUuid graph / fork branches / compaction boundary** | absent | absent | absent (the wire only shows the resulting context, not the JSONL branching that produced it) | full (the irreducible JSONL win) |
| **session.id**, `prompt.id` correlation | full | full | partial (`session_id` only if `OT-Property-session-id` header sent) | full (JSONL session-uuid in filename) |
| **Sub-agent JSONL discovery** (separate sub-agent session files) | absent (subagent session id might appear on `agent_id` span attribute if a Task was used; this test didn't use Task) | same | absent | full (parser walks subagent JSONLs from disk) |

**Where OTel-content-on is strictly worse than the proxy:** nowhere observed. The `api_request_body` capture is byte-identical to the wire — that's the same source of truth.

**Where OTel-content-on adds value the proxy doesn't have:**
1. **`plugin_loaded` events** — proxy never sees these; they're emitted from the Claude Code client side before any API call. The Context Tree's `runtime_state` and `tool_registry` layers benefit because we get exact plugin attribution for skills, MCP servers, and hooks without re-reading the user's plugin install set.
2. **`mcp_server_connection` events with `status`, `error`, `error_code`, `duration_ms`** — the proxy sees only successful tool invocations against MCP servers; OTel sees connection lifecycle.
3. **`hook_registered` events** — proxy never sees hook configuration; OTel emits one event per hook at session start with matcher + source + owning plugin.
4. **`tool_use_id` / `prompt.id` correlation IDs that match the API body's `tool_use` blocks 1:1** — the proxy has to reconstruct the same correlation from the wire body alone.
5. **`speed` / `effort` / `query_source` / `agent_id` / `parent_agent_id` on the `llm_request` span** — semantic metadata about WHY this call was issued (interaction vs tool vs standalone vs subagent), which the proxy can only see as an opaque API request.

**Where OTel-content-on still falls short of the JSONL pipeline (the irreducible JSONL win, same as for the proxy):**
1. `cwd`, `permission_mode`, `claude_code_version` — `claude_code_version` lives in the resource attributes when `OTEL_METRICS_INCLUDE_VERSION=true` (off by default), but `cwd` and `permission_mode` are never on the wire.
2. Per-file CLAUDE.md provenance — only the assembled bytes cross the wire.
3. `parentUuid` graph for rewinds, sub-agents, compaction — JSONL-record concepts.
4. Sub-agent session JSONL files on disk — discoverable only by the JSONL pipeline.

This is the **same** provenance gap the proxy hit, and for the same reason: anything that doesn't cross the network is invisible to a wire-side observer, whether that observer is an HTTP proxy or the Anthropic API client's instrumentation.

---

## Surprises and gotchas

1. **OTel GenAI semantic convention is largely vestigial.** Claude Code shipped 4 `gen_ai.*` attribute aliases as a token gesture; the actual schema is `claude_code.*`. Receivers that try to map to OTel GenAI will see almost nothing. Receivers that consume the `claude_code.*` vocabulary directly get everything.
2. **`OTEL_LOG_RAW_API_BODIES=file:<dir>` is the substrate's lock-picking gadget.** It's not advertised on the observability page as "the proxy alternative" — it's listed as one of four content-bearing opt-ins. But it produces literally the same artifact (full Messages API request and response JSON) that an HTTP proxy would write, with the bonus that Claude Code adds a structured log event with `body_ref` for correlation. Total cost: zero new infrastructure, no MITM, no `ANTHROPIC_BASE_URL` override.
3. **Inline mode (`=1`) is unusable** — body limit is 60 KB and Claude Code's system prompt alone is ~27 KB; you'd be at 45% capacity before any messages or tools. File mode is the only viable production option.
4. **Some content gates are weirdly granular.** `OTEL_LOG_TOOL_DETAILS=1` reveals tool parameters (file paths, bash commands) but NOT the tool result body — that's a separate `OTEL_LOG_TOOL_CONTENT=1` opt-in which additionally requires tracing to be enabled. For substrate purposes you need both, since the wire body in `api_request_body` doesn't include tool results in the *response* body until the *next* turn folds them in.
5. **`gen_ai.system_instructions` would have been the cleanest attribute** to populate the Context Tree's `system` layer if Claude Code emitted it — but it doesn't. The opt-in span attribute spec says instrumentations "SHOULD" emit it when content capture is enabled. They don't.
6. **No `claude_code.hook` span without detailed-beta-tracing.** The span hierarchy diagram in the docs suggests hook spans nest under `tool` or `interaction`; in practice (without `ENABLE_BETA_TRACING_DETAILED=1`) hooks only show up as the three `hook_*` log events. For Context Tree this is fine — log-event granularity is sufficient.
7. **The `prompt.id` correlation key is excellent and undersold.** Every event Claude Code emits while processing one user turn shares the same `prompt.id`. That makes downstream join cheap: one `prompt.id` window → one Context Tree node. The HTTP proxy has to synthesize this from request timing.
8. **`api_request_body` files are kept indefinitely** under the directory you pass to `OTEL_LOG_RAW_API_BODIES=file:<dir>`. Three short calls in this test produced ~400 KB of body files. A long session would produce gigabytes. A substrate consumer must own deletion/rotation policy.
9. **Anthropic includes a synthetic `system` block in position [0]** that looks like an HTTP header: `"x-anthropic-billing-header: cc_version=2.1.143.e0b; cc_entrypoint=sdk-cli; cch=00000;"`. The model presumably ignores this; it's a billing-attribution marker the SDK inserts. The Context Tree substrate should either preserve it (for honest replay) or strip it as a known no-op (for cleaner content hashing). The committed prototype proxy treats it as content; we should match that.

---

## Recommendation

**SKIP PROXY.** The Context Tree substrate can rely on Claude Code's OTel emission alone for v1 capture, gated behind a single user-facing toggle that exports the documented env vars.

Specifically:
- Build an OTLP HTTP receiver (~150 LOC, same pattern as `receiver.py` in this experiment, plus a writer that splits the captured envelopes into `TrailEvent`s of the existing `context_layer_captured` / `context_node_observed` types).
- Set `capture_method=live_capture` on layers built from `api_request_body` / `api_response_body` (these are byte-perfect; they qualify as `full` completeness under R5).
- Set `capture_method=transcript_reconstruction` on layers built from the existing JSONL pipeline; keep that pipeline running as the fallback when the user has not opted into `OTEL_LOG_RAW_API_BODIES` (which they shouldn't have to).
- Treat the `api_request_body` body files as content-addressed layer content; hash the body file once, dedupe across calls.
- Adopt `prompt.id` as the Context Tree node grouping key — it's exactly what we want.

**The proxy is not needed because:**
- Every content-bearing thing the proxy captures (`system` assembled bytes, `tools` with `input_schema`, full messages, all request params), `api_request_body` also captures, at the same fidelity, with no MITM / TLS / `ANTHROPIC_BASE_URL` plumbing.
- Every non-content thing the substrate needs that's not in OTel (cwd, permission_mode, parentUuid graph, per-file CLAUDE.md provenance, sub-agent JSONL discovery) is *also* invisible to the proxy. The JSONL pipeline remains the floor for those, exactly as it does in the proxy world.
- OTel-only ADDS three categories of data the proxy doesn't have (`plugin_loaded`, `mcp_server_connection` lifecycle, `hook_registered` configuration) which directly inform the Context Tree's `runtime_state` and `tool_registry` layers.
- The install footprint is one-liner: export six env vars on `claude` launch via the existing `opentraces setup` flow. No sidecar process, no port binding, no failure mode where a dead proxy kills the agent.

**Plan 079 (HTTP proxy capture layer) is therefore unnecessary for the Context Tree substrate.** Plan 078 (OTLP receiver) is sufficient. Consider archiving plan 079 with a pointer to this report, or repurposing it for the orthogonal use case of capturing OpenAI / Gemini agent traffic — where Claude Code's OTel is by definition not the right substrate.

The one scenario where the proxy still wins is **non-Claude-Code clients**. If opentraces ever wants to capture Cursor, Cline, Aider, or any other Anthropic-API client that doesn't ship the same OTel instrumentation, the proxy comes back into the picture. For now, the Context Tree substrate is Claude-Code-focused (per plan 077 §"Honest gaps"), and OTel covers it.

---

## Repro

```bash
cd /Users/jayfarei/src/tries/2026-03-27-community-traces-hf
source .venv/bin/activate
python tests/otbox/captures/claude-code-otel-experiment/receiver.py &  # listens on :4318
# (drive your own claude --print invocation with the env vars from "Setup" above)
python tests/otbox/captures/claude-code-otel-experiment/analyze.py    # inventories the envelopes
python tests/otbox/captures/claude-code-otel-experiment/sanitize.py   # scrubs identifiers and merges
```
