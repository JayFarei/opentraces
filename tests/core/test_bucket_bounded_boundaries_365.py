"""Issue #365 follow-up: keep bucket hot/read-only paths trace-bounded."""

from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path

from opentraces_schema import Agent, GitAnchor, Patch, Step, TraceRecord

from opentraces.core import bucket_envelope as be
from opentraces.core import bucket_events as bev
from opentraces.core import bucket_store as bs
from opentraces.core import paths
from opentraces.core._bucket_io import _atomic_write_gzip
from opentraces.core.bucket_layout import (
    trace_v1_context_path,
    trace_v1_trail_path,
)
from opentraces.core.trails import TrailEventDraft, append_event_batch


def _record(trace_id: str) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="codex-cli"),
        steps=[Step(step_index=1, role="user", content="task")],
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
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
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _append_trace_event(repo: Path, trace_id: str, tag: str) -> None:
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id=trace_id,
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={"snapshot_id": tag, "limitations": []},
            )
        ],
        writer=f"test-{tag}",
    )


def test_refresh_preserves_existing_companions_when_fallback_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, trace_id = "proj", "trace-refresh"
    trail_path = trace_v1_trail_path(slug, trace_id)
    context_path = trace_v1_context_path(slug, trace_id)
    _atomic_write_gzip(trail_path, b'{"existing":"trail"}\n')
    _atomic_write_gzip(context_path, b'{"existing":"context"}\n')
    prior_trail = trail_path.read_bytes()
    prior_context = context_path.read_bytes()

    be.project_per_trace_exports(
        None,
        project_slug=slug,
        trace_id=trace_id,
        record=_record(trace_id),
        events=[],
        events_authoritative=False,
        mirror_fallback=False,
    )

    assert trail_path.read_bytes() == prior_trail
    assert context_path.read_bytes() == prior_context
    assert (
        json.loads(
            paths.bucket_dir()
            .joinpath("traces", "v1", slug, trace_id, "trace.json")
            .read_text(encoding="utf-8")
        )["trace_id"]
        == trace_id
    )


def test_preserved_companion_counts_use_bounded_chunks_and_match_jsonl_lines(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, trace_id = "proj", "trace-fat-preserved"
    trail_path = trace_v1_trail_path(slug, trace_id)
    context_path = trace_v1_context_path(slug, trace_id)
    fat_line = b'{"fat":"' + (b"x" * 200_000) + b'"}'
    trail_body = b"\n \t\r\n" + fat_line + b'\n\n{"tail":true}'
    context_body = b"\n{}\n \t\n[]"
    _atomic_write_gzip(trail_path, trail_body)
    _atomic_write_gzip(context_path, context_body)
    prior_trail = trail_path.read_bytes()
    prior_context = context_path.read_bytes()

    real_gzip_open = be.gzip.open
    read_sizes: list[int] = []

    class _BoundedReader:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *args):
            return self._handle.__exit__(*args)

        def read(self, size=-1):
            assert 0 < size <= 64 * 1024, "gzip reads must stay fixed-size"
            read_sizes.append(size)
            return self._handle.read(size)

        def readline(self, *_args, **_kwargs):
            raise AssertionError("preserved companion counting must not use readline")

        def __iter__(self):
            raise AssertionError("gzip line iteration can materialize a fat JSONL line")

    def _instrumented_open(path, mode="rb", *args, **kwargs):
        assert mode == "rb"
        return _BoundedReader(real_gzip_open(path, mode, *args, **kwargs))

    monkeypatch.setattr(be.gzip, "open", _instrumented_open)

    result = be.project_per_trace_exports(
        None,
        project_slug=slug,
        trace_id=trace_id,
        record=_record(trace_id),
        events=[],
        events_authoritative=False,
        mirror_fallback=False,
    )

    assert result["trail_event_count"] == 2
    assert result["context_event_count"] == 2
    assert read_sizes
    assert trail_path.read_bytes() == prior_trail
    assert context_path.read_bytes() == prior_context


