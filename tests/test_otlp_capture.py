"""Integration test for plan 078's OTLP capture source.

Drives the public callback chain (receiver -> mapper -> buffer ->
emitter -> append_event_batch -> Git event log) end-to-end against
sanitized OTel envelopes from the experiment fixture. Asserts:

* the receiver accepts OTLP/HTTP+JSON envelopes,
* the mapper populates ContextLayer / ContextNode drafts,
* the emitter writes context_* events on
  ``refs/opentraces/local/events/v1`` with
  ``capture_method=["otel"]``,
* the four canonical event types land in the expected order,
* the raw-body watcher pairs request/response files and folds
  byte-perfect Anthropic Messages API content into the substrate.

The test is hermetic: it uses a tmp Git repo, a tmp raw-bodies dir,
and an in-process receiver bound to an ephemeral port.
"""
from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from opentraces.capture.otlp import OTLPReceiver, start_receiver
from opentraces.capture.otlp.emitter import (
    OTLPCaptureBuffer,
    flush_session_to_project,
)
from opentraces.capture.otlp.raw_body_watcher import RawBodyWatcher
from opentraces.core.context_tree import (
    CAPTURE_METHOD_VALUES,
    CONTEXT_LAYER_CAPTURED,
    CONTEXT_NODE_OBSERVED,
    CONTEXT_TREE_RECONCILED,
)
from opentraces.core.trails.event_log import read_events


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Initialise a tmp Git repo so append_event_batch has refs/."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=project_dir, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "otel-test@opentraces.local"],
        cwd=project_dir, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "otel-test"],
        cwd=project_dir, check=True,
    )
    # need at least one commit so refs/heads/main exists
    (project_dir / "README.md").write_text("seed\n")
    subprocess.run(
        ["git", "add", "README.md"], cwd=project_dir, check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"], cwd=project_dir, check=True,
    )
    return project_dir


@pytest.fixture
def raw_bodies_dir(tmp_path: Path) -> Path:
    d = tmp_path / "raw-bodies"
    d.mkdir()
    return d


@pytest.fixture
def buffer() -> OTLPCaptureBuffer:
    return OTLPCaptureBuffer()


@pytest.fixture
def receiver(buffer: OTLPCaptureBuffer, raw_bodies_dir: Path):
    port = _free_port()
    r = start_receiver(port=port, bind="127.0.0.1", raw_bodies_dir=raw_bodies_dir)
    r.set_callback(buffer.handle_envelope)
    assert r.wait_until_ready(timeout=5.0)
    yield r
    r.shutdown()


# --------------------------------------------------------------------------- #
# Sample OTel envelopes (minimal, matching real Claude Code shapes)
# --------------------------------------------------------------------------- #


def _attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    if isinstance(value, list):
        return {"key": key, "value": {"arrayValue": {
            "values": [{"stringValue": str(v)} for v in value]
        }}}
    return {"key": key, "value": {"stringValue": str(value)}}


SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"
PROMPT_ID = "prompt-aaaaaaaa-0000-0000-0000-000000000001"
REQUEST_ID = "req_test_0001"


SPAN_ENVELOPE = {
    "resourceSpans": [{
        "resource": {"attributes": [_attr("session.id", SESSION_ID)]},
        "scopeSpans": [{
            "spans": [{
                "name": "claude_code.llm_request",
                "attributes": [
                    _attr("session.id", SESSION_ID),
                    _attr("prompt.id", PROMPT_ID),
                    _attr("request_id", REQUEST_ID),
                    _attr("gen_ai.system", "anthropic"),
                    _attr("gen_ai.request.model", "claude-opus-4-7"),
                    _attr("gen_ai.response.id", PROMPT_ID),
                    _attr("gen_ai.response.finish_reasons", ["end_turn"]),
                    _attr("model", "claude-opus-4-7"),
                    _attr("input_tokens", 1234),
                    _attr("output_tokens", 56),
                ],
            }],
        }],
    }],
}


