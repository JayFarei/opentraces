"""Public contract tests for portable Capture orchestration (A3)."""

from __future__ import annotations

import json
import gzip
import hashlib
import os
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

from opentraces.capture import Capture, CapturePlan
from opentraces.capture.parity import compare_placements, write_parity_report


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
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": "2026-07-13T10:00:00Z",
            "GIT_COMMITTER_DATE": "2026-07-13T10:00:00Z",
        },
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


def _write_edit_session(session_root: Path, project: Path, session_id: str) -> Path:
    """Build a real hook-backed edit fixture while leaving the pre-edit tree live."""

    from opentraces.core.trails import write_worktree_tree

    target = project / "captured-world-effect.txt"
    before = "before trail evidence\n"
    after = "substantive trail evidence\n"
    target.write_text(before, encoding="utf-8")
    before_tree = write_worktree_tree(project)
    target.write_text(after, encoding="utf-8")
    after_tree = write_worktree_tree(project)
    target.write_text(before, encoding="utf-8")
    head = {
        "algo": "sha1",
        "hex": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
    }
    trail_before = {
        "worktree_root": str(project),
        "tree_id": before_tree,
        "git_head": head,
    }
    trail_after = {
        "worktree_root": str(project),
        "tree_id": after_tree,
        "git_head": head,
    }
    tool_input = {
        "file_path": str(target),
        "old_string": before,
        "new_string": after,
    }
    rows = [
        {
            "type": "user",
            "sessionId": session_id,
            "timestamp": "2026-07-13T10:00:00Z",
            "message": {"role": "user", "content": "Update the evidence file."},
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
                        "id": "tool-edit-1",
                        "name": "Edit",
                        "input": tool_input,
                    }
                ],
            },
        },
        {
            "type": "opentraces_hook",
            "event": "PreToolUse",
            "timestamp": "2026-07-13T10:00:01Z",
            "data": {
                "tool": "Edit",
                "tool_use_id": "tool-edit-1",
                "tool_input": tool_input,
                "trail": trail_before,
            },
        },
        {
            "type": "opentraces_hook",
            "event": "PostToolUse",
            "timestamp": "2026-07-13T10:00:02Z",
            "data": {
                "tool": "Edit",
                "tool_use_id": "tool-edit-1",
                "tool_input": tool_input,
                "capture_status": "captured",
                "trail": trail_after,
            },
        },
    ]
    session_root.mkdir(parents=True, exist_ok=True)
    session = session_root / f"{session_id}.jsonl"
    session.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return session


def _write_raw_message_reference(trace_path: Path, payload: dict[str, object]) -> str:
    material = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    content_hash = "sha256:" + hashlib.sha256(material).hexdigest()
    digest = content_hash.removeprefix("sha256:")
    project_slug = trace_path.parent.parent.name
    blob = (
        trace_path.parents[4]
        / "blobs"
        / "v1"
        / project_slug
        / "raw"
        / digest[:2]
        / f"{digest}.json.gz"
    )
    blob.parent.mkdir(parents=True, exist_ok=True)
    with blob.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            zipped.write(material)
    return content_hash


def _write_context_reference(trace_path: Path, content_hash: str) -> None:
    row = {"payload": {"content": {"messages": [{"content_hash": content_hash}]}}}
    with trace_path.with_name("context.jsonl.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            zipped.write((json.dumps(row, sort_keys=True) + "\n").encode())


def _read_companion(path: Path) -> list[dict[str, object]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


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


def test_missing_optional_source_still_makes_requested_capture_partial(
    tmp_path: Path,
) -> None:
    project = _git_project(tmp_path / "project")

    result = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="persistent",
            requested_sources=("session_jsonl",),
            observer_version="observer-pin",
            product_under_test_version="product-pin",
            result_dir=tmp_path / "missing-source-result",
        )
    ).finish(deadline=time.monotonic() + 5.0)

    assert result.completeness == "partial"
    assert result.source("session_jsonl").status == "unavailable"
    assert result.source("session_jsonl").completeness == "missing"


