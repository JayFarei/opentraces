"""Issue #365 residual single-trace readers stay off the full Trail log."""

from __future__ import annotations

import pickle
import subprocess
from pathlib import Path
from types import SimpleNamespace

from opentraces_schema import TraceMap, TraceMapNode


def _snapshot_event(trace_id: str, *, step_index: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        event_id="trailevent-sha256:snapshot",
        event_sequence=7,
        event_time="2026-07-28T09:00:00Z",
        event_type="trace_snapshot_created",
        trace_id=trace_id,
        generation_index=0,
        step_index=step_index,
        payload={
            "snapshot_id": "sha256:snapshot",
            "snapshot_ref": "ot://snapshot/sha256:snapshot",
            "snapshot_role": "after",
            "tree_id": {"algo": "sha1", "hex": "a" * 40},
            "capture_status": "captured",
        },
    )


def _trail_event(
    event_type: str,
    event_sequence: int,
    payload: dict,
    *,
    trace_id: str = "trace-target",
    step_index: int = 2,
) -> SimpleNamespace:
    return SimpleNamespace(
        event_id=f"trailevent-sha256:event-{event_sequence}",
        event_sequence=event_sequence,
        event_time=f"2026-07-28T09:00:{event_sequence:02d}Z",
        event_type=event_type,
        trace_id=trace_id,
        generation_index=0,
        step_index=step_index,
        capture_method=["test"],
        payload=payload,
    )


def _forbid_full_read(*_args, **_kwargs):
    raise AssertionError("single-trace operation called read_events")


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def test_list_trace_snapshots_uses_trace_indexed_reader(tmp_path: Path, monkeypatch) -> None:
    from opentraces.core.trails import workspace

    event = _snapshot_event("trace-target")
    monkeypatch.setattr(workspace, "read_events", _forbid_full_read, raising=False)
    monkeypatch.setattr(
        workspace,
        "read_events_for_trace",
        lambda _repo, trace_id, *, rebuild_index: (
            [event]
            if trace_id == "trace-target" and rebuild_index is False
            else []
        ),
    )

    payload = workspace.list_trace_snapshots(tmp_path, "trace-target")

    assert payload["snapshot_count"] == 1
    assert payload["snapshots"][0]["snapshot_id"] == "sha256:snapshot"


def test_snapshot_checkout_uses_exact_snapshot_reader(
    tmp_path: Path, monkeypatch
) -> None:
    from opentraces.core.trails import workspace

    event = _snapshot_event("trace-target")
    monkeypatch.setattr(workspace, "read_events", _forbid_full_read, raising=False)
    monkeypatch.setattr(
        workspace,
        "read_events_scoped",
        _forbid_full_read,
        raising=False,
    )
    calls = []
    monkeypatch.setattr(
        workspace,
        "read_event_for_snapshot",
        lambda _repo, snapshot_id: calls.append(snapshot_id) or event,
    )

    payload = workspace.snapshot_checkout_packet(
        tmp_path,
        "sha256:snapshot",
        dry_run=True,
    )

    assert payload["relation"] == "snapshot_rewind"
    assert payload["snapshot"]["snapshot_id"] == "sha256:snapshot"
    assert calls == ["sha256:snapshot"]


def test_snapshot_resume_uses_trace_indexed_reader(tmp_path: Path, monkeypatch) -> None:
    from opentraces.core.trails import workspace

    record = SimpleNamespace(
        trace_id="trace-target",
        metadata={"trace_trails": {"snapshot_resume_contract": "opentraces.snapshot_resume.v1"}},
        steps=[],
    )
    monkeypatch.setattr(workspace, "read_events", _forbid_full_read, raising=False)
    monkeypatch.setattr(
        workspace,
        "read_events_for_trace",
        lambda *_args, rebuild_index: [] if rebuild_index is False else None,
    )

    payload = workspace.snapshot_resume_packet(
        tmp_path,
        record,
        "s2",
        dry_run=True,
    )

    assert payload["resume_mode"] == "unknown"
    assert payload["limitations"] == ["missing_snapshot:step_2"]