PLUGIN_LOG_ENVELOPE = {
    "resourceLogs": [{
        "resource": {"attributes": [_attr("session.id", SESSION_ID)]},
        "scopeLogs": [{
            "logRecords": [{
                "attributes": [
                    _attr("event.name", "plugin_loaded"),
                    _attr("session.id", SESSION_ID),
                    _attr("plugin.name", "test-plugin"),
                    _attr("plugin.version", "1.0.0"),
                    _attr("plugin.scope", "project"),
                    _attr("enabled_via", "marketplace"),
                    _attr("has_hooks", True),
                    _attr("has_mcp", False),
                    _attr("skill_path_count", 2),
                    _attr("command_path_count", 1),
                    _attr("agent_path_count", 0),
                    _attr("marketplace.name", "test-market"),
                ],
            }],
        }],
    }],
}


MCP_LOG_ENVELOPE = {
    "resourceLogs": [{
        "resource": {"attributes": [_attr("session.id", SESSION_ID)]},
        "scopeLogs": [{
            "logRecords": [{
                "attributes": [
                    _attr("event.name", "mcp_server_connection"),
                    _attr("session.id", SESSION_ID),
                    _attr("server_name", "test-mcp"),
                    _attr("transport_type", "stdio"),
                    _attr("server_scope", "user"),
                    _attr("status", "connected"),
                    _attr("is_plugin", False),
                ],
            }],
        }],
    }],
}


HOOK_LOG_ENVELOPE = {
    "resourceLogs": [{
        "resource": {"attributes": [_attr("session.id", SESSION_ID)]},
        "scopeLogs": [{
            "logRecords": [{
                "attributes": [
                    _attr("event.name", "hook_registered"),
                    _attr("session.id", SESSION_ID),
                    _attr("hook_event", "PreToolUse"),
                    _attr("hook_matcher", "Bash"),
                    _attr("hook_source", "user"),
                    _attr("hook_type", "command"),
                    _attr("plugin.name", "test-plugin"),
                    _attr("plugin_id_hash", "ab12cd"),
                ],
            }],
        }],
    }],
}


def _post(port: int, path: str, body: dict[str, Any]) -> int:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        return resp.status


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_otel_capture_method_in_vocabulary():
    """Plan 078 substrate widening: 'otel' must be in the frozen set."""
    assert "otel" in CAPTURE_METHOD_VALUES


def test_receiver_accepts_and_routes(receiver: OTLPReceiver, buffer: OTLPCaptureBuffer):
    """End-to-end: POST envelopes -> mapper -> buffer accumulates."""
    assert _post(receiver.port, "/v1/traces", SPAN_ENVELOPE) == 200
    assert _post(receiver.port, "/v1/logs", PLUGIN_LOG_ENVELOPE) == 200
    assert _post(receiver.port, "/v1/logs", MCP_LOG_ENVELOPE) == 200
    assert _post(receiver.port, "/v1/logs", HOOK_LOG_ENVELOPE) == 200

    # The receiver thread routes async; give it a tick.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if SESSION_ID in buffer.session_ids():
            break
        time.sleep(0.05)
    assert SESSION_ID in buffer.session_ids(), buffer.session_ids()
    snap = buffer.snapshot_session(SESSION_ID)
    assert snap is not None
    assert len(snap["plugins"]) == 1
    assert snap["plugins"][0]["plugin.name"] == "test-plugin"
    assert len(snap["mcp_servers"]) == 1
    assert snap["mcp_servers"][0]["server_name"] == "test-mcp"
    assert len(snap["hooks"]) == 1
    assert snap["hooks"][0]["hook_event"] == "PreToolUse"
    assert PROMPT_ID in snap["nodes_by_prompt"]


