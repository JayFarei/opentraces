"""Public contract tests for portable Capture orchestration (A3)."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

from opentraces.capture import Capture, CapturePlan


def _git_project(root: Path) -> Path:
    root.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "capture-test@opentraces.local"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "capture-test"],
        cwd=root,
        check=True,
    )
    (root / "README.md").write_text("capture fixture\n", encoding="utf-8")
    (root / ".opentraces.json").write_text(
        json.dumps(
            {
                "marker_version": "2",
                "project_id": "portable-capture-test",
                "review_policy": "review",
                "push_policy": "manual",
                "remotes": {
                    "origin": {"url": "test/test", "visibility": "private"}
                },
                "active_remote": "origin",
                "agents": ["claude-code"],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "README.md", ".opentraces.json"], cwd=root, check=True
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "seed"],
        cwd=root,
        check=True,
    )
    return root


def _write_session(project: Path, session_id: str) -> Path:
    session = project / f"{session_id}.jsonl"
    rows = [
        {
            "type": "user",
            "sessionId": session_id,
            "timestamp": "2026-07-13T10:00:00Z",
            "message": {
                "role": "user",
                "content": "Read the project file and report what it contains.",
            },
        },
        {
            "type": "assistant",
            "sessionId": session_id,
            "timestamp": "2026-07-13T10:00:01Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-read-1",
                        "name": "Read",
                        "input": {"file_path": str(project / "README.md")},
                    }
                ],
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
        },
        {
            "type": "user",
            "sessionId": session_id,
            "timestamp": "2026-07-13T10:00:02Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-read-1",
                        "content": "capture fixture",
                    }
                ],
            },
        },
    ]
    session.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return session


def test_killed_required_source_is_persisted_as_partial_before_deadline(
    tmp_path: Path,
) -> None:
    """A real killed source can never be reported as a thinner complete."""
    project = _git_project(tmp_path / "project")
    result_dir = tmp_path / "capture-result"
    session = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("telemetry",),
            required_sources=("telemetry",),
            observer_version="0.4.9-observer",
            product_under_test_version="0.4.8-product",
            result_dir=result_dir,
        )
    )

    assert session.bindings.otlp_endpoint.startswith("http://127.0.0.1:")
    assert session.interrupt("telemetry") is True

    started = time.monotonic()
    result = session.finish(deadline=started + 1.0)

    assert time.monotonic() - started < 1.5
    assert result.completeness == "partial"
    assert result.source("telemetry").status == "unavailable"
    assert result.source("telemetry").completeness == "missing"
    assert result.view("model_boundary").completeness == "missing"
    assert "source process exited" in result.source("telemetry").limitations[0]
    assert result.observer_version == "0.4.9-observer"
    assert result.product_under_test_version == "0.4.8-product"

    frozen = json.loads((result_dir / "capture_result.json").read_text())
    assert frozen == result.to_dict()


def test_persistent_capture_owns_ingest_and_bucket_projection(tmp_path: Path) -> None:
    """The caller asks for sources; finish owns their complete write chain."""
    project = _git_project(tmp_path / "project")
    source = _write_session(project, "portable-session")

    result = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="persistent",
            requested_sources=("session_jsonl", "bucket"),
            required_sources=("session_jsonl", "bucket"),
            actor="claude-code",
            session_id="portable-session",
            session_path=source,
            observer_version="observer-pin",
            product_under_test_version="product-pin",
            result_dir=tmp_path / "persistent-result",
        )
    ).finish(deadline=time.monotonic() + 10.0)

    assert result.completeness == "complete"
    assert result.source("session_jsonl").status == "finalized"
    assert result.source("session_jsonl").completeness == "full"
    assert result.source("bucket").status == "finalized"
    assert result.source("bucket").completeness == "full"
    assert result.view("harness").completeness == "full"
    assert result.view("world_effects").completeness == "full"
    assert len(result.trace_refs) == 1

    trace_path = Path(result.source("bucket").details["trace_path"])
    assert trace_path.is_file()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["trace_id"] == result.trace_refs[0]
    assert result.source("bucket").details["security"] == trace["security"]
    assert "scanned" in trace["security"]


def test_elapsed_deadline_never_turns_requested_sources_complete(tmp_path: Path) -> None:
    project = _git_project(tmp_path / "project")

    result = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="persistent",
            requested_sources=("watcher", "git", "bucket"),
            required_sources=("watcher", "git", "bucket"),
            observer_version="observer-pin",
            product_under_test_version="product-pin",
            result_dir=tmp_path / "deadline-result",
        )
    ).finish(deadline=time.monotonic() - 0.001)

    assert result.completeness == "partial"
    assert {source.status for source in result.sources} == {"timed_out"}
    assert all(source.completeness == "missing" for source in result.sources)


def test_leased_telemetry_finalizes_through_the_canonical_event_writer(
    tmp_path: Path,
) -> None:
    project = _git_project(tmp_path / "project")
    session_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    capture = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("telemetry",),
            required_sources=("telemetry",),
            session_id=session_id,
            trace_id=trace_id,
            observer_version="observer-pin",
            product_under_test_version="product-pin",
            result_dir=tmp_path / "leased-result",
        )
    )
    body = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "session.id", "value": {"stringValue": session_id}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "claude_code.llm_request",
                                "attributes": [
                                    {
                                        "key": "session.id",
                                        "value": {"stringValue": session_id},
                                    },
                                    {
                                        "key": "prompt.id",
                                        "value": {"stringValue": "prompt-1"},
                                    },
                                    {
                                        "key": "request_id",
                                        "value": {"stringValue": "req_capture_1"},
                                    },
                                    {
                                        "key": "gen_ai.request.model",
                                        "value": {"stringValue": "test-model"},
                                    },
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }
    request = urllib.request.Request(
        f"{capture.bindings.otlp_endpoint}/v1/traces",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2.0) as response:
        assert response.status == 200

    result = capture.finish(deadline=time.monotonic() + 5.0)

    assert result.completeness == "complete"
    assert result.source("telemetry").status == "finalized"
    assert result.source("telemetry").completeness == "full"
    assert result.source("telemetry").details["nodes_count"] == 1
    assert result.view("model_boundary").completeness == "full"

    from opentraces.core.context_tree import CONTEXT_NODE_OBSERVED
    from opentraces.core.trails.event_log import read_events

    events = read_events(project, verify=True)
    assert any(
        event.event_type == CONTEXT_NODE_OBSERVED and event.trace_id == trace_id
        for event in events
    )