def test_explain_commit_reads_commit_slice_then_only_referenced_patches(
    tmp_path: Path, monkeypatch
) -> None:
    from opentraces.core.trails import explain

    commit = "c" * 40
    patch_id = "a" * 64
    anchor_id = "b" * 64
    patch_event = _trail_event(
        "trace_patch_created",
        2,
        {
            "trace_patch_id": patch_id,
            "file_path": "app.py",
            "affected_range": {"start_line": 4, "end_line": 6},
        },
    )
    anchor_event = _trail_event(
        "git_anchor_created",
        3,
        {
            "trace_patch_id": patch_id,
            "git_anchor_id": anchor_id,
            "commit_id": {"algo": "sha1", "hex": commit},
            "path": "app.py",
            "range": {"start_line": 4, "end_line": 6},
        },
    )
    monkeypatch.setattr(explain, "read_events", _forbid_full_read, raising=False)
    scoped_calls = []
    patch_calls = []

    def _read_commit_slice(_repo, **kwargs):
        scoped_calls.append(kwargs)
        return [anchor_event]

    def _read_patches(_repo, patch_ids, *, event_types, rebuild_index):
        patch_calls.append((patch_ids, event_types, rebuild_index))
        return [patch_event]

    monkeypatch.setattr(explain, "read_events_scoped", _read_commit_slice, raising=False)
    monkeypatch.setattr(explain, "read_events_for_patches", _read_patches, raising=False)

    payload = explain.explain_commit(tmp_path, commit)

    assert payload["trace_patches"][0]["file_path"] == "app.py"
    assert scoped_calls == [
        {
            "event_types": {
                "git_anchor_created",
                "git_anchor_search_completed",
            },
            "commit_filter": {
                "git_anchor_created": "commit_id",
                "git_anchor_search_completed": "search_head",
            },
            "commit_sha": commit,
            "rebuild_index": False,
        }
    ]
    assert patch_calls == [({patch_id}, {"trace_patch_created"}, False)]


def test_explain_file_line_streams_one_best_anchor_then_reads_its_patch(
    tmp_path: Path, monkeypatch
) -> None:
    from opentraces.core.trails import explain

    patch_id = "a" * 64
    latest_anchor_id = "d" * 64
    patch_event = _trail_event(
        "trace_patch_created",
        2,
        {
            "trace_patch_id": patch_id,
            "file_path": "app.py",
            "affected_range": {"start_line": 8, "end_line": 12},
        },
    )
    older = _trail_event(
        "git_anchor_created",
        3,
        {
            "trace_patch_id": patch_id,
            "git_anchor_id": "b" * 64,
            "path": "app.py",
            "range": {"start_line": 8, "end_line": 12},
        },
    )
    unrelated = _trail_event(
        "git_anchor_created",
        4,
        {
            "trace_patch_id": "f" * 64,
            "git_anchor_id": "e" * 64,
            "path": "other.py",
            "range": {"start_line": 1, "end_line": 99},
        },
    )
    latest = _trail_event(
        "git_anchor_created",
        5,
        {
            "trace_patch_id": patch_id,
            "git_anchor_id": latest_anchor_id,
            "path": "app.py",
            "range": {"start_line": 8, "end_line": 12},
        },
    )
    monkeypatch.setattr(explain, "read_events", _forbid_full_read, raising=False)
    scoped_calls = []
    patch_calls = []

    def _stream_anchors(_repo, *, event_types, sink, rebuild_index):
        scoped_calls.append((event_types, rebuild_index))
        # Explicit non-rebuilding sinks may receive Git object order rather
        # than event-sequence order; exact lookup must still choose the max.
        for event in (latest, unrelated, older):
            sink(event)
        return []

    def _read_patch(_repo, patch_ids, *, event_types, rebuild_index):
        patch_calls.append((patch_ids, event_types, rebuild_index))
        return [patch_event]

    monkeypatch.setattr(explain, "read_events_scoped", _stream_anchors, raising=False)
    monkeypatch.setattr(explain, "read_events_for_patches", _read_patch, raising=False)

    payload = explain.explain_file_line(tmp_path, "app.py:10")

    assert payload["git_anchor"]["git_anchor_id"] == latest_anchor_id
    assert scoped_calls == [({"git_anchor_created"}, False)]
    assert patch_calls == [({patch_id}, {"trace_patch_created"}, False)]