def test_flush_emits_context_events(
    receiver: OTLPReceiver,
    buffer: OTLPCaptureBuffer,
    tmp_project: Path,
):
    """Receiver -> buffer -> flush -> TrailEvents on refs/opentraces/local/events/v1."""
    # Populate the buffer.
    _post(receiver.port, "/v1/traces", SPAN_ENVELOPE)
    _post(receiver.port, "/v1/logs", PLUGIN_LOG_ENVELOPE)
    _post(receiver.port, "/v1/logs", MCP_LOG_ENVELOPE)
    _post(receiver.port, "/v1/logs", HOOK_LOG_ENVELOPE)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if SESSION_ID in buffer.session_ids():
            break
        time.sleep(0.05)
    assert SESSION_ID in buffer.session_ids()

    trace_id = "trace-test-0001"
    report = flush_session_to_project(
        project_dir=tmp_project,
        trace_id=trace_id,
        session_id=SESSION_ID,
        buffer=buffer,
    )
    assert report["ok"], report
    assert report["nodes_count"] >= 1
    assert report["layers_count"] == 4  # system, messages, tool_registry, runtime_state

    # The event log on the project's Git ref must now contain our events.
    events = read_events(tmp_project)
    types = [e.event_type for e in events]
    assert CONTEXT_LAYER_CAPTURED in types
    assert CONTEXT_NODE_OBSERVED in types
    assert CONTEXT_TREE_RECONCILED in types
    # Every emitted event must carry capture_method=["otel"].
    for e in events:
        if e.event_type in {
            CONTEXT_LAYER_CAPTURED,
            CONTEXT_NODE_OBSERVED,
            CONTEXT_TREE_RECONCILED,
        }:
            assert e.capture_method == ["otel"], (
                f"event {e.event_type} has capture_method={e.capture_method}"
            )
    # Exactly 4 layer events (one per layer type), at least 1 node,
    # exactly 1 reconciled sentinel.
    layer_events = [e for e in events if e.event_type == CONTEXT_LAYER_CAPTURED]
    node_events = [e for e in events if e.event_type == CONTEXT_NODE_OBSERVED]
    reconciled = [e for e in events if e.event_type == CONTEXT_TREE_RECONCILED]
    assert len(layer_events) == 4
    assert len(node_events) >= 1
    assert len(reconciled) == 1
    # The reconciled sentinel must name the OTel source.
    assert reconciled[0].payload.get("source") == "otel"
    assert reconciled[0].payload.get("session_id") == SESSION_ID


def test_raw_body_pairing_into_buffer(
    raw_bodies_dir: Path, buffer: OTLPCaptureBuffer,
):
    """Watcher pairs *.request.json + *.response.json and feeds buffer."""
    # Seed a fake node draft so the watcher's pair-by-request_id finds it.
    buffer.handle_envelope({
        "signal": "traces",
        "body": SPAN_ENVELOPE,
        "received_at": time.time(),
    })
    assert SESSION_ID in buffer.session_ids()

    watcher = RawBodyWatcher(raw_bodies_dir, buffer.handle_body_pair)
    watcher.start()
    try:
        # Drop a synthetic Anthropic Messages API request/response pair.
        (raw_bodies_dir / f"{REQUEST_ID}.request.json").write_text(json.dumps({
            "system": [
                {"type": "text", "text": "x-anthropic-billing-header: cc_version=test"},
                {"type": "text", "text": "You are Claude Code."},
            ],
            "messages": [
                {"role": "user", "content": "hi"},
            ],
            "tools": [
                {"name": "Read", "input_schema": {"type": "object",
                 "properties": {"file_path": {"type": "string"}}}},
                {"name": "Bash", "input_schema": {"type": "object",
                 "properties": {"command": {"type": "string"}}}},
            ],
            "max_tokens": 64000,
            "stream": True,
            "model": "claude-opus-4-7",
            "metadata": {"user_id": "test"},
        }))
        (raw_bodies_dir / f"{REQUEST_ID}.response.json").write_text(json.dumps({
            "id": PROMPT_ID,
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 5, "output_tokens": 1},
            "stop_reason": "end_turn",
        }))

        # Watcher polls at 200ms + 200ms stability window.
        deadline = time.time() + 3.0
        while time.time() < deadline:
            snap = buffer.snapshot_session(SESSION_ID)
            if snap and snap["raw_bodies_by_request"]:
                break
            time.sleep(0.1)
        snap = buffer.snapshot_session(SESSION_ID)
        assert snap is not None
        assert REQUEST_ID in snap["raw_bodies_by_request"], snap["raw_bodies_by_request"]
        decomposed = snap["raw_bodies_by_request"][REQUEST_ID]["decomposed"]
        # Byte-perfect content preserved.
        assert isinstance(decomposed["system"], list)
        assert len(decomposed["system"]) == 2
        assert "billing-header" in decomposed["system"][0]["text"]
        assert decomposed["tools"][0]["name"] == "Read"
        assert decomposed["runtime_params"]["max_tokens"] == 64000
        assert decomposed["runtime_params"]["stream"] is True
    finally:
        watcher.shutdown()


