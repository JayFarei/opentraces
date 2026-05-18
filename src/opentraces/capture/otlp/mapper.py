"""OTLP envelope mapper (plan 078, R2 + R4).

Pure functions translating one accepted OTLP/HTTP+JSON envelope (as the
receiver hands it to its capture callback) into draft inputs the emitter
consumes. The emitter owns ``build_layer`` / ``build_node`` so this
module stays free of substrate-side concerns and easy to unit test.

Envelope shape (from ``receiver.py::CaptureEnvelope``)::

    {"received_at": float, "signal": "traces"|"logs"|"metrics"|"v1/...",
     "path": "/v1/{traces,logs,metrics}", "body": parsed OTLP JSON,
     "raw_size": int}

Mapping rules sourced verbatim from plan 078 §"Attribute mapping" plus
the OTel emission experiment coverage report (``tests/otbox/captures/
claude-code-otel-experiment/emission-coverage.md``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MapperResult:
    """Draft inputs derived from one envelope; the emitter merges them."""

    runtime_state_updates: dict[str, Any] = field(default_factory=dict)
    tool_registry_updates: dict[str, Any] = field(default_factory=dict)
    system_layer_hint: dict[str, Any] | None = None
    node_observation: dict[str, Any] | None = None
    raw_body_ref: str | None = None
    lifecycle_event: dict[str, Any] | None = None
    session_id: str | None = None
    prompt_id: str | None = None
    request_id: str | None = None


# --- OTLP AnyValue + attribute helpers ------------------------------------- #


def _unwrap_any(value: dict[str, Any]) -> Any:
    if not isinstance(value, dict):
        return value
    for k, cast in (("stringValue", lambda v: v),
                    ("intValue", lambda v: int(v) if isinstance(v, (int, str)) and str(v).lstrip("-").isdigit() else v),
                    ("boolValue", bool),
                    ("doubleValue", lambda v: float(v) if isinstance(v, (int, float, str)) else v)):
        if k in value:
            try:
                return cast(value[k])
            except (TypeError, ValueError):
                return value[k]
    if "arrayValue" in value:
        return [_unwrap_any(v) for v in (value["arrayValue"] or {}).get("values", [])]
    if "kvlistValue" in value:
        return {e.get("key"): _unwrap_any(e.get("value", {}))
                for e in (value["kvlistValue"] or {}).get("values", [])}
    return None


def _otlp_attr(attrs: list[dict[str, Any]] | None, name: str) -> Any | None:
    """Flatten one OTLP attribute by key; returns the natural Python value."""
    for entry in attrs or []:
        if entry.get("key") == name:
            return _unwrap_any(entry.get("value", {}) or {})
    return None


def _merged_attrs(*lists: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for lst in lists:
        for entry in lst or []:
            key = entry.get("key")
            if key is not None:
                out[key] = entry
    return list(out.values())


def _res_attrs(item: dict[str, Any]) -> list[dict[str, Any]]:
    return (item.get("resource", {}) or {}).get("attributes", []) or []


# --- Public dispatch ------------------------------------------------------- #


def map_otlp_envelope(envelope: dict[str, Any]) -> MapperResult:
    """Translate one receiver envelope into a MapperResult."""
    body = envelope.get("body") or {}
    signal = (envelope.get("signal") or "").removeprefix("v1/")
    result = MapperResult()
    if not isinstance(body, dict):
        return result
    if signal == "traces" or "resourceSpans" in body:
        for rs in body.get("resourceSpans", []) or []:
            res = _res_attrs(rs)
            for ss in rs.get("scopeSpans", []) or []:
                for sp in ss.get("spans", []) or []:
                    _apply_span(result, _merged_attrs(res, sp.get("attributes")), sp)
    elif signal == "logs" or "resourceLogs" in body:
        for rl in body.get("resourceLogs", []) or []:
            res = _res_attrs(rl)
            for sl in rl.get("scopeLogs", []) or []:
                for rec in sl.get("logRecords", []) or []:
                    _apply_log(result, _merged_attrs(res, rec.get("attributes")), rec)
    # /v1/metrics carries no structural Context Tree content in v1.
    return result


# --- Per-signal handlers --------------------------------------------------- #


def _set_correlation(result: MapperResult, attrs: list[dict[str, Any]]) -> None:
    result.session_id = (result.session_id
                         or _otlp_attr(attrs, "session.id")
                         or _otlp_attr(attrs, "claude_code.session.id"))
    result.prompt_id = result.prompt_id or _otlp_attr(attrs, "prompt.id")


def _apply_span(result: MapperResult, attrs: list[dict[str, Any]], span: dict[str, Any]) -> None:
    _set_correlation(result, attrs)
    request_id = _otlp_attr(attrs, "request_id") or _otlp_attr(attrs, "gen_ai.response.id")
    if request_id:
        result.request_id = request_id
    model = (_otlp_attr(attrs, "claude_code.model")
             or _otlp_attr(attrs, "gen_ai.request.model")
             or _otlp_attr(attrs, "model"))
    if model:
        result.runtime_state_updates["model"] = model
    for src, dst in (("claude_code.permission_mode", "permission_mode"),
                     ("claude_code.cwd", "cwd"),
                     ("gen_ai.system", "provider_name")):
        v = _otlp_attr(attrs, src)
        if v is not None:
            result.runtime_state_updates[dst] = v
    version = _otlp_attr(attrs, "claude_code.version") or _otlp_attr(attrs, "service.version")
    if version:
        result.system_layer_hint = {"static_core_ref": f"claude_code:{version}"}
    if span.get("name") == "claude_code.llm_request" and request_id:
        result.node_observation = {
            "transcript_uuid": request_id,
            "session_id": result.session_id,
            "prompt_id": result.prompt_id,
            "finish_reasons": _otlp_attr(attrs, "gen_ai.response.finish_reasons"),
        }


_LIFECYCLE_KEYS: dict[str, tuple[str, ...]] = {
    "plugin_loaded": ("plugin.name", "plugin.version", "plugin.scope", "enabled_via",
                      "has_hooks", "has_mcp", "skill_path_count", "command_path_count",
                      "agent_path_count", "marketplace.name"),
    "mcp_server_connection": ("server_name", "transport_type", "server_scope", "status",
                              "is_plugin", "plugin.name"),
    "hook_registered": ("hook_event", "hook_matcher", "hook_source", "hook_type",
                        "plugin.name", "plugin_id_hash"),
}
_LIFECYCLE_BUCKET: dict[str, tuple[str, str]] = {
    "plugin_loaded": ("tool_registry_updates", "plugins"),
    "mcp_server_connection": ("runtime_state_updates", "mcp_servers"),
    "hook_registered": ("runtime_state_updates", "hooks"),
}


def _event_name(attrs: list[dict[str, Any]], record: dict[str, Any]) -> str | None:
    name = _otlp_attr(attrs, "event.name")
    if name:
        return name
    body = record.get("body", {}) or {}
    body_str = body.get("stringValue") if isinstance(body, dict) else None
    if isinstance(body_str, str) and body_str.startswith("claude_code."):
        return body_str.removeprefix("claude_code.")
    return None


def _apply_log(result: MapperResult, attrs: list[dict[str, Any]], record: dict[str, Any]) -> None:
    _set_correlation(result, attrs)
    event = _event_name(attrs, record)
    if not event:
        return
    if event == "api_request_body":
        ref = _otlp_attr(attrs, "body_ref")
        if ref:
            result.raw_body_ref = ref
        rid = _otlp_attr(attrs, "request_id")
        if rid:
            result.request_id = rid
        return
    if event == "api_response_body":
        rid = _otlp_attr(attrs, "request_id") or _otlp_attr(attrs, "gen_ai.response.id")
        if rid:
            result.request_id = rid
        result.node_observation = {
            "transcript_uuid": rid,
            "session_id": result.session_id,
            "prompt_id": result.prompt_id,
            "response_body_ref": _otlp_attr(attrs, "body_ref"),
        }
        return
    keys = _LIFECYCLE_KEYS.get(event)
    if keys is None:
        return
    ev: dict[str, Any] = {"kind": event}
    for k in keys:
        ev[k] = _otlp_attr(attrs, k)
    result.lifecycle_event = ev
    bucket_attr, bucket_key = _LIFECYCLE_BUCKET[event]
    getattr(result, bucket_attr).setdefault(bucket_key, []).append(ev)


# --- Raw body decomposition (called by raw_body_watcher's pair callback) --- #


def map_raw_request_body(body: dict[str, Any]) -> dict[str, Any]:
    """Decompose one parsed Anthropic Messages API request body.

    Per OTel experiment gotcha #9 we preserve ``body.system`` order
    verbatim INCLUDING the billing-header block at index 0 (matches the
    HTTP proxy prototype so content hashes stay stable across sources).
    """
    if not isinstance(body, dict):
        return {"system": [], "messages": [], "tools": [], "runtime_params": {}}
    return {
        "system": list(body.get("system") or []),
        "messages": list(body.get("messages") or []),
        "tools": list(body.get("tools") or []),
        "runtime_params": {
            "max_tokens": body.get("max_tokens"),
            "stream": body.get("stream"),
            "temperature": body.get("temperature"),
            "top_p": body.get("top_p"),
            "top_k": body.get("top_k"),
            "metadata": body.get("metadata"),
            "model": body.get("model"),
        },
    }