def test_detect_bursts_uses_trace_indexed_reader(tmp_path: Path, monkeypatch) -> None:
    from opentraces.core import trails
    from opentraces.core.trails import event_log
    from opentraces.core.bursts import detect_bursts

    trace_id = "trace-target"
    trace_map = TraceMap(
        trace_id=trace_id,
        root_node_ids=[f"tmn:{trace_id}:1"],
        nodes=[
            TraceMapNode(
                node_id=f"tmn:{trace_id}:1",
                trace_id=trace_id,
                unit_id=f"tu:{trace_id}:1",
                action_type="file_edit",
                step_index=2,
                start_step_index=2,
                end_step_index=2,
                files_modified=["app.py"],
            )
        ],
        edges=[],
    )
    full_reads: list[Path] = []

    def _trap_full_read(repo: Path):
        full_reads.append(repo)
        raise AssertionError("single-trace operation called read_events")

    monkeypatch.setattr(trails, "read_events", _trap_full_read)
    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, requested, *, rebuild_index: (
            []
            if requested == trace_id and rebuild_index is False
            else None
        ),
    )

    bursts = detect_bursts(
        trace_map,
        trace_record={"trace_id": trace_id, "steps": []},
        repo_path=tmp_path,
    )

    assert len(bursts) == 1
    assert bursts[0].step_range == [2, 2]
    assert full_reads == []


def test_detect_bursts_supplements_legacy_trace_less_survival_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from opentraces.core import trails
    from opentraces.core.bursts import detect_bursts
    from opentraces.core.trails import event_log

    trace_id = "trace-target"
    patch_id = "a" * 64
    trace_map = TraceMap(
        trace_id=trace_id,
        root_node_ids=[f"tmn:{trace_id}:1"],
        nodes=[
            TraceMapNode(
                node_id=f"tmn:{trace_id}:1",
                trace_id=trace_id,
                unit_id=f"tu:{trace_id}:1",
                action_type="file_edit",
                step_index=2,
                start_step_index=2,
                end_step_index=2,
                files_modified=["app.py"],
            )
        ],
        edges=[],
    )
    patch_event = _trail_event(
        "trace_patch_created",
        2,
        {
            "trace_patch_id": patch_id,
            "file_path": "app.py",
            "step_index": 2,
        },
        trace_id=trace_id,
    )
    legacy_cache = _trail_event(
        "patch_survival_cached",
        3,
        {
            "trace_patch_id": patch_id,
            "observed_head_id": {"algo": "sha1", "hex": "c" * 40},
            "survival": {"survival_state": "alive_on_path"},
        },
        trace_id=None,
    )
    patch_reads = []

    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, requested, *, rebuild_index: (
            [patch_event]
            if requested == trace_id and rebuild_index is False
            else []
        ),
    )

    def _read_patches(_repo, patch_ids, *, event_types, rebuild_index):
        patch_reads.append((patch_ids, event_types, rebuild_index))
        return [legacy_cache]

    monkeypatch.setattr(event_log, "read_events_for_patches", _read_patches)

    def _sync_patch(_repo, requested, *, events, lost_attribution_cache):
        assert requested == patch_id
        assert legacy_cache in events
        assert lost_attribution_cache == {}
        return {"current_survival": {"survival_state": "alive_on_path"}}

    monkeypatch.setattr(trails, "sync_patch", _sync_patch)

    bursts = detect_bursts(
        trace_map,
        trace_record={"trace_id": trace_id, "steps": []},
        repo_path=tmp_path,
    )

    assert patch_reads == [({patch_id}, {"patch_survival_cached"}, False)]
    assert bursts[0].patches_with_survival[0]["survival_state"] == "alive_on_path"