def test_world_effect_finalizers_settle_inside_capture_lifecycle(
    tmp_path: Path,
) -> None:
    project = _git_project(tmp_path / "project")

    result = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("watcher", "git"),
            required_sources=("watcher", "git"),
            observer_version="observer-pin",
            product_under_test_version="product-pin",
            result_dir=tmp_path / "world-effects-result",
        )
    ).finish(deadline=time.monotonic() + 10.0)

    assert result.completeness == "complete"
    assert result.source("watcher").status == "finalized"
    assert result.source("git").status == "finalized"
    assert result.view("world_effects").completeness == "full"


def test_watcher_baseline_is_opened_before_lifecycle_mutation(tmp_path: Path) -> None:
    project = _git_project(tmp_path / "project")
    capture = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("watcher",),
            required_sources=("watcher",),
            observer_version="observer-pin",
            product_under_test_version="product-pin",
            result_dir=tmp_path / "watcher-result",
        )
    )

    (project / "created-inside-lifecycle.txt").write_text("observed\n")
    result = capture.finish(deadline=time.monotonic() + 10.0)

    watcher = result.source("watcher")
    assert watcher.completeness == "full"
    assert watcher.details["open_baseline_initialized"] is True
    assert watcher.details["final_baseline_initialized"] is False
    assert watcher.details["mutations"] == 1


def test_watcher_refreshes_proven_baseline_across_repeated_lifecycles(
    tmp_path: Path,
) -> None:
    for placement in ("persistent", "leased"):
        project = _git_project(tmp_path / f"{placement}-project")
        result_dir = tmp_path / f"{placement}-watcher-result"
        watcher_results = []

        for run in (1, 2):
            capture = Capture.open(
                CapturePlan(
                    project=project,
                    workspace=project,
                    placement=placement,
                    requested_sources=("watcher",),
                    required_sources=("watcher",),
                    observer_version="observer-pin",
                    product_under_test_version="product-pin",
                    result_dir=result_dir,
                )
            )
            (project / f"lifecycle-{run}.txt").write_text(f"run {run}\n")
            result = capture.finish(deadline=time.monotonic() + 10.0)

            watcher = result.source("watcher")
            assert result.completeness == "complete"
            assert watcher.status == "finalized"
            assert watcher.completeness == "full"
            assert watcher.details["mutations"] == 1
            watcher_results.append(watcher)

        assert all(
            watcher.details["open_baseline_proven"] is True
            for watcher in watcher_results
        )


def test_finalization_is_dependency_ordered_but_results_preserve_request_order(
    tmp_path: Path,
) -> None:
    project = _git_project(tmp_path / "project")
    source = _write_session(project, "reverse-order-session")

    result = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="persistent",
            requested_sources=("bucket", "session_jsonl"),
            required_sources=("bucket", "session_jsonl"),
            session_id="reverse-order-session",
            session_path=source,
            observer_version="observer-pin",
            product_under_test_version="product-pin",
            result_dir=tmp_path / "reverse-order-result",
        )
    ).finish(deadline=time.monotonic() + 10.0)

    assert result.completeness == "complete"
    assert [source.name for source in result.sources] == ["bucket", "session_jsonl"]
    assert result.source("session_jsonl").details["trace_id"]
    assert result.source("bucket").details["trace_id"] == result.trace_refs[0]


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
    assert result.source("telemetry").details["snapshot_quiescent"] is True
    assert result.source("telemetry").details["snapshot_generation"] >= 1
    assert (
        result.source("telemetry").details["accepted_envelopes"]
        == result.source("telemetry").details["snapshot_generation"]
    )
    assert result.view("model_boundary").completeness == "full"

    from opentraces.core.context_tree import CONTEXT_NODE_OBSERVED
    from opentraces.core.trails.event_log import read_events

    events = read_events(project, verify=True)
    assert any(
        event.event_type == CONTEXT_NODE_OBSERVED and event.trace_id == trace_id
        for event in events
    )


