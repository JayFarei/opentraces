"""Regression gates for single-trace readers on hot and interactive paths."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def _fail_full_read(*_args, **_kwargs):
    raise AssertionError("single-trace path used the whole event log")


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def test_trace_reader_can_skip_index_rebuild_with_bounded_raw_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentraces.core.trails import TrailEventDraft, append_event_batch, event_index
    from opentraces.core.trails import event_log

    repo = tmp_path / "repo"
    _init_repo(repo)
    written = append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                step_index=sequence,
                capture_method=["test"],
                payload={"trace_patch_id": str(sequence) * 64},
            )
            for sequence, trace_id in (
                (1, "trace-target"),
                (2, "trace-other"),
                (3, "trace-target"),
            )
        ],
        writer="test",
    )
    monkeypatch.setattr(event_index, "fresh_index_for_read", _fail_full_read)
    monkeypatch.setattr(event_index, "_load_persisted", _fail_full_read)
    monkeypatch.setattr(event_index, "rebuild_event_index", _fail_full_read)
    monkeypatch.setattr(event_log, "_list_event_blob_entries", _fail_full_read)

    events = event_log.read_events_for_trace(
        repo,
        "trace-target",
        rebuild_index=False,
    )

    assert [event.event_id for event in events] == [
        written[0].event_id,
        written[2].event_id,
    ]


def test_trace_reader_fallbacks_match_index_for_json_escaped_trace_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentraces.core.trails import TrailEventDraft, append_event_batch, event_index
    from opentraces.core.trails import event_log

    repo = tmp_path / "repo"
    _init_repo(repo)
    trace_id = 'trace-☃-"quoted"-\\backslash'
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=event_trace_id,
                step_index=sequence,
                capture_method=["test"],
                payload={"trace_patch_id": str(sequence) * 64},
            )
            for sequence, event_trace_id in (
                (1, trace_id),
                (2, "trace-other"),
                (3, trace_id),
            )
        ],
        writer="test",
    )

    indexed = event_log.read_events_for_trace(repo, trace_id)

    trace_cache = event_log._trace_events_cache_path(repo, trace_id)
    assert trace_cache is not None
    trace_cache.unlink()
    index_path = event_index._base_path(repo)
    assert index_path is not None
    index_path.unlink()
    event_index._INDEX_MEMO.clear()

    cold = event_log.read_events_for_trace(repo, trace_id, rebuild_index=False)

    trace_cache.unlink()
    monkeypatch.setattr(event_index, "rebuild_event_index", lambda *_args: None)
    legacy = event_log.read_events_for_trace(repo, trace_id)

    assert cold == indexed
    assert legacy == indexed


def test_context_query_uses_trace_reader_for_single_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.core.context_tree import query

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(query, "read_events", _fail_full_read)
    monkeypatch.setattr(
        query,
        "read_events_for_trace",
        lambda _repo, trace_id, *, rebuild_index: (
            calls.append((trace_id, rebuild_index)) or []
        ),
    )

    projection = query.build_context_tree_projection(tmp_path, trace_id="trace-target")

    assert projection.nodes_by_trace == {}
    assert calls == [("trace-target", False)]


def test_context_bucket_projection_reuses_one_trace_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.core import bucket_context_store
    from opentraces.core.context_tree import query
    from opentraces.core.trails import TrailEventDraft, append_event_batch, event_log

    _init_repo(tmp_path)
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id="trace-target",
                step_index=1,
                capture_method=["test"],
                payload={"snapshot_id": "snapshot-target"},
            )
        ],
        writer="test",
    )
    watermark_path = event_log._verify_watermark_path(tmp_path)
    assert watermark_path is not None
    assert not watermark_path.exists()
    event_log._VERIFY_STATUS_CACHE.clear()

    scoped_events = [
        SimpleNamespace(
            trace_id=None,
            payload={},
            event_sequence=0,
            event_type="unrelated",
        )
    ]
    calls: list[tuple[str, bool]] = []
    observed: list[list] = []
    full_reads: list[bool] = []

    def _trap_full_read(*_args, **_kwargs):
        full_reads.append(True)
        raise AssertionError("single-trace context projection verified the whole event log")

    monkeypatch.setattr(event_log, "read_events", _trap_full_read)
    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, trace_id, *, rebuild_index: (
            calls.append((trace_id, rebuild_index)) or scoped_events
        ),
    )
    monkeypatch.setattr(
        query,
        "build_context_tree_projection",
        lambda _repo, **kwargs: (
            observed.append(kwargs["events"]) or SimpleNamespace(nodes_by_trace={})
        ),
    )

    result = bucket_context_store.project_context_tree_to_bucket(
        tmp_path, project_slug="project", trace_id="trace-target"
    )

    assert result["traces_projected"] == 0
    assert calls == [("trace-target", False)]
    assert observed == [scoped_events]
    assert full_reads == []


def test_context_status_streams_only_to_first_event_after_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.core import bucket_context_store, config, trails
    from opentraces.core.trails import event_log

    monkeypatch.setattr(
        bucket_context_store,
        "_iter_context_tree_head_payloads",
        lambda: [
            (
                "project-slug",
                "trace-target",
                {
                    "events_processed_through_sequence": 2,
                    "last_projection_at": "2026-07-28T09:00:00Z",
                },
            )
        ],
    )
    monkeypatch.setattr(
        bucket_context_store,
        "context_tree_snapshot",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        bucket_context_store,
        "verify_context_tree_layer_refs",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(config, "opted_in_projects", lambda _cfg: [str(tmp_path)])
    monkeypatch.setattr(
        config,
        "get_project_dir",
        lambda _path: tmp_path / "project-slug",
    )
    monkeypatch.setattr(
        trails,
        "event_log_status",
        lambda _repo: {"state": "ok", "event_count": 4, "head": "head-4"},
    )
    monkeypatch.setattr(trails, "read_events", _fail_full_read)
    consumed: list[int] = []

    def _events():
        for sequence in (1, 2, 3):
            consumed.append(sequence)
            yield SimpleNamespace(
                event_sequence=sequence,
                event_time=f"2026-07-28T09:00:0{sequence}Z",
            )
        raise AssertionError("status read beyond the first unprojected event")

    monkeypatch.setattr(
        event_log,
        "iter_events",
        lambda _repo, _head: _events(),
    )

    result = bucket_context_store.compute_context_tree_status()

    assert result["events_since_last_projection"] == 2
    assert result["oldest_unprojected_event_time"] == "2026-07-28T09:00:03Z"
    assert consumed == [1, 2, 3]


def test_patch_backfill_uses_only_current_ingest_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentraces.core import ingest
    from opentraces.core.trails import TrailEventDraft, append_event_batch, event_index
    from opentraces.core.trails import event_log

    repo = tmp_path / "repo"
    _init_repo(repo)
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="unrelated-historical-trace",
                generation_index=1,
                step_index=1,
                capture_method=["test"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:historical",
                    "file_path": "historical.py",
                    "limitations": [],
                },
            )
        ],
        writer="historical",
    )
    [current_event] = append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="trace-target",
                generation_index=2,
                step_index=3,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:target",
                    "file_path": "app.py",
                    "limitations": [],
                },
            )
        ],
        writer="current-ingest",
    )
    monkeypatch.setattr(event_index, "fresh_index_for_read", _fail_full_read)
    monkeypatch.setattr(event_index, "rebuild_event_index", _fail_full_read)
    monkeypatch.setattr(event_log, "_iter_event_blobs_streaming", _fail_full_read)
    monkeypatch.setattr(event_log, "_list_event_blob_entries", _fail_full_read)

    patches = ingest._backfill_patches_from_trail_events(
        repo,
        "trace-target",
        2,
        events=[current_event],
    )

    assert [patch.patch_id for patch in patches] == ["tracepatch-sha256:target"]


def test_step_window_emission_uses_trace_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.core.trails import event_log, snapshots

    _init_repo(tmp_path)
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(event_log, "read_events", _fail_full_read)
    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, trace_id, *, rebuild_index: (
            calls.append((trace_id, rebuild_index)) or []
        ),
    )
    record = SimpleNamespace(
        trace_id="trace-target",
        session_id="session-target",
        generation_index=0,
        steps=[],
        metadata={
            "hook_stop": [
                {
                    "timestamp": "2026-07-28T10:00:00Z",
                    "trail": {"worktree_root": str(tmp_path)},
                }
            ]
        },
    )

    result = snapshots.emit_step_window_events_from_record(tmp_path, record)

    assert [event.event_type for event in result.emitted_events] == ["trace_session_closed"]
    assert calls == [("trace-target", False)]


def test_origin_snapshot_uses_trace_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentraces.core.trails import event_log, snapshots

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(event_log, "read_events", _fail_full_read)
    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, trace_id, *, rebuild_index: (
            calls.append((trace_id, rebuild_index)) or []
        ),
    )
    monkeypatch.setattr(snapshots, "append_event_batch", lambda *_a, **_k: [])
    monkeypatch.setattr(snapshots, "_create_snapshot_ref", lambda *_a, **_k: None)

    snapshots.emit_origin_snapshot(
        tmp_path,
        trace_id="trace-target",
        start_tree_id={"algo": "sha1", "hex": "a" * 40},
        capture_method=["hook_pretooluse"],
    )

    assert calls == [("trace-target", False)]


def test_snapshot_diff_uses_trace_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentraces.core.trails import event_log, snapshots

    calls: list[tuple[str, bool]] = []
    scoped_events = [
        SimpleNamespace(
            event_type="trace_snapshot_created",
            trace_id="trace-target",
            step_index=1,
            event_sequence=1,
            event_id="event-1",
            payload={
                "snapshot_id": "snapshot-1",
                "snapshot_role": "after",
                "tree_id": {"hex": "a" * 40},
            },
        ),
        SimpleNamespace(
            event_type="trace_snapshot_created",
            trace_id="trace-target",
            step_index=2,
            event_sequence=2,
            event_id="event-2",
            payload={
                "snapshot_id": "snapshot-2",
                "snapshot_role": "after",
                "tree_id": {"hex": "b" * 40},
            },
        ),
    ]
    monkeypatch.setattr(event_log, "read_events", _fail_full_read)
    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, trace_id, *, rebuild_index: (
            calls.append((trace_id, rebuild_index)) or scoped_events
        ),
    )
    monkeypatch.setattr(snapshots, "_git", lambda *_a, **_k: "")

    result = snapshots.diff_step_snapshots(tmp_path, "trace-target", 1, 2)

    assert result["relation"] == "snapshot_diff"
    assert calls == [("trace-target", False)]


def test_step_explanation_uses_trace_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.core.trails import explain

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(explain, "read_events", _fail_full_read, raising=False)
    monkeypatch.setattr(
        explain,
        "read_events_for_trace",
        lambda _repo, trace_id, *, rebuild_index: (
            calls.append((trace_id, rebuild_index)) or []
        ),
    )

    result = explain.explain_trace_step(tmp_path, "trace-target", 4)

    assert result["relation"] == "unknown"
    assert calls == [("trace-target", False)]


def test_warn_missing_patch_audit_uses_trace_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.cli import trail_helpers
    from opentraces.core import trails
    from opentraces.core.trails import event_log

    calls: list[tuple[str, bool]] = []
    events = [
        SimpleNamespace(event_type="file_edit"),
        SimpleNamespace(event_type="trace_patch_created"),
    ]
    monkeypatch.setattr(trails, "read_events", _fail_full_read)
    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, trace_id, *, rebuild_index: (
            calls.append((trace_id, rebuild_index)) or events
        ),
    )

    result = trail_helpers._audit_trail_capture(tmp_path, "trace-target")

    assert result == {
        "file_edits_count": 1,
        "patch_created_count": 1,
        "incomplete": False,
    }
    assert calls == [("trace-target", False)]