def test_multi_patch_reader_is_indexed_and_fallback_equivalent(tmp_path: Path, monkeypatch) -> None:
    from opentraces.core.trails import event_index
    from opentraces.core.trails.event_log import (
        append_event_batch,
        make_survival_cache_draft,
        read_events_for_patches,
    )

    repo = tmp_path / "repo"
    _init_repo(repo)
    patch_a = "a" * 64
    patch_b = "b" * 64
    append_event_batch(
        repo,
        [
            make_survival_cache_draft(
                trace_patch_id=patch_a,
                observed_head_sha="c" * 40,
                survival={"survival_state": "alive_on_path"},
                trace_id="trace-a",
            ),
            make_survival_cache_draft(
                trace_patch_id=patch_b,
                observed_head_sha="c" * 40,
                survival={"survival_state": "repaired"},
                trace_id="trace-b",
            ),
        ],
        writer="test",
    )

    indexed = read_events_for_patches(
        repo,
        {patch_a, patch_b},
        event_types={"patch_survival_cached"},
    )

    monkeypatch.setattr(event_index, "fresh_index_for_read", _forbid_full_read)
    monkeypatch.setattr(event_index, "_load_persisted", _forbid_full_read)
    monkeypatch.setattr(event_index, "rebuild_event_index", _forbid_full_read)
    from opentraces.core.trails import event_log

    monkeypatch.setattr(event_log, "_list_event_blob_entries", _forbid_full_read)
    fallback = read_events_for_patches(
        repo,
        {patch_a, patch_b},
        event_types={"patch_survival_cached"},
        rebuild_index=False,
    )

    assert [event.event_id for event in indexed] == [event.event_id for event in fallback]
    assert {event.payload["trace_patch_id"] for event in indexed} == {patch_a, patch_b}