def test_persistent_and_leased_capture_have_normalized_material_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentraces.core import paths as capture_paths

    monkeypatch.setattr(
        capture_paths,
        "OPENTRACES_DIR",
        tmp_path / "persistent-runtime",
    )
    projects = {
        "persistent": _git_project(tmp_path / "persistent-project"),
        "leased": _git_project(tmp_path / "leased-project"),
    }
    sources = {
        placement: _write_session(project, "placement-parity-session")
        for placement, project in projects.items()
    }

    def run(placement: str, result_dir: Path):
        from opentraces.core.trails import TrailEventDraft, append_event_batch

        project = projects[placement]
        capture = Capture.open(
            CapturePlan(
                project=project,
                workspace=project,
                placement=placement,
                requested_sources=("session_jsonl", "bucket"),
                required_sources=("session_jsonl", "bucket"),
                actor="claude-code",
                session_id="placement-parity-session",
                session_path=sources[placement],
                observer_version="observer-pin",
                product_under_test_version="product-pin",
                result_dir=result_dir,
            )
        )
        result = capture.finish(deadline=time.monotonic() + 10.0)
        append_event_batch(
            project,
            [
                TrailEventDraft(
                    event_type="filesystem_mutation_observed",
                    payload={
                        "file_path": "captured-world-effect.txt",
                        "authored_text": "substantive trail evidence",
                    },
                    trace_id=result.trace_refs[0],
                    capture_method=["filesystem_watcher"],
                )
            ],
            writer="portable-capture-parity-test",
        )
        Capture.open(
            CapturePlan(
                project=project,
                workspace=project,
                placement=placement,
                requested_sources=("bucket",),
                required_sources=("bucket",),
                trace_id=result.trace_refs[0],
                result_dir=result_dir,
            )
        ).finish(deadline=time.monotonic() + 10.0)
        return result

    persistent = run("persistent", tmp_path / "persistent")
    leased = run("leased", tmp_path / "leased")
    report = compare_placements(
        persistent,
        leased,
        persistent_roots=(projects["persistent"],),
        leased_roots=(projects["leased"],),
    )
    report_path = write_parity_report(report, tmp_path / "parity-report.json")

    assert persistent.completeness == leased.completeness == "complete"
    assert report.matches is True
    assert report.view_completeness_match is True
    assert report.canonical_trace_match is True
    assert report.context_companion_match is True
    assert report.trail_companion_match is True
    assert report.security_match is True
    assert report.path_normalization_applied is True
    assert report.differences == ()
    assert json.loads(report_path.read_text(encoding="utf-8")) == report.to_dict()


def test_parity_dereferences_message_content_before_comparing_hashes(
    tmp_path: Path,
) -> None:
    projects = {
        placement: _git_project(tmp_path / f"{placement}-project")
        for placement in ("persistent", "leased")
    }
    results = []
    for placement in ("persistent", "leased"):
        source = _write_session(projects[placement], "referenced-content-session")
        results.append(
            Capture.open(
                CapturePlan(
                    project=projects[placement],
                    workspace=projects[placement],
                    placement=placement,
                    requested_sources=("session_jsonl", "bucket"),
                    required_sources=("session_jsonl", "bucket"),
                    session_id="referenced-content-session",
                    session_path=source,
                    observer_version="observer-pin",
                    product_under_test_version="product-pin",
                    result_dir=tmp_path / placement,
                )
            ).finish(deadline=time.monotonic() + 10.0)
        )

    trace_paths = [Path(result.source("bucket").details["trace_path"]) for result in results]
    left_hash = _write_raw_message_reference(
        trace_paths[0], {"role": "user", "content": "left meaning"}
    )
    right_hash = _write_raw_message_reference(
        trace_paths[1], {"role": "user", "content": "right meaning"}
    )
    _write_context_reference(trace_paths[0], left_hash)
    _write_context_reference(trace_paths[1], right_hash)

    report = compare_placements(
        results[0],
        results[1],
        persistent_roots=(projects["persistent"],),
        leased_roots=(projects["leased"],),
    )

    assert report.matches is False
    assert report.context_companion_match is False
    assert "context_companion" in report.differences

    _write_context_reference(trace_paths[0], "sha256:" + "a" * 64)
    _write_context_reference(trace_paths[1], "sha256:" + "b" * 64)
    unresolved = compare_placements(
        results[0],
        results[1],
        persistent_roots=(projects["persistent"],),
        leased_roots=(projects["leased"],),
    )
    assert unresolved.context_companion_match is False
    assert "context_companion" in unresolved.differences