def test_multi_prompt_partial_bodies_label_honestly(
    receiver: OTLPReceiver,
    buffer: OTLPCaptureBuffer,
    raw_bodies_dir: Path,
    tmp_project: Path,
):
    """Plan 078 OJ HIGH #5: when a session has N prompts but only K
    raw bodies, the K prompts get full layers, the (N-K) prompts get
    approximated layers. No layer is silently overclaimed."""
    # First prompt: paired raw body (gets full).
    _post(receiver.port, "/v1/traces", SPAN_ENVELOPE)
    # Second prompt: span only, no raw body (gets approximated).
    second_envelope = {
        "resourceSpans": [{
            "resource": {"attributes": [_attr("session.id", SESSION_ID)]},
            "scopeSpans": [{
                "spans": [{
                    "name": "claude_code.llm_request",
                    "attributes": [
                        _attr("session.id", SESSION_ID),
                        _attr("prompt.id", "prompt-aaaaaaaa-0000-0000-0000-000000000002"),
                        _attr("request_id", "req_test_no_body"),
                        _attr("gen_ai.response.id", "prompt-aaaaaaaa-0000-0000-0000-000000000002"),
                    ],
                }],
            }],
        }],
    }
    _post(receiver.port, "/v1/traces", second_envelope)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        snap = buffer.snapshot_session(SESSION_ID)
        if snap and len(snap["nodes_by_prompt"]) == 2:
            break
        time.sleep(0.05)

    watcher = RawBodyWatcher(raw_bodies_dir, buffer.handle_body_pair)
    watcher.start()
    try:
        (raw_bodies_dir / f"{REQUEST_ID}.request.json").write_text(json.dumps({
            "system": [{"type": "text", "text": "first-prompt system"}],
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
            "max_tokens": 64000, "stream": True, "model": "claude-opus-4-7",
        }))
        (raw_bodies_dir / f"{REQUEST_ID}.response.json").write_text(json.dumps({
            "id": PROMPT_ID, "content": [], "stop_reason": "end_turn",
        }))
        deadline = time.time() + 3.0
        while time.time() < deadline:
            snap = buffer.snapshot_session(SESSION_ID)
            if snap and snap["raw_bodies_by_request"]:
                break
            time.sleep(0.1)

        report = flush_session_to_project(
            project_dir=tmp_project,
            trace_id="trace-mixed-honesty",
            session_id=SESSION_ID,
            buffer=buffer,
        )
        assert report["ok"]
        assert report["nodes_count"] == 2

        events = read_events(tmp_project)
        node_events = [e for e in events if e.event_type == CONTEXT_NODE_OBSERVED]
        completeness_per_step = [
            (e.payload["step_index"], e.payload["capture_completeness"])
            for e in node_events
        ]
        completeness_per_step.sort()
        # Step 0 paired with raw body -> full; step 1 unpaired -> approximated.
        # OJ's HIGH #5 fix: no silent overclaim of "full" for layers we don't have evidence for.
        assert completeness_per_step == [(0, "full"), (1, "approximated")], (
            completeness_per_step
        )
    finally:
        watcher.shutdown()