def test_multi_patch_index_filters_event_type_before_loading_fat_posting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A same-patch search summary is not fetched for a survival-only read."""
    from opentraces.core.trails import TrailEventDraft, append_event_batch
    from opentraces.core.trails import event_log
    from opentraces.core.trails.event_log import (
        make_survival_cache_draft,
        read_events_for_patches,
    )

    repo = tmp_path / "repo"
    _init_repo(repo)
    patch_id = "a" * 64
    summary, survival = append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=None,
                step_index=None,
                capture_method=["watcher_reconcile"],
                payload={
                    "schema_version": "opentraces.trail.anchor_search.v2",
                    "summary": True,
                    "search_head": {"algo": "sha1", "hex": "d" * 40},
                    "algorithms_attempted": ["exact_range_hash"],
                    "searched": 1,
                    "anchored": 0,
                    "unknown": 1,
                    "results": [
                        {
                            "trace_patch_id": patch_id,
                            "trace_id": "trace-target",
                            "result": "unknown",
                            "created_anchor_ids": [],
                            "fat_irrelevant_payload": "x" * 64_000,
                        }
                    ],
                },
            ),
            make_survival_cache_draft(
                trace_patch_id=patch_id,
                observed_head_sha="c" * 40,
                survival={"survival_state": "alive_on_path"},
                trace_id="trace-target",
            ),
        ],
        writer="test",
    )
    forbidden = f"events/{summary.event_sequence:012d}.json"
    original_iter = event_log._iter_blobs_batch

    def _reject_irrelevant_fetch(read_repo, entries):
        requested = list(entries)
        assert forbidden not in {path for path, _oid in requested}, (
            "an irrelevant fat/invalid search posting was fetched before "
            "event-type filtering"
        )
        return original_iter(read_repo, requested)

    monkeypatch.setattr(event_log, "_iter_blobs_batch", _reject_irrelevant_fetch)

    events = read_events_for_patches(
        repo,
        {patch_id},
        event_types={"patch_survival_cached"},
    )

    assert [event.event_id for event in events] == [survival.event_id]


def test_multi_patch_reader_matches_normalized_id_in_existing_prefixed_index(
    tmp_path: Path,
) -> None:
    """Format-2 indexes keyed by historical ids remain query-compatible."""
    from opentraces.core.trails import TrailEventDraft, append_event_batch, event_index
    from opentraces.core.trails.event_log import read_events_for_patches
    from opentraces.core.trails.ids import trace_patch_ref

    repo = tmp_path / "repo"
    _init_repo(repo)
    patch_id = "a" * 64
    prefixed_id = f"tracepatch-sha256:{patch_id}"
    [written] = append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="trace-target",
                step_index=2,
                capture_method=["test"],
                payload={
                    "trace_patch_id": prefixed_id,
                    "trace_patch_ref": trace_patch_ref(prefixed_id),
                    "file_path": "app.py",
                },
            )
        ],
        writer="test",
    )

    # Reproduce an already-persisted format-2 index from before normalized
    # aliases were added: only the historical raw key is present.
    base_path = event_index._base_path(repo)
    assert base_path is not None
    persisted = pickle.loads(base_path.read_bytes())
    assert persisted["format"] == 2
    persisted["by_patch"].pop(patch_id, None)
    persisted["by_patch"][prefixed_id] = [written.event_sequence]
    base_path.write_bytes(pickle.dumps(persisted, protocol=pickle.HIGHEST_PROTOCOL))
    event_index.invalidate_event_index_memo(repo)

    normalized = read_events_for_patches(
        repo,
        {patch_id},
        event_types={"trace_patch_created"},
    )
    raw = read_events_for_patches(
        repo,
        {prefixed_id},
        event_types={"trace_patch_created"},
    )

    assert [event.event_id for event in normalized] == [written.event_id]
    assert [event.event_id for event in raw] == [written.event_id]


def test_default_scoped_sink_preserves_event_sequence_after_index_rebuild_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The legacy default sink contract remains sequence ordered."""
    from opentraces.core.trails import TrailEventDraft, append_event_batch, event_index
    from opentraces.core.trails import event_log

    repo = tmp_path / "repo"
    _init_repo(repo)
    written = append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="trace-target",
                step_index=sequence,
                capture_method=["test"],
                payload={"trace_patch_id": str(sequence) * 64},
            )
            for sequence in (1, 2, 3)
        ],
        writer="test",
    )
    raw_by_path = {
        f"events/{event.event_sequence:012d}.json": event.model_dump_json().encode()
        for event in written
    }
    entries = [(path, f"oid-{index}") for index, path in enumerate(raw_by_path)]

    monkeypatch.setattr(event_index, "fresh_index_for_read", lambda *_args: None)
    monkeypatch.setattr(event_index, "rebuild_event_index", lambda *_args: None)
    monkeypatch.setattr(event_log, "_list_event_blob_entries", lambda *_args: entries)
    monkeypatch.setattr(
        event_log,
        "_iter_blobs_batch",
        lambda _repo, requested: iter(raw_by_path[path] for path, _oid in requested),
    )
    monkeypatch.setattr(
        event_log,
        "_iter_event_blobs_streaming",
        lambda *_args: iter(
            raw_by_path[f"events/{event.event_sequence:012d}.json"]
            for event in reversed(written)
        ),
    )
    seen: list[int] = []

    event_log.read_events_scoped(
        repo,
        event_types={"trace_patch_created"},
        sink=lambda event: seen.append(event.event_sequence),
    )

    assert seen == [1, 2, 3]


def test_explicit_nonrebuilding_scoped_read_never_loads_persisted_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The bounded cold path bypasses even a valid persisted index."""
    from opentraces.core.trails import TrailEventDraft, append_event_batch, event_index
    from opentraces.core.trails import event_log

    repo = tmp_path / "repo"
    _init_repo(repo)
    [written] = append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="trace-target",
                step_index=1,
                capture_method=["test"],
                payload={"trace_patch_id": "a" * 64},
            )
        ],
        writer="test",
    )
    monkeypatch.setattr(event_index, "fresh_index_for_read", _forbid_full_read)
    monkeypatch.setattr(event_index, "_load_persisted", _forbid_full_read)

    events = event_log.read_events_scoped(
        repo,
        event_types={"trace_patch_created"},
        rebuild_index=False,
    )

    assert [event.event_id for event in events] == [written.event_id]