def test_parity_preserves_semantic_attribution_range_content_hashes(
    tmp_path: Path,
) -> None:
    projects = {
        placement: _git_project(tmp_path / f"{placement}-project")
        for placement in ("persistent", "leased")
    }
    results = []
    for placement in ("persistent", "leased"):
        source = _write_session(projects[placement], "attribution-hash-session")
        results.append(
            Capture.open(
                CapturePlan(
                    project=projects[placement],
                    workspace=projects[placement],
                    placement=placement,
                    requested_sources=("session_jsonl", "bucket"),
                    required_sources=("session_jsonl", "bucket"),
                    session_id="attribution-hash-session",
                    session_path=source,
                    observer_version="observer-pin",
                    product_under_test_version="product-pin",
                    result_dir=tmp_path / placement,
                )
            ).finish(deadline=time.monotonic() + 10.0)
        )

    for result, semantic_hash in zip(
        results,
        ("murmur3:left-range", "murmur3:right-range"),
        strict=True,
    ):
        trace_path = Path(result.source("bucket").details["trace_path"])
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        trace["attribution"] = {
            "experimental": False,
            "files": [
                {
                    "path": "src/example.py",
                    "conversations": [
                        {
                            "contributor": {"type": "ai", "model_id": "test-model"},
                            "ranges": [
                                {
                                    "start_line": 1,
                                    "end_line": 3,
                                    "content_hash": semantic_hash,
                                    "confidence": "high",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        trace_path.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n")

    report = compare_placements(
        results[0],
        results[1],
        persistent_roots=(projects["persistent"],),
        leased_roots=(projects["leased"],),
    )

    assert report.matches is False
    assert report.canonical_trace_match is False
    assert "canonical_trace" in report.differences


def test_bucket_companions_preserve_canonical_bytes_and_parity_normalizes_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentraces.core import paths as capture_paths
    from opentraces.core.context_tree import CONTEXT_LAYER_CAPTURED
    from opentraces.core.context_tree.models import build_layer
    from opentraces.core.trails import TrailEventDraft, append_event_batch
    from opentraces.core.trails.event_log import read_events
    from opentraces.core.trails.models import payload_content_hash

    monkeypatch.setattr(
        capture_paths,
        "OPENTRACES_DIR",
        tmp_path / "persistent-runtime",
    )

    projects = {
        placement: _git_project(tmp_path / f"{placement}-project")
        for placement in ("persistent", "leased")
    }
    results = []
    for placement in ("persistent", "leased"):
        project = projects[placement]
        source = _write_session(project, "sanitized-companion-session")
        initial_result_dir = tmp_path / f"{placement}-initial"
        initial = Capture.open(
            CapturePlan(
                project=project,
                workspace=project,
                placement=placement,
                requested_sources=("session_jsonl", "bucket"),
                required_sources=("session_jsonl", "bucket"),
                session_id="sanitized-companion-session",
                session_path=source,
                observer_version="observer-pin",
                product_under_test_version="product-pin",
                result_dir=initial_result_dir,
            )
        ).finish(deadline=time.monotonic() + 10.0)
        trace_id = initial.trace_refs[0]
        sensitive = (
            "Read /Users/shared-dev/workspace/private.txt using "
            "sk-live-abcdefghijklmnopqrstuvwxyz123456"
        )
        layer = build_layer(
            layer_type="messages",
            content={"messages": [{"content": sensitive}]},
            completeness="full",
            capture_method="transcript_reconstruction",
        )
        append_event_batch(
            project,
            [
                TrailEventDraft(
                    event_type=CONTEXT_LAYER_CAPTURED,
                    payload=layer.model_dump(mode="json"),
                    trace_id=trace_id,
                    capture_method=["transcript_reconstruction"],
                ),
                TrailEventDraft(
                    event_type="filesystem_mutation_observed",
                    payload={"file_path": sensitive, "authored_text": sensitive},
                    trace_id=trace_id,
                    capture_method=["filesystem_watcher"],
                ),
            ],
            writer="portable-capture-test",
        )
        results.append(
            Capture.open(
                CapturePlan(
                    project=project,
                    workspace=project,
                    placement=placement,
                    requested_sources=("bucket",),
                    required_sources=("bucket",),
                    trace_id=trace_id,
                    observer_version="observer-pin",
                    product_under_test_version="product-pin",
                    # A leased placement's bucket belongs to its lease root;
                    # reopening that same placement must retain that root.
                    result_dir=(
                        initial_result_dir
                        if placement == "leased"
                        else tmp_path / "persistent-projection"
                    ),
                )
            ).finish(deadline=time.monotonic() + 10.0)
        )

    for result in results:
        from opentraces.core.trails.models import TrailEvent, expected_event_id

        trace_path = Path(result.source("bucket").details["trace_path"])
        rows = _read_companion(trace_path.with_name("context.jsonl.gz"))
        rows += _read_companion(trace_path.with_name("trail.jsonl.gz"))
        assert len(rows) >= 2
        material = json.dumps(rows, sort_keys=True)
        assert "/Users/shared-dev" in material
        assert "sk-live-" in material
        assert all(row["content_hash"] == payload_content_hash(row["payload"]) for row in rows)
        assert all(
            row["event_id"] == expected_event_id(TrailEvent.model_validate(row))
            for row in rows
        )

    report = compare_placements(
        results[0],
        results[1],
        persistent_roots=(projects["persistent"],),
        leased_roots=(projects["leased"],),
    )
    assert report.context_companion_match is True
    assert report.trail_companion_match is True

    for project in projects.values():
        canonical = [
            event
            for event in read_events(project, verify=True)
            if event.writer == "portable-capture-test"
        ]
        assert len(canonical) == 2
        assert all(
            event.content_hash == payload_content_hash(event.payload)
            for event in canonical
        )
        # Sanitization is an outbound companion concern. The private canonical
        # log remains byte-identical and preserves its content-address chain.
        assert "/Users/shared-dev" in json.dumps(
            [event.payload for event in canonical], sort_keys=True
        )


@pytest.mark.parametrize("empty_family", ["context", "trail"])
def test_parity_rejects_each_empty_required_companion_family_independently(
    tmp_path: Path,
    empty_family: str,
) -> None:
    projects = {
        placement: _git_project(tmp_path / f"{placement}-project")
        for placement in ("persistent", "leased")
    }
    results = []
    for placement in ("persistent", "leased"):
        source = _write_session(projects[placement], "family-parity-session")
        results.append(
            Capture.open(
                CapturePlan(
                    project=projects[placement],
                    workspace=projects[placement],
                    placement=placement,
                    requested_sources=("session_jsonl", "bucket"),
                    required_sources=("session_jsonl", "bucket"),
                    session_id="family-parity-session",
                    session_path=source,
                    result_dir=tmp_path / placement,
                )
            ).finish(deadline=time.monotonic() + 10.0)
        )

    for result in results:
        trace_path = Path(result.source("bucket").details["trace_path"])
        for family in ("context", "trail"):
            body = b"" if family == empty_family else b'{"payload":{"proof":"substantive"}}\n'
            with trace_path.with_name(f"{family}.jsonl.gz").open("wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
                    zipped.write(body)

    report = compare_placements(
        results[0],
        results[1],
        persistent_roots=(projects["persistent"],),
        leased_roots=(projects["leased"],),
    )

    assert report.matches is False
    assert f"empty_{empty_family}_companion" in report.differences


def test_named_security_policy_is_applied_and_observed(tmp_path: Path) -> None:
    project = _git_project(tmp_path / "project")
    source = _write_session(project, "security-policy-session")

    result = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="persistent",
            requested_sources=("session_jsonl", "bucket"),
            required_sources=("session_jsonl", "bucket"),
            session_id="security-policy-session",
            session_path=source,
            security_policy="basic",
            result_dir=tmp_path / "result",
        )
    ).finish(deadline=time.monotonic() + 10.0)

    assert result.security == {
        "policy": "basic",
        "declared_policy": "basic",
        "configured_tools": ["regex", "entropy"],
        "tools_applied": ["regex", "entropy"],
        "observed": True,
        "raw_body_retention": "delete",
    }
    trace_path = Path(result.source("bucket").details["trace_path"])
    assert json.loads(trace_path.read_text())["security"]["scanned"] is True


def test_unknown_security_policy_is_refused_before_capture(tmp_path: Path) -> None:
    project = _git_project(tmp_path / "project")
    with pytest.raises(ValueError, match="unknown capture security policy"):
        CapturePlan(
            project=project,
            workspace=project,
            placement="persistent",
            requested_sources=("watcher",),
            security_policy="not-a-real-policy",
        )


def test_security_result_does_not_claim_observation_when_no_sanitized_record_exists(
    tmp_path: Path,
) -> None:
    project = _git_project(tmp_path / "project")

    result = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("watcher",),
            required_sources=("watcher",),
            security_policy="basic",
            result_dir=tmp_path / "result",
        )
    ).finish(deadline=time.monotonic() + 5.0)

    assert result.security["declared_policy"] == "basic"
    assert result.security["configured_tools"] == ["regex", "entropy"]
    assert result.security["tools_applied"] == []
    assert result.security["observed"] is False
    assert any("security applied-state" in item for item in result.limitations)


def test_persistent_bindings_report_actual_installed_claude_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentraces.capture.claude_code.install import install

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    installed = install()
    project = _git_project(tmp_path / "project")

    session = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="persistent",
            requested_sources=("session_jsonl",),
            actor="claude-code",
            result_dir=tmp_path / "result",
        )
    )

    assert session.bindings.hook_commands_status == "observed"
    assert session.bindings.hook_commands_reason is None
    assert len(session.bindings.hook_commands) == 4
    command_tokens = {token for command in session.bindings.hook_commands for token in command}
    assert set(installed.installed.values()) <= command_tokens


def test_persistent_bindings_name_unavailable_hook_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = _git_project(tmp_path / "project")

    session = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="persistent",
            requested_sources=("session_jsonl",),
            actor="claude-code",
            result_dir=tmp_path / "result",
        )
    )

    assert session.bindings.hook_commands == ()
    assert session.bindings.hook_commands_status == "unavailable"
    assert "not installed" in session.bindings.hook_commands_reason


