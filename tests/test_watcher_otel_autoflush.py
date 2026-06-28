"""Issue #158 B — zero-touch watcher auto-flush of OTel staging sessions.

Before this, nothing moved an OTel-captured session into the bucket without a
manual ``opentraces capture-otlp flush``. The watcher tick now does it: inside
the ``changed`` gate (a live Claude Code session also writes its JSONL, so the
tick is active + changed when OTel data is flowing) the tick enumerates the
project's OTel staging snapshots and flushes the ones whose snapshot advanced,
reusing the JSONL-minted ``trace_id`` so the OTel context lands on the same
trace spine.

These tests drive the tick's trace-trails runtime (the exact code path that
contains the auto-flush) against a synthetic per-llm-request staging snapshot
and assert context_* events land for the resolved trace_id WITHOUT a manual
flush, then assert a second changed tick with no new snapshot activity is a
no-op (the per-session watermark holds — no duplicate events).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from opentraces.capture.otlp.emitter import OTLPCaptureBuffer
from opentraces.core import paths as _paths
from opentraces.core.config import (
    get_project_dir,
    get_project_state_path,
    load_config,
    register_project,
    save_config,
)
from opentraces.core.context_tree import (
    CONTEXT_LAYER_CAPTURED,
    CONTEXT_NODE_OBSERVED,
)
from opentraces.core.context_tree.raw_blobs import deref_message_content
from opentraces.core.state import GenerationRecord, StateManager
from opentraces.core.trails.event_log import read_events
from opentraces.watcher import daemon

SESSION_ID = "9f8e7d6c-5b4a-3210-fedc-ba9876543210"
TRACE_ID = "watcher-autoflush-trace-0001"


def _attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, list):
        return {"key": key, "value": {"arrayValue": {
            "values": [{"stringValue": str(v)} for v in value]
        }}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _git_init(project_dir: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=project_dir, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "autoflush-test@opentraces.local"],
        cwd=project_dir, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "autoflush-test"],
        cwd=project_dir, check=True,
    )
    (project_dir / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=project_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=project_dir, check=True)


def _build_staging_snapshot(session_id: str, raw_dir: Path) -> dict[str, Any]:
    """Build a per-llm-request staging snapshot via the live receiver buffer.

    Mirrors the real ``api_request_body`` / ``api_response_body`` body-ref log
    events: one session, two turns, two llm-requests per turn => four nodes.
    Each request body carries a distinct user message so the per-step manifests
    (and thus messages-layer ids) differ. Returns the snapshot dict the
    foreground daemon would persist to the staging dir.
    """
    buffer = OTLPCaptureBuffer()
    # (prompt_id, request_stem, request_id, req_seq, resp_seq, user_text)
    specs = [
        ("turn-A", "uuidA1", "req_A1", 1, 2, "turn A request one"),
        ("turn-A", "uuidA2", "req_A2", 3, 4, "turn A request two"),
        ("turn-B", "uuidB1", "req_B1", 5, 6, "turn B request one"),
        ("turn-B", "uuidB2", "req_B2", 7, 8, "turn B request two"),
    ]

    def _log_env(event_name: str, attrs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "signal": "logs",
            "body": {"resourceLogs": [{
                "resource": {"attributes": [_attr("session.id", session_id)]},
                "scopeLogs": [{"logRecords": [{
                    "attributes": [_attr("event.name", event_name)] + attrs,
                }]}],
            }]},
        }

    for prompt_id, stem, rid, req_seq, resp_seq, text in specs:
        req_path = raw_dir / f"{stem}.request.json"
        req_path.write_text(json.dumps({
            "model": "claude-opus-4-7",
            "system": [{"type": "text", "text": f"system for {stem}"}],
            "messages": [{"role": "user", "content": text}],
            "tools": [{"name": "Read", "input_schema": {"type": "object",
                       "properties": {"file_path": {"type": "string"}}}}],
            "max_tokens": 64000, "stream": True,
        }))
        resp_path = raw_dir / f"{rid}.response.json"
        resp_path.write_text(json.dumps({
            "id": f"msg_{rid}", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100 + req_seq, "output_tokens": 5},
        }))
        buffer.handle_envelope(_log_env("api_request_body", [
            _attr("session.id", session_id),
            _attr("prompt.id", prompt_id),
            _attr("body_ref", str(req_path)),
            _attr("event.sequence", req_seq),
        ]))
        buffer.handle_envelope(_log_env("api_response_body", [
            _attr("session.id", session_id),
            _attr("prompt.id", prompt_id),
            _attr("body_ref", str(resp_path)),
            _attr("request_id", rid),
            _attr("event.sequence", resp_seq),
        ]))

    snap = buffer.snapshot_session(session_id)
    assert snap is not None
    assert len(snap["body_ref_events"]) == 8, snap["body_ref_events"]
    return snap


def _setup_project_with_session(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """Enlisted git project + a JSONL-minted trace_id + a staging snapshot."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _git_init(project_dir)

    cfg = load_config()
    register_project(cfg, project_dir)
    save_config(cfg)

    # Simulate JSONL ingest having minted + stored the session's trace_id.
    state = StateManager(state_path=get_project_state_path(project_dir))
    state.upsert_session(
        SESSION_ID, source_path=str(project_dir / "x.jsonl"),
        observed_size=1, observed_mtime=1.0,
    )
    state.append_generation(SESSION_ID, GenerationRecord(
        trace_id=TRACE_ID, generation=1, captured_size=1, captured_mtime=1.0,
        schema_version="0.5.0", security_version="0.6.0",
        status_at_capture="CAPTURED",
    ))

    # Drop the per-session OTel staging snapshot the daemon would have written.
    raw_dir = tmp_path / "raw-bodies"
    raw_dir.mkdir()
    snap = _build_staging_snapshot(SESSION_ID, raw_dir)
    staging = _paths.otel_staging_dir()
    staging.mkdir(parents=True, exist_ok=True)
    (staging / f"{SESSION_ID}.json").write_text(json.dumps(snap, default=str))
    return project_dir, snap