def test_auto_live_empty_preserves_companions_when_fallback_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, trace_id = "proj", "trace-auto-live-empty"
    trail_path = trace_v1_trail_path(slug, trace_id)
    context_path = trace_v1_context_path(slug, trace_id)
    _atomic_write_gzip(trail_path, b'{"existing":"trail"}\n')
    _atomic_write_gzip(context_path, b'{"existing":"context"}\n')
    prior_trail = trail_path.read_bytes()
    prior_context = context_path.read_bytes()

    from opentraces.core.trails import event_log

    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, _trace_id, *, rebuild_index: [],
    )

    be.project_per_trace_exports(
        tmp_path,
        project_slug=slug,
        trace_id=trace_id,
        record=_record(trace_id),
        mirror_fallback=False,
    )

    assert trail_path.read_bytes() == prior_trail
    assert context_path.read_bytes() == prior_context


def test_auto_live_empty_preserves_record_anchor_when_fallback_is_disabled(tmp_path, monkeypatch):
    """An empty hot live read is not authority to de-attribute prior evidence."""
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, trace_id, commit_sha = "proj", "trace-anchored", "a" * 40
    record = _record(trace_id)
    record.patches = [
        Patch(
            patch_id="patch-anchored",
            file_path="app.py",
            anchor=GitAnchor(
                last_searched_at="2026-07-28T00:00:00Z",
                found=True,
                commit_sha=commit_sha,
            ),
        )
    ]

    from opentraces.core.trails import event_log

    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, _trace_id, *, rebuild_index: [],
    )

    be.project_per_trace_exports(
        tmp_path,
        project_slug=slug,
        trace_id=trace_id,
        record=record,
        mirror_fallback=False,
    )

    trace_doc = json.loads(
        paths.bucket_dir()
        .joinpath("traces", "v1", slug, trace_id, "trace.json")
        .read_text(encoding="utf-8")
    )
    anchor = trace_doc["patches"][0]["anchor"]
    assert anchor["found"] is True
    assert anchor["commit_sha"] == commit_sha


def test_refresh_preserves_existing_companions_when_mirror_fallback_is_corrupt(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, trace_id = "proj", "trace-corrupt-fallback"
    trail_path = trace_v1_trail_path(slug, trace_id)
    context_path = trace_v1_context_path(slug, trace_id)
    _atomic_write_gzip(trail_path, b'{"existing":"trail"}\n')
    _atomic_write_gzip(context_path, b'{"existing":"context"}\n')
    prior_trail = trail_path.read_bytes()
    prior_context = context_path.read_bytes()

    from opentraces.core.trails import event_log

    monkeypatch.setattr(event_log, "read_events_for_trace", lambda _repo, _trace_id: [])

    def _corrupt(_trace_id):
        raise ValueError("relevant mirror event is corrupt")

    monkeypatch.setattr(be, "read_events_mirror_for_trace", _corrupt)

    be.project_per_trace_exports(
        tmp_path,
        project_slug=slug,
        trace_id=trace_id,
        record=_record(trace_id),
    )

    assert trail_path.read_bytes() == prior_trail
    assert context_path.read_bytes() == prior_context


def test_incomplete_nonempty_mirror_preserves_companions_and_anchor(
    tmp_path,
    monkeypatch,
):
    """A declared non-empty mirror with missing batches is not empty authority."""
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, trace_id, commit_sha = "proj", "trace-incomplete-mirror", "a" * 40
    trail_path = trace_v1_trail_path(slug, trace_id)
    context_path = trace_v1_context_path(slug, trace_id)
    _atomic_write_gzip(trail_path, b'{"existing":"trail"}\n')
    _atomic_write_gzip(context_path, b'{"existing":"context"}\n')
    prior_trail = trail_path.read_bytes()
    prior_context = context_path.read_bytes()

    record = _record(trace_id)
    record.patches = [
        Patch(
            patch_id="patch-anchored",
            file_path="app.py",
            anchor=GitAnchor(
                last_searched_at="2026-07-28T00:00:00Z",
                found=True,
                commit_sha=commit_sha,
            ),
        )
    ]
    index_path = paths.bucket_dir() / "events" / "v1" / "index.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        json.dumps(
            {
                "schema_version": bev.BUCKET_EVENTS_INDEX_SCHEMA,
                "repo_id": slug,
                "event_log_ref": "refs/opentraces/local/events/v1",
                "event_log_head": "b" * 40,
                "batch_count": 1,
                "last_batch_id": "missing-batch",
                "latest_event_sequence": 1,
                "state": "ok",
            }
        ),
        encoding="utf-8",
    )

    from opentraces.core.trails import event_log

    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, _trace_id, *, rebuild_index: [],
    )

    be.project_per_trace_exports(
        tmp_path,
        project_slug=slug,
        trace_id=trace_id,
        record=record,
    )

    assert trail_path.read_bytes() == prior_trail
    assert context_path.read_bytes() == prior_context
    trace_doc = json.loads(
        paths.bucket_dir()
        .joinpath("traces", "v1", slug, trace_id, "trace.json")
        .read_text(encoding="utf-8")
    )
    assert trace_doc["patches"][0]["anchor"]["commit_sha"] == commit_sha