def test_required_non_self_observation_is_enforced_and_can_be_proven(
    tmp_path: Path,
) -> None:
    project = _git_project(tmp_path / "project")
    unproven = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("watcher",),
            required_sources=("watcher",),
            require_observer_separation=True,
            result_dir=tmp_path / "unproven",
        )
    ).finish(deadline=time.monotonic() + 5.0)
    assert unproven.completeness == "partial"
    assert unproven.provenance["separation"]["required"] is True
    assert unproven.provenance["separation"]["proven"] is False

    product = subprocess.Popen(["sleep", "10"])
    try:
        proven = Capture.open(
            CapturePlan(
                project=project,
                workspace=project,
                placement="leased",
                requested_sources=("watcher",),
                required_sources=("watcher",),
                require_observer_separation=True,
                product_under_test_pid=product.pid,
                result_dir=tmp_path / "proven",
            )
        ).finish(deadline=time.monotonic() + 5.0)
    finally:
        product.terminate()
        product.wait(timeout=2.0)

    assert proven.completeness == "complete"
    assert proven.provenance["separation"]["proven"] is True
    assert proven.provenance["observer"]["pid"] != proven.provenance["product_under_test"]["pid"]


def test_display_label_parity_requires_matching_labeler_provenance(
    tmp_path: Path,
) -> None:
    projects = {
        placement: _git_project(tmp_path / f"{placement}-project")
        for placement in ("persistent", "leased")
    }
    sources = {
        placement: _write_session(project, "label-parity-session")
        for placement, project in projects.items()
    }
    results = []
    for placement in ("persistent", "leased"):
        project = projects[placement]
        results.append(
            Capture.open(
                CapturePlan(
                    project=project,
                    workspace=project,
                    placement=placement,
                    requested_sources=("session_jsonl", "bucket"),
                    required_sources=("session_jsonl", "bucket"),
                    session_id="label-parity-session",
                    session_path=sources[placement],
                    observer_version="observer-pin",
                    product_under_test_version="product-pin",
                    result_dir=tmp_path / placement,
                )
            ).finish(deadline=time.monotonic() + 10.0)
        )

    report = compare_placements(
        results[0],
        results[1],
        persistent_spans=[{"start": 1, "end": 2, "label": "Read file"}],
        leased_spans=[{"start": 1, "end": 2, "label": "Different wording"}],
        persistent_labeler_provenance={"model": "labeler-a", "version": "1"},
        leased_labeler_provenance={"model": "labeler-b", "version": "1"},
        persistent_roots=(projects["persistent"],),
        leased_roots=(projects["leased"],),
    )

    assert report.span_match is True
    assert report.display_label_match is None
    assert "display labels were not compared" in report.limitations[0]

    unpinned = compare_placements(
        results[0],
        results[1],
        persistent_spans=[{"start": 1, "end": 2, "label": "Read file"}],
        leased_spans=[{"start": 1, "end": 2, "label": "Different wording"}],
        persistent_roots=(projects["persistent"],),
        leased_roots=(projects["leased"],),
    )
    assert unpinned.span_match is True
    assert unpinned.display_label_match is None
    assert "labeler provenance is not pinned" in unpinned.limitations[-1]