def test_jsonl_otel_messages_layer_content_hash_equivalence(
    receiver: OTLPReceiver,
    buffer: OTLPCaptureBuffer,
    raw_bodies_dir: Path,
):
    """Plan 078 R7 + OJ MEDIUM #8: when JSONL and OTLP capture the same
    message history, the messages-layer content hash matches across
    sources. Content-addressed identity is the substrate's dedup key.

    Drives the OTLP path with one raw body, then synthesizes the
    equivalent JSONL-shaped messages layer via the substrate's own
    ``build_layer``, and asserts the layer_ids are identical.
    """
    from opentraces.core.context_tree import build_layer

    same_messages = [
        {"role": "user", "content": "what does foo.py do?"},
        {"role": "assistant", "content": [{"type": "text", "text": "It returns 42."}]},
    ]

    # OTel path: post a span + pair a raw body containing these messages.
    _post(receiver.port, "/v1/traces", SPAN_ENVELOPE)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if SESSION_ID in buffer.session_ids():
            break
        time.sleep(0.05)

    watcher = RawBodyWatcher(raw_bodies_dir, buffer.handle_body_pair)
    watcher.start()
    try:
        (raw_bodies_dir / f"{REQUEST_ID}.request.json").write_text(json.dumps({
            "system": [], "messages": same_messages, "tools": [],
            "max_tokens": 64000, "stream": True, "model": "claude-opus-4-7",
        }))
        (raw_bodies_dir / f"{REQUEST_ID}.response.json").write_text(json.dumps({
            "id": PROMPT_ID, "content": [], "stop_reason": "end_turn",
        }))
        deadline = time.time() + 3.0
        while time.time() < deadline:
            snap = buffer.snapshot_session(SESSION_ID)
            if snap and snap["raw_bodies_by_request"]:
                break
            time.sleep(0.1)

        snap = buffer.snapshot_session(SESSION_ID)
        otel_messages_layer = build_layer(
            layer_type="messages",
            content={"messages": same_messages, "finish_reasons": []},
            completeness="full",
            capture_method="otel",
        )

        # JSONL path: same messages, same shape, different capture_method.
        jsonl_messages_layer = build_layer(
            layer_type="messages",
            content={"messages": same_messages, "finish_reasons": []},
            completeness="full",
            capture_method="transcript_reconstruction",
        )

        # Layer IDs are computed over (type, content, completeness, capture_method).
        # capture_method participates in the hash, so layer_ids differ — that's
        # by design (consumers can dedup or filter as needed). But the
        # *content* hashes (the messages list payload) must be byte-identical:
        # consumers join cross-source layers by comparing layer.content payloads.
        from opentraces.core.context_tree import sha256_hex
        otel_content_hash = sha256_hex(otel_messages_layer.content)
        jsonl_content_hash = sha256_hex(jsonl_messages_layer.content)
        assert otel_content_hash == jsonl_content_hash
    finally:
        watcher.shutdown()


def test_full_chain_layers_have_full_completeness(
    receiver: OTLPReceiver,
    buffer: OTLPCaptureBuffer,
    raw_bodies_dir: Path,
    tmp_project: Path,
):
    """When raw bodies are paired, system + messages + tool_registry layers
    are ``completeness=full`` (R13 honest gap labeling closed via OTLP)."""
    _post(receiver.port, "/v1/traces", SPAN_ENVELOPE)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if SESSION_ID in buffer.session_ids():
            break
        time.sleep(0.05)

    # Wire the watcher so the raw bodies are picked up too.
    watcher = RawBodyWatcher(raw_bodies_dir, buffer.handle_body_pair)
    watcher.start()
    try:
        (raw_bodies_dir / f"{REQUEST_ID}.request.json").write_text(json.dumps({
            "system": [{"type": "text", "text": "You are Claude Code."}],
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
            "max_tokens": 64000,
            "stream": True,
            "model": "claude-opus-4-7",
        }))
        (raw_bodies_dir / f"{REQUEST_ID}.response.json").write_text(json.dumps({
            "id": PROMPT_ID, "content": [], "usage": {}, "stop_reason": "end_turn",
        }))

        deadline = time.time() + 3.0
        while time.time() < deadline:
            snap = buffer.snapshot_session(SESSION_ID)
            if snap and snap["raw_bodies_by_request"]:
                break
            time.sleep(0.1)

        report = flush_session_to_project(
            project_dir=tmp_project,
            trace_id="trace-full-test",
            session_id=SESSION_ID,
            buffer=buffer,
        )
        assert report["ok"], report

        events = read_events(tmp_project)
        layer_events = [e for e in events if e.event_type == CONTEXT_LAYER_CAPTURED]
        by_type = {e.payload["layer_type"]: e.payload for e in layer_events}
        # The static-core / schema gaps are closed via raw bodies.
        assert by_type["system"]["completeness"] == "full"
        assert by_type["system"]["capture_method"] == "otel"
        assert by_type["messages"]["completeness"] == "full"
        assert by_type["tool_registry"]["completeness"] == "full"
        # runtime_state requires a model field; we supplied one in raw body.
        assert by_type["runtime_state"]["completeness"] == "full"
    finally:
        watcher.shutdown()