def test_truncated_declared_mirror_batch_preserves_companions(
    tmp_path,
    monkeypatch,
):
    """A gzip failure after scanning remains a read failure, never empty authority."""
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, trace_id = "proj", "trace-truncated-mirror"
    trail_path = trace_v1_trail_path(slug, trace_id)
    context_path = trace_v1_context_path(slug, trace_id)
    _atomic_write_gzip(trail_path, b'{"existing":"trail"}\n')
    _atomic_write_gzip(context_path, b'{"existing":"context"}\n')
    prior_trail = trail_path.read_bytes()
    prior_context = context_path.read_bytes()

    index_path = paths.bucket_dir() / "events" / "v1" / "index.json"
    batches_dir = index_path.parent / "batches"
    batches_dir.mkdir(parents=True)
    batch_id = "truncated-batch"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": bev.BUCKET_EVENTS_INDEX_SCHEMA,
                "repo_id": slug,
                "event_log_ref": "refs/opentraces/local/events/v1",
                "event_log_head": "b" * 40,
                "batch_count": 1,
                "last_batch_id": batch_id,
                "latest_event_sequence": 1,
                "state": "ok",
            }
        ),
        encoding="utf-8",
    )
    batch_path = batches_dir / f"000000000001-{batch_id}.jsonl.gz"
    _atomic_write_gzip(batch_path, b'{"trace_id":"other"}\n')
    batch_path.write_bytes(batch_path.read_bytes()[:-4])

    from opentraces.core.trails import event_log

    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, _trace_id, *, rebuild_index: [],
    )

    be.project_per_trace_exports(
        tmp_path,
        project_slug=slug,
        trace_id=trace_id,
        record=_record(trace_id),
    )

    assert trail_path.read_bytes() == prior_trail
    assert context_path.read_bytes() == prior_context