def test_watcher_refuses_distinct_declared_workspace(tmp_path: Path) -> None:
    project = _git_project(tmp_path / "project")
    workspace = tmp_path / "actor-workspace"
    workspace.mkdir()

    result = Capture.open(
        CapturePlan(
            project=project,
            workspace=workspace,
            placement="leased",
            requested_sources=("watcher",),
            required_sources=("watcher",),
            result_dir=tmp_path / "result",
        )
    ).finish(deadline=time.monotonic() + 5.0)

    assert result.completeness == "partial"
    assert result.source("watcher").completeness == "missing"
    assert "declared workspace" in result.source("watcher").limitations[0]
    assert result.source("watcher").details["observed_root"] == str(project)
    assert result.source("watcher").details["declared_workspace"] == str(workspace)


def test_stale_telemetry_snapshot_cannot_satisfy_new_lifecycle(tmp_path: Path) -> None:
    from opentraces.capture.otlp.emitter import OTLPCaptureBuffer

    project = _git_project(tmp_path / "project")
    result_dir = tmp_path / "result"
    session_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    buffer = OTLPCaptureBuffer()
    buffer.handle_envelope(
        {
            "received_at": time.time() - 60,
            "signal": "traces",
            "path": "/v1/traces",
            "raw_size": 1,
            "body": {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "session.id",
                                    "value": {"stringValue": session_id},
                                }
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
                                                "value": {"stringValue": "stale-prompt"},
                                            },
                                            {
                                                "key": "request_id",
                                                "value": {"stringValue": "req_stale"},
                                            },
                                        ],
                                    }
                                ]
                            }
                        ],
                    }
                ]
            },
        }
    )
    snapshot = buffer.snapshot_session(session_id)
    assert snapshot is not None
    stale_path = result_dir / "runtime" / "otel-sessions" / f"{session_id}.json"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_text(json.dumps(snapshot), encoding="utf-8")

    result = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("telemetry",),
            required_sources=("telemetry",),
            session_id=session_id,
            trace_id=trace_id,
            result_dir=result_dir,
        )
    ).finish(deadline=time.monotonic() + 2.0)

    assert result.completeness == "partial"
    assert result.source("telemetry").completeness == "missing"
    assert "fresh" in result.source("telemetry").limitations[0]