def _node_events(project_dir: Path):
    return [e for e in read_events(project_dir)
            if e.event_type == CONTEXT_NODE_OBSERVED]


def test_watcher_auto_flushes_otel_session_without_manual_flush(tmp_path: Path):
    project_dir, _snap = _setup_project_with_session(tmp_path)

    # Drive the tick's trace-trails runtime (active tick => changed) — the code
    # path that hosts the auto-flush. NO manual `capture-otlp flush` is called.
    report = daemon.TickReport(project_cwd=project_dir, started_at=daemon._utcnow())
    daemon._run_trace_trails_runtime(project_dir, report, force_maturation=True)

    assert report.otel_sessions_flushed == 1, vars(report)

    node_events = _node_events(project_dir)
    # 4 llm-requests => 4 nodes (per-llm-request, not 2 per-turn).
    assert len(node_events) == 4, [e.event_type for e in read_events(project_dir)]
    # Events carry the reused JSONL trace_id — the cross-substrate spine key.
    assert all(e.trace_id == TRACE_ID for e in node_events), node_events

    # The per-message text resolves through the raw/ content blobs (round-trip).
    slug = get_project_dir(project_dir).name
    messages_layers = [
        e.payload for e in read_events(project_dir)
        if e.event_type == CONTEXT_LAYER_CAPTURED
        and e.payload.get("layer_type") == "messages"
    ]
    derefs = 0
    for layer in messages_layers:
        for entry in (layer.get("content") or {}).get("messages", []):
            ch = entry.get("content_hash")
            assert ch
            raw = deref_message_content(slug, ch)
            assert raw is not None, ch
            assert "sha256:" + hashlib.sha256(raw).hexdigest() == ch
            derefs += 1
    assert derefs == 4, derefs


def test_watcher_auto_flush_second_tick_is_noop(tmp_path: Path):
    project_dir, _snap = _setup_project_with_session(tmp_path)

    report1 = daemon.TickReport(project_cwd=project_dir, started_at=daemon._utcnow())
    daemon._run_trace_trails_runtime(project_dir, report1, force_maturation=True)
    assert report1.otel_sessions_flushed == 1, vars(report1)
    first_count = len(_node_events(project_dir))
    assert first_count == 4

    # Second changed tick, snapshot unchanged: the per-session watermark must
    # skip the flush — no second flush, no duplicate context events.
    report2 = daemon.TickReport(project_cwd=project_dir, started_at=daemon._utcnow())
    daemon._run_trace_trails_runtime(project_dir, report2, force_maturation=True)
    assert report2.otel_sessions_flushed == 0, vars(report2)
    assert len(_node_events(project_dir)) == first_count

    # The watermark file records the session signature for next-tick skipping.
    wm_path = daemon._otel_autoflush_watermark_path(project_dir)
    assert wm_path is not None and wm_path.is_file()
    wm = json.loads(wm_path.read_text())
    assert SESSION_ID in wm, wm


def test_watcher_run_once_auto_flushes_end_to_end(tmp_path: Path):
    """The real ``run_once`` tick entry auto-flushes on an active tick.

    A fresh enlisted repo with an unbackfilled commit is an active tick, which
    always invokes the trace-trails runtime; the OTel session is flushed with
    no manual ``capture-otlp flush``.
    """
    project_dir, _snap = _setup_project_with_session(tmp_path)

    report = daemon.run_once(project_dir)
    assert report.error is None, report.error
    assert report.otel_sessions_flushed == 1, vars(report)

    node_events = _node_events(project_dir)
    assert len(node_events) == 4
    assert all(e.trace_id == TRACE_ID for e in node_events)