def test_semantically_incomplete_valid_gzip_mirror_cannot_clear_trace(
    tmp_path,
    monkeypatch,
):
    """Filename completeness alone cannot prove that a trace is absent."""
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)
    slug, trace_id, commit_sha = "proj", "trace-replaced-batch", "a" * 40
    _append_trace_event(repo, trace_id, "wanted")
    bev.sync_events_mirror(repo, repo_id=slug)

    trail_path = trace_v1_trail_path(slug, trace_id)
    context_path = trace_v1_context_path(slug, trace_id)
    _atomic_write_gzip(trail_path, b'{"existing":"trail"}\n')
    _atomic_write_gzip(context_path, b'{"existing":"context"}\n')
    prior_trail = trail_path.read_bytes()
    prior_context = context_path.read_bytes()
    record = _record(trace_id)
    record.patches = [
        Patch(
            patch_id="patch-anchored",
            file_path="app.py",
            anchor=GitAnchor(
                last_searched_at="2026-07-28T00:00:00Z",
                found=True,
                commit_sha=commit_sha,
            ),
        )
    ]

    # Keep the exact declared filename/ordinal and a valid gzip stream, but
    # replace the semantic contents. Current v2 index metadata has no digest
    # with which a scoped reader can prove that the wanted row was not lost.
    batch_path = next(paths.bucket_dir().joinpath("events", "v1", "batches").glob("*.jsonl.gz"))
    replacement = (
        json.dumps(
            {
                "event_id": "trailevent-sha256:" + ("0" * 64),
                "event_sequence": 1,
                "event_time": "2026-01-01T00:00:00Z",
                "previous_event_id": None,
                "trace_id": "other",
                "generation_index": 0,
                "step_index": 1,
                "batch_id": "other",
                "writer": "test",
                "capture_method": ["test_fixture"],
                "event_type": "trace_snapshot_created",
                "payload": {"snapshot_id": "other"},
                "content_hash": "sha256:" + ("0" * 64),
                "SCHEMA_VERSION": "0.9.0",
                "SECURITY_VERSION": "0.8.0",
                "ATTRIBUTION_VERSION": "0.1.0",
            },
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    _atomic_write_gzip(batch_path, replacement)

    from opentraces.core.trails import event_log

    monkeypatch.setattr(
        event_log,
        "read_events_for_trace",
        lambda _repo, _trace_id, *, rebuild_index: [],
    )

    be.project_per_trace_exports(
        repo,
        project_slug=slug,
        trace_id=trace_id,
        record=record,
    )

    assert trail_path.read_bytes() == prior_trail
    assert context_path.read_bytes() == prior_context
    trace_doc = json.loads(
        paths.bucket_dir()
        .joinpath("traces", "v1", slug, trace_id, "trace.json")
        .read_text(encoding="utf-8")
    )
    assert trace_doc["patches"][0]["anchor"]["commit_sha"] == commit_sha


def test_authoritative_empty_events_clear_existing_companions(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, trace_id = "proj", "trace-authoritative-empty"
    trail_path = trace_v1_trail_path(slug, trace_id)
    context_path = trace_v1_context_path(slug, trace_id)
    _atomic_write_gzip(trail_path, b'{"stale":"trail"}\n')
    _atomic_write_gzip(context_path, b'{"stale":"context"}\n')

    be.project_per_trace_exports(
        None,
        project_slug=slug,
        trace_id=trace_id,
        record=_record(trace_id),
        events=[],
        events_authoritative=True,
        mirror_fallback=False,
    )

    with __import__("gzip").open(trail_path, "rb") as handle:
        assert handle.read() == b""
    with __import__("gzip").open(context_path, "rb") as handle:
        assert handle.read() == b""


def test_trace_scoped_mirror_reader_streams_and_ignores_unrelated_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_trace_event(repo, "wanted", "wanted")
    _append_trace_event(repo, "other", "other")
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id=None,
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_id": "wanted",
                    "snapshot_id": "payload-owned",
                    "limitations": [],
                },
            ),
            TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=None,
                step_index=None,
                capture_method=["watcher_reconcile"],
                payload={
                    "schema_version": "opentraces.trail.anchor_search.v2",
                    "summary": True,
                    "search_head": {"algo": "sha1", "hex": "a" * 40},
                    "algorithms_attempted": ["exact_range_hash"],
                    "searched": 1,
                    "anchored": 1,
                    "unknown": 0,
                    "results": [
                        {
                            "trace_id": "wanted",
                            "trace_patch_id": "patch-wanted",
                            "result": "anchored",
                            "created_anchor_ids": [],
                        }
                    ],
                },
            ),
        ],
        writer="test-owned-shapes",
    )
    bev.sync_events_mirror(repo, repo_id="proj")

    batch = sorted(paths.bucket_dir().joinpath("events", "v1", "batches").glob("*.jsonl.gz"))[0]
    with __import__("gzip").open(batch, "at", encoding="utf-8") as handle:
        handle.write("{unrelated-corruption\n")

    def _boom(*_args, **_kwargs):
        raise AssertionError("whole-batch inflation is forbidden")

    monkeypatch.setattr(bev, "_read_gzip_bytes", _boom)
    events = list(bev.read_events_mirror_for_trace("wanted"))
    assert len(events) == 3
    assert events[0].trace_id == "wanted"
    assert events[1].payload["trace_id"] == "wanted"
    assert events[2].payload["results"][0]["trace_id"] == "wanted"


def test_trace_scoped_mirror_reader_raises_on_relevant_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_trace_event(repo, "wanted", "wanted")
    bev.sync_events_mirror(repo, repo_id="proj")

    batch = next(paths.bucket_dir().joinpath("events", "v1", "batches").glob("*.jsonl.gz"))
    with __import__("gzip").open(batch, "at", encoding="utf-8") as handle:
        handle.write('{"trace_id":"wanted","broken":\n')

    import pytest

    with pytest.raises(ValueError, match="relevant event"):
        list(bev.read_events_mirror_for_trace("wanted"))