def test_live_body_ref_events_produce_per_llm_request_nodes(
    buffer: OTLPCaptureBuffer,
    raw_bodies_dir: Path,
    tmp_project: Path,
):
    """Issue #158 W3: the live snapshot path keys nodes per-llm-request via the
    OTel ``api_*_body`` body-ref log events — NOT per-turn ``prompt.id``.

    One session, TWO prompt.ids, each with TWO (api_request_body,
    api_response_body) pairs => 4 llm-requests => 4 ContextNodes. Each request
    body carries distinct messages so the per-step messages manifests differ,
    and every manifest ``content_hash`` derefs to its text via the raw/ blob.
    """
    import hashlib

    from opentraces.capture.otlp.emitter import _project_slug_for
    from opentraces.core.context_tree.raw_blobs import deref_message_content

    session_id = "11111111-2222-3333-4444-555555555555"

    def _log_env(event_name: str, attrs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "signal": "logs",
            "received_at": time.time(),
            "body": {"resourceLogs": [{
                "resource": {"attributes": [_attr("session.id", session_id)]},
                "scopeLogs": [{"logRecords": [{
                    "attributes": [_attr("event.name", event_name)] + attrs,
                }]}],
            }]},
        }

    # 4 llm-requests across 2 turns, each with distinct user messages so the
    # per-step manifests (and thus messages-layer ids) differ.
    # (prompt_id, request_stem, request_id, req_seq, resp_seq, user_text)
    specs = [
        ("turn-A", "uuidA1", "req_A1", 1, 2, "turn A request one"),
        ("turn-A", "uuidA2", "req_A2", 3, 4, "turn A request two"),
        ("turn-B", "uuidB1", "req_B1", 5, 6, "turn B request one"),
        ("turn-B", "uuidB2", "req_B2", 7, 8, "turn B request two"),
    ]
    for prompt_id, stem, rid, req_seq, resp_seq, text in specs:
        req_path = raw_bodies_dir / f"{stem}.request.json"
        req_path.write_text(json.dumps({
            "model": "claude-opus-4-7",
            "system": [{"type": "text", "text": f"system for {stem}"}],
            "messages": [{"role": "user", "content": text}],
            "tools": [{"name": "Read", "input_schema": {"type": "object",
                       "properties": {"file_path": {"type": "string"}}}}],
            "max_tokens": 64000, "stream": True,
        }))
        resp_path = raw_bodies_dir / f"{rid}.response.json"
        resp_path.write_text(json.dumps({
            "id": f"msg_{rid}", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100 + req_seq, "output_tokens": 5},
        }))
        # api_request_body carries body_ref (request file) + sequence, NO request_id.
        buffer.handle_envelope(_log_env("api_request_body", [
            _attr("session.id", session_id),
            _attr("prompt.id", prompt_id),
            _attr("body_ref", str(req_path)),
            _attr("event.sequence", req_seq),
        ]))
        # api_response_body carries body_ref (response file) + request_id + sequence.
        buffer.handle_envelope(_log_env("api_response_body", [
            _attr("session.id", session_id),
            _attr("prompt.id", prompt_id),
            _attr("body_ref", str(resp_path)),
            _attr("request_id", rid),
            _attr("event.sequence", resp_seq),
        ]))

    snap = buffer.snapshot_session(session_id)
    assert snap is not None
    # 8 body-ref log events accumulated (4 request + 4 response).
    assert len(snap["body_ref_events"]) == 8, snap["body_ref_events"]

    report = flush_session_to_project(
        project_dir=tmp_project,
        trace_id="trace-bodyref-0001",
        session_id=session_id,
        snapshot=snap,
        steps=None,
        raw_bodies_dir=raw_bodies_dir,
    )
    assert report["ok"], report
    # 4 llm-requests => 4 nodes, NOT 2 per-prompt nodes (the per-turn bug).
    assert report["nodes_count"] == 4, report

    events = read_events(tmp_project)
    node_events = [e for e in events if e.event_type == CONTEXT_NODE_OBSERVED]
    assert len(node_events) == 4

    # Per-step messages manifests differ: each node points at a distinct
    # (content-addressed) messages layer.
    msg_layer_ids = {e.payload["messages_layer_id"] for e in node_events}
    assert len(msg_layer_ids) == 4, msg_layer_ids

    # Every manifest content_hash derefs to its text via the raw/ blob, and the
    # round-trip holds (sha256 of the stored bytes reproduces the content_hash).
    slug = _project_slug_for(tmp_project)
    messages_layers = [
        e.payload for e in events
        if e.event_type == CONTEXT_LAYER_CAPTURED
        and e.payload.get("layer_type") == "messages"
    ]
    assert len(messages_layers) == 4
    checked = 0
    for layer in messages_layers:
        for entry in (layer.get("content") or {}).get("messages", []):
            ch = entry.get("content_hash")
            assert ch
            raw = deref_message_content(slug, ch)
            assert raw is not None, ch
            assert "sha256:" + hashlib.sha256(raw).hexdigest() == ch
            assert len(raw.decode("utf-8").strip()) > 2
            checked += 1
    assert checked == 4  # one distinct user message per step


