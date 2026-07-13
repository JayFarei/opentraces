"""Public contract tests for portable Capture orchestration (A3)."""

from __future__ import annotations

import json
import gzip
import hashlib
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

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
) -> None:
    projects = {
        "persistent": _git_project(tmp_path / "persistent-project"),
        "leased": _git_project(tmp_path / "leased-project"),
    }
    sources = {
        placement: _write_session(project, "placement-parity-session")
        for placement, project in projects.items()
    }

    def run(placement: str, result_dir: Path):
        project = projects[placement]
        return Capture.open(
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
        ).finish(deadline=time.monotonic() + 10.0)

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


def test_bucket_companions_are_substantive_sanitized_and_placement_equal(
    tmp_path: Path,
) -> None:
    from opentraces.core.context_tree import CONTEXT_LAYER_CAPTURED
    from opentraces.core.context_tree.models import build_layer
    from opentraces.core.trails import TrailEventDraft, append_event_batch
    from opentraces.core.trails.event_log import read_events
    from opentraces.core.trails.models import payload_content_hash

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
        trace_path = Path(result.source("bucket").details["trace_path"])
        rows = _read_companion(trace_path.with_name("context.jsonl.gz"))
        rows += _read_companion(trace_path.with_name("trail.jsonl.gz"))
        assert len(rows) >= 2
        material = json.dumps(rows, sort_keys=True)
        assert "/Users/shared-dev" not in material
        assert "sk-live-" not in material
        assert "[ot-user-" in material
        assert "[REDACTED]" in material
        assert all(row["content_hash"] == payload_content_hash(row["payload"]) for row in rows)

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