def test_trace_scoped_mirror_scans_shared_union_beyond_latest_declared_count(
    tmp_path,
    monkeypatch,
):
    """Owned non-empty evidence remains usable outside the latest index slice."""
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_trace_event(repo, "wanted", "wanted")
    bev.sync_events_mirror(repo, repo_id="proj")

    index_path = paths.bucket_dir() / "events" / "v1" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index.update(
        {
            "batch_count": 0,
            "last_batch_id": None,
            "latest_event_sequence": 0,
        }
    )
    index_path.write_text(json.dumps(index), encoding="utf-8")

    assert [event.trace_id for event in bev.read_events_mirror_for_trace("wanted")] == ["wanted"]


def test_trace_scoped_mirror_accepts_shared_extra_and_duplicate_ordinals(
    tmp_path,
    monkeypatch,
):
    """Shared filenames are not a per-project ownership boundary."""
    import shutil

    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_trace_event(repo, "wanted", "wanted")
    bev.sync_events_mirror(repo, repo_id="proj")

    batches_dir = paths.bucket_dir() / "events" / "v1" / "batches"
    declared = next(batches_dir.glob("*.jsonl.gz"))
    duplicate = batches_dir / "000000000001-other-project.jsonl.gz"
    shutil.copyfile(declared, duplicate)

    assert [event.trace_id for event in bev.read_events_mirror_for_trace("wanted")] == ["wanted"]
    assert [event.trace_id for event in bev.read_events_mirror_batches()] == ["wanted"]

    duplicate.unlink()
    extra = batches_dir / "000000000002-stale-leftover.jsonl.gz"
    shutil.copyfile(declared, extra)

    assert [event.trace_id for event in bev.read_events_mirror_for_trace("wanted")] == ["wanted"]
    assert [event.trace_id for event in bev.read_events_mirror_batches()] == ["wanted"]


def test_trace_scoped_mirror_rejects_conflicting_relevant_shared_copy(
    tmp_path,
    monkeypatch,
):
    """Shared filenames are allowed; conflicting owned content is not."""
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_trace_event(repo, "wanted", "wanted")
    bev.sync_events_mirror(repo, repo_id="proj")

    batches_dir = paths.bucket_dir() / "events" / "v1" / "batches"
    declared = next(batches_dir.glob("*.jsonl.gz"))
    payload = json.loads(gzip.decompress(declared.read_bytes()).decode("utf-8"))
    payload["payload"]["snapshot_id"] = "conflicting-copy"
    conflict = batches_dir / "000000000001-other-project.jsonl.gz"
    _atomic_write_gzip(
        conflict,
        (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"),
    )

    import pytest

    with pytest.raises(ValueError, match="conflicting relevant copies"):
        list(bev.read_events_mirror_for_trace("wanted"))


def test_trace_scoped_mirror_reader_scans_unrelated_fat_lines_in_bounded_chunks(
    tmp_path,
    monkeypatch,
):
    """An unrelated unbounded payload must not become one in-memory JSONL line."""
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="other",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "patch-other",
                    "authored_text": "x" * (2 * 1024 * 1024),
                },
            )
        ],
        writer="test-fat-unrelated",
    )
    _append_trace_event(repo, "wanted", "wanted")
    bev.sync_events_mirror(repo, repo_id="proj")

    real_gzip_open = bev.gzip.open
    read_sizes: list[int] = []

    class _BoundedReader:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *args):
            return self._handle.__exit__(*args)

        def read(self, size=-1):
            assert 0 < size <= 64 * 1024
            read_sizes.append(size)
            return self._handle.read(size)

        def readline(self, *_args, **_kwargs):
            raise AssertionError("fat unrelated lines must not be materialised")

        def __iter__(self):
            raise AssertionError("gzip line iteration is unbounded")

    def _instrumented_open(path, mode="rb", *args, **kwargs):
        assert mode == "rb"
        return _BoundedReader(real_gzip_open(path, mode, *args, **kwargs))

    monkeypatch.setattr(bev.gzip, "open", _instrumented_open)

    events = list(bev.read_events_mirror_for_trace("wanted"))

    assert [event.trace_id for event in events] == ["wanted"]
    assert read_sizes