def test_install_autostart_uses_shim_and_threads_flags(tmp_path, monkeypatch):
    """0.4.5: the launchd/systemd unit points at a SHIM (not the binary), so an
    unsigned binary loads on Ventura+ with NO signing gate; port/bind/raw-dir are
    baked into the shim. Also pins the 0.4.4 regression — passing port= must not
    raise (the old signature didn't accept it).
    """
    from opentraces.capture.otlp import lifecycle as L

    plist = tmp_path / "rec.plist"
    shim = tmp_path / "bin" / "ot-otlp-receiver"
    monkeypatch.setattr(L, "LAUNCHD_PLIST_PATH", plist)
    monkeypatch.setattr(L, "SHIM_PATH", shim)
    monkeypatch.setattr(L, "_plat", lambda: "darwin")
    monkeypatch.setattr(L, "_run", lambda cmd, timeout=10: (0, ""))
    binp = tmp_path / "opentraces"
    binp.write_text("#!/bin/sh\n")
    binp.chmod(0o755)

    res = L.install_autostart(
        opentraces_binary=binp,
        log_dir=tmp_path / "logs",
        port=4319,
        bind="0.0.0.0",
        raw_bodies_dir=tmp_path / "raw",
    )
    assert res.ok, res
    # The unit programs the SHIM, not the binary (so launchd runs /bin/sh, signed).
    plist_body = plist.read_text()
    assert str(shim) in plist_body
    assert "ProgramArguments" in plist_body
    import os
    assert shim.exists() and os.access(shim, os.X_OK)
    # The shim carries the knobs and resolves the CLI at run time.
    shim_body = shim.read_text()
    assert "--port 4319" in shim_body
    assert "--bind 0.0.0.0" in shim_body
    assert "--raw-bodies-dir" in shim_body and str(tmp_path / "raw") in shim_body
    assert "capture-otlp start --help" in shim_body  # verb verification

    # No-knob install (the integration-repair caller) stays default + clean.
    res2 = L.install_autostart(opentraces_binary=binp, log_dir=tmp_path / "logs")
    assert res2.ok, res2
    shim_body2 = shim.read_text()
    assert "--foreground" in shim_body2
    assert "--port" not in shim_body2 and "--bind" not in shim_body2


def test_setup_capture_otlp_reports_autostart_failure_honestly(tmp_path, monkeypatch, capsys):
    """0.4.5 fix: when install_autostart returns gracefully with ok=False (e.g.
    an unsigned binary on macOS Ventura+), ``setup capture-otlp`` must NOT report
    'installed (OK)'. Before this fix, success was inferred from "no exception",
    so a skipped autostart was misreported as installed.
    """
    import json as _json
    from types import SimpleNamespace

    from opentraces.cli import capture_otlp as C
    from opentraces.capture.otlp import lifecycle as L
    from opentraces.capture.otlp import settings_patcher as S

    monkeypatch.setattr(
        L, "install_autostart",
        lambda **kw: L.InstallResult(
            ok=False, platform="darwin",
            reason="unsigned-binary-on-ventura-plus",
            details="macOS Ventura+ requires signed binaries for LaunchAgents.",
        ),
    )
    monkeypatch.setattr(
        S, "install_otel_env",
        lambda **kw: SimpleNamespace(
            ok=True,
            settings_path=tmp_path / "settings.json",
            backup_path=tmp_path / "settings.json.opentraces-backup",
            keys_added=["CLAUDE_CODE_ENABLE_TELEMETRY"],
            keys_skipped=[],
        ),
    )

    C._setup_capture_otlp_impl(
        raw_bodies_dir=tmp_path / "raw", no_autostart=False, as_json=True
    )
    out = capsys.readouterr().out
    payload = _json.loads(out[out.find("{"):])
    assert payload["autostart_installed"] is False
    assert payload["autostart"]["ok"] is False
    assert payload["autostart"]["reason"] == "unsigned-binary-on-ventura-plus"
    assert payload["next_command"] == "opentraces capture-otlp start"