def test_silent_first_source_cannot_consume_later_source_deadline(tmp_path: Path) -> None:
    project = _git_project(tmp_path / "project")
    fifo = tmp_path / "silent-session.jsonl"
    os.mkfifo(fifo)
    capture = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("session_jsonl", "watcher"),
            required_sources=("session_jsonl", "watcher"),
            session_path=fifo,
            result_dir=tmp_path / "result",
        )
    )
    (project / "during-lifecycle.txt").write_text("observed\n", encoding="utf-8")

    result = capture.finish(deadline=time.monotonic() + 2.0)

    assert result.source("session_jsonl").status == "timed_out"
    assert result.source("watcher").status == "finalized"
    assert result.source("watcher").details["mutations"] == 1


def test_parity_rejects_empty_required_companions_and_cross_placement_leaks(
    tmp_path: Path,
) -> None:
    projects = {
        placement: _git_project(tmp_path / f"{placement}-project")
        for placement in ("persistent", "leased")
    }
    results = []
    for placement in ("persistent", "leased"):
        source = _write_session(projects[placement], "strict-parity-session")
        results.append(
            Capture.open(
                CapturePlan(
                    project=projects[placement],
                    workspace=projects[placement],
                    placement=placement,
                    requested_sources=("session_jsonl", "bucket"),
                    required_sources=("session_jsonl", "bucket"),
                    session_id="strict-parity-session",
                    session_path=source,
                    result_dir=tmp_path / placement,
                )
            ).finish(deadline=time.monotonic() + 10.0)
        )

    left_path = Path(results[0].source("bucket").details["trace_path"])
    right_path = Path(results[1].source("bucket").details["trace_path"])
    # Both-empty is not evidence of Context/Trail parity.
    for trace_path in (left_path, right_path):
        for companion in ("context.jsonl.gz", "trail.jsonl.gz"):
            with trace_path.with_name(companion).open("wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0):
                    pass
    left = json.loads(left_path.read_text(encoding="utf-8"))
    right = json.loads(right_path.read_text(encoding="utf-8"))
    left["task"]["description"] = str(projects["leased"] / "private.txt")
    right["task"]["description"] = str(projects["leased"] / "private.txt")
    left_path.write_text(json.dumps(left), encoding="utf-8")
    right_path.write_text(json.dumps(right), encoding="utf-8")

    report = compare_placements(
        results[0],
        results[1],
        persistent_roots=(projects["persistent"],),
        leased_roots=(projects["leased"],),
    )

    assert report.matches is False
    assert "empty_context_companion" in report.differences
    assert "empty_trail_companion" in report.differences
    assert "canonical_trace" in report.differences


def test_parity_preserves_nested_semantic_content_hashes(tmp_path: Path) -> None:
    projects = {
        placement: _git_project(tmp_path / f"{placement}-project")
        for placement in ("persistent", "leased")
    }
    results = []
    for placement in ("persistent", "leased"):
        source = _write_session(projects[placement], "hash-parity-session")
        results.append(
            Capture.open(
                CapturePlan(
                    project=projects[placement],
                    workspace=projects[placement],
                    placement=placement,
                    requested_sources=("session_jsonl", "bucket"),
                    required_sources=("session_jsonl", "bucket"),
                    session_id="hash-parity-session",
                    session_path=source,
                    result_dir=tmp_path / placement,
                )
            ).finish(deadline=time.monotonic() + 10.0)
        )
    for index, result in enumerate(results):
        trace_path = Path(result.source("bucket").details["trace_path"])
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        trace["attribution"] = {
            "files": [
                {
                    "path": "README.md",
                    "conversations": [
                        {"ranges": [{"content_hash": f"semantic-{index}"}]}
                    ],
                }
            ]
        }
        trace_path.write_text(json.dumps(trace), encoding="utf-8")

    report = compare_placements(
        results[0],
        results[1],
        persistent_roots=(projects["persistent"],),
        leased_roots=(projects["leased"],),
    )
    assert report.canonical_trace_match is False
    assert "canonical_trace" in report.differences


def test_capture_records_unverified_non_self_observation_as_limitation(
    tmp_path: Path,
) -> None:
    project = _git_project(tmp_path / "project")
    result = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("watcher",),
            required_sources=("watcher",),
            observer_version="caller-observer-claim",
            product_under_test_version="caller-product-claim",
            result_dir=tmp_path / "result",
        )
    ).finish(deadline=time.monotonic() + 5.0)

    assert result.provenance["observer"]["derivation"] == "runtime"
    from opentraces import __version__ as runtime_version

    assert result.observer_version == runtime_version
    assert result.provenance["observer"]["version"] != "caller-observer-claim"
    assert result.provenance["observer"]["caller_claim"] == "caller-observer-claim"
    assert result.provenance["product_under_test"]["derivation"] == "caller_claim"
    assert result.provenance["separation"]["proven"] is False
    assert any("non-self-observation" in item for item in result.limitations)