def test_readonly_orphan_context_uses_trace_scoped_mirror(monkeypatch):
    monkeypatch.setattr(be, "_iter_opted_in_projects", lambda: [])

    def _full_read_boom():
        raise AssertionError("read-only orphan reconstruction inflated whole mirror")

    monkeypatch.setattr(be, "read_events_mirror_batches", _full_read_boom)
    monkeypatch.setattr(be, "read_events_mirror_for_trace", lambda trace_id: iter(()))
    assert be._context_events_for_trace_readonly("proj", "trace") == []


def test_readonly_orphan_context_surfaces_relevant_mirror_corruption(monkeypatch):
    monkeypatch.setattr(be, "_iter_opted_in_projects", lambda: [])

    def _corrupt(_trace_id):
        raise ValueError("relevant event is corrupt")

    monkeypatch.setattr(be, "read_events_mirror_for_trace", _corrupt)

    import pytest

    with pytest.raises(ValueError, match="relevant event"):
        be._context_events_for_trace_readonly("proj", "trace")


def test_upsert_manifest_reads_only_this_traces_anchors(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    repo.mkdir()
    slug, trace_id = "proj", "wanted"
    monkeypatch.setattr(be, "_iter_opted_in_projects", lambda: [(repo, slug)])

    from opentraces.core.trails import event_log

    def _full_anchor_read_boom(*_args, **_kwargs):
        raise AssertionError("upsert consulted all project anchors")

    trace_reads = []

    def _trace_read(read_repo, read_trace_id, *, rebuild_index):
        trace_reads.append((read_repo, read_trace_id, rebuild_index))
        return []

    monkeypatch.setattr(event_log, "read_events_scoped", _full_anchor_read_boom)
    monkeypatch.setattr(event_log, "read_events_for_trace", _trace_read)
    be.project_per_trace_exports(
        None,
        project_slug=slug,
        trace_id=trace_id,
        record=_record(trace_id),
        events=[],
        mirror_fallback=False,
    )

    row = bs.upsert_manifest_trace_row(
        repo,
        project_slug=slug,
        trace_id=trace_id,
        record=_record(trace_id),
    )
    assert row is not None
    assert row["summary"]["anchored_count"] == 0
    assert trace_reads == [(repo, trace_id, False)]


def test_full_mirror_rebuild_streams_event_log(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_trace_event(repo, "one", "one")
    _append_trace_event(repo, "two", "two")

    from opentraces.core.trails import event_log

    full_reads: list[bool] = []

    def _boom(*_args, **_kwargs):
        full_reads.append(True)
        raise AssertionError("full rebuild materialized read_events")

    watermark_path = event_log._verify_watermark_path(repo)
    assert watermark_path is not None
    assert not watermark_path.exists()
    monkeypatch.setattr(event_log, "read_events", _boom)
    index = bev.sync_events_mirror(repo, repo_id="proj")
    assert full_reads == []
    assert index["state"] == "ok"
    assert "verification" not in index
    assert index["batch_count"] == 2
    assert [event.trace_id for event in bev.read_events_mirror_batches()] == [
        "one",
        "two",
    ]


def test_same_event_ref_mirror_digest_ignores_local_verification_watermark(
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_trace_event(repo, "one", "one")

    from opentraces.core.trails import event_log

    monkeypatch.setattr(bev, "utc_now_str", lambda: "2026-07-29T00:00:00Z")
    event_log._VERIFY_STATUS_CACHE.clear()
    watermark_path = event_log._verify_watermark_path(repo)
    assert watermark_path is not None
    assert not watermark_path.exists()

    first_root = tmp_path / "first" / ".opentraces"
    monkeypatch.setattr(paths, "OPENTRACES_DIR", first_root)
    before_status = event_log.event_log_verification_status(repo, mode="quick")
    before_index = bev.sync_events_mirror(repo, repo_id="proj")
    before_index_bytes = (paths.bucket_dir() / "events" / "v1" / "index.json").read_bytes()
    before_batches = {
        path.name: path.read_bytes()
        for path in sorted((paths.bucket_dir() / "events" / "v1" / "batches").glob("*.jsonl.gz"))
    }
    before_snapshot = bev.trail_event_snapshot()
    before_bucket_digest = bs.bucket_manifest(
        write=False,
        heal=False,
        include_objects=False,
    )["bucket_digest"]

    verified = event_log.verify_event_log(repo)
    assert verified["errors"] == []
    assert watermark_path.exists()

    second_root = tmp_path / "second" / ".opentraces"
    monkeypatch.setattr(paths, "OPENTRACES_DIR", second_root)
    after_status = event_log.event_log_verification_status(repo, mode="quick")
    after_index = bev.sync_events_mirror(repo, repo_id="proj")
    after_index_bytes = (paths.bucket_dir() / "events" / "v1" / "index.json").read_bytes()
    after_batches = {
        path.name: path.read_bytes()
        for path in sorted((paths.bucket_dir() / "events" / "v1" / "batches").glob("*.jsonl.gz"))
    }
    after_snapshot = bev.trail_event_snapshot()
    after_bucket_digest = bs.bucket_manifest(
        write=False,
        heal=False,
        include_objects=False,
    )["bucket_digest"]

    assert before_status["state"] == "unverified_large"
    assert after_status["state"] == "ok"
    assert before_index["state"] == after_index["state"]
    assert before_index_bytes == after_index_bytes
    assert before_batches == after_batches
    assert before_snapshot["digest"] == after_snapshot["digest"]
    assert before_bucket_digest == after_bucket_digest


def test_canonical_mirror_state_preserves_structural_invalid_diagnostic(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_trace_event(repo, "one", "one")

    from opentraces.core import trails

    status = trails.event_log_verification_status(repo, mode="quick")
    assert status["head"]
    monkeypatch.setattr(
        trails,
        "event_log_verification_status",
        lambda _repo, *, mode: {
            **status,
            "state": "invalid",
            "errors": ["event log tail read failed"],
        },
    )

    index = bev.sync_events_mirror(repo, repo_id="proj")

    assert index["state"] == "invalid"
    assert "verification" not in index


def test_missing_event_ref_preserves_existing_nonempty_mirror(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)

    index_path = paths.bucket_dir() / "events" / "v1" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    prior = {
        "schema_version": "opentraces.bucket.events.v2",
        "repo_id": "restored",
        "event_log_ref": "refs/opentraces/local/events/v1",
        "event_log_head": "a" * 40,
        "batch_count": 1,
        "last_batch_id": "batch-restored",
        "latest_event_sequence": 7,
        "state": "ok",
    }
    index_path.write_text(json.dumps(prior, sort_keys=True), encoding="utf-8")
    prior_bytes = index_path.read_bytes()

    result = bev.sync_events_mirror(repo, repo_id="proj")

    assert result == prior
    assert index_path.read_bytes() == prior_bytes


def test_full_rebuild_compares_existing_large_batches_without_read_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_trace_event(repo, "one", "one")
    bev.sync_events_mirror(repo, repo_id="proj")

    index_path = paths.bucket_dir() / "events" / "v1" / "index.json"
    index_path.write_text("{invalid", encoding="utf-8")

    from opentraces.core.trails import event_log

    watermark_path = event_log._verify_watermark_path(repo)
    assert watermark_path is not None
    watermark_path.parent.mkdir(parents=True, exist_ok=True)
    watermark_path.write_text(
        json.dumps(
            {
                "format": event_log._EVENT_CACHE_FORMAT,
                "head": "0" * 40,
                "last_event_sequence": 0,
                "last_event_id": None,
                "event_count": 0,
                "batch_count": 0,
            }
        ),
        encoding="utf-8",
    )
    event_log._VERIFY_STATUS_CACHE.clear()

    full_reads: list[bool] = []

    def _full_read_boom(*_args, **_kwargs):
        full_reads.append(True)
        raise AssertionError("full rebuild materialized read_events")

    def _read_bytes_boom(self):
        raise AssertionError(f"full rebuild materialized compressed file {self}")

    monkeypatch.setattr(event_log, "read_events", _full_read_boom)
    monkeypatch.setattr(Path, "read_bytes", _read_bytes_boom)
    rebuilt = bev.sync_events_mirror(repo, repo_id="proj")
    assert full_reads == []
    assert rebuilt["state"] == "ok"
    assert "verification" not in rebuilt
    assert rebuilt["batch_count"] == 1
    assert rebuilt["batches_written"] == 0
