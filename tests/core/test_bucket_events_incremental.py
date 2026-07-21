"""#65 — events-mirror sync reads only the appended suffix.

``sync_events_mirror`` was write-incremental but READ-full: every changed
tick materialised the whole log to filter it down to the suffix. These tests
pin that the incremental path (a) never calls the full reader and (b) writes
batch files byte-identical to a from-scratch rebuild (the replay-equals-git
invariant).
"""

from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path

import pytest

import opentraces.core.trails as trails_pkg
from opentraces.core._bucket_io import _gzip_deterministic
from opentraces.core.bucket_events import read_events_mirror_batches, sync_events_mirror
from opentraces.core.paths import bucket_dir
from opentraces.core.trails import TrailEventDraft, append_event_batch


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _append_round(repo: Path, n: int, tag: str) -> None:
    append_event_batch(repo, [
        TrailEventDraft(
            event_type="trace_patch_created", trace_id=f"tr-{tag}",
            step_index=i, capture_method=["hook_posttooluse"],
            payload={"trace_patch_id": f"tracepatch-sha256:{tag}-{i}",
                     "file_path": "f.py", "authored_text": f"# {tag} {i}\n"},
        )
        for i in range(n)
    ], writer=f"test-{tag}")


def _mirror_files() -> dict[str, bytes]:
    batches = bucket_dir() / "events" / "v1" / "batches"
    if not batches.is_dir():
        return {}
    return {p.name: p.read_bytes() for p in sorted(batches.glob("*.jsonl.gz"))}


def test_incremental_sync_never_full_reads_and_matches_rebuild(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_round(repo, 3, "one")
    first = sync_events_mirror(repo, repo_id="proj-a")
    assert first["batch_count"] == 1

    # Appended suffix; the full reader must NOT be needed any more.
    _append_round(repo, 2, "two")
    _append_round(repo, 4, "three")

    def _boom(*a, **k):  # noqa: ANN001, ANN003
        raise AssertionError("full read_events on the incremental mirror path")

    # Scoped patch context: calling monkeypatch.undo() on the shared per-test
    # instance would ALSO revert the conftest's HOME isolation (it nearly
    # wrote into the real ~/.opentraces during development of this test).
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(trails_pkg, "read_events", _boom)
        second = sync_events_mirror(repo, repo_id="proj-a")
    assert second["batch_count"] == 3
    assert second["batches_written"] == 2

    incremental_files = _mirror_files()
    assert len(incremental_files) == 3

    # Byte-identity: wipe the mirror, rebuild from scratch, compare.
    import shutil
    shutil.rmtree(bucket_dir() / "events")
    rebuilt = sync_events_mirror(repo, repo_id="proj-a")
    assert rebuilt["batch_count"] == 3
    assert _mirror_files() == incremental_files


def test_idempotent_when_log_unchanged(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_round(repo, 2, "only")
    sync_events_mirror(repo, repo_id="proj-b")

    def _boom(*a, **k):  # noqa: ANN001, ANN003
        raise AssertionError("any event read on an unchanged log")

    monkeypatch.setattr(trails_pkg, "read_events", _boom)
    again = sync_events_mirror(repo, repo_id="proj-b")
    assert again["batch_count"] == 1


def test_read_events_mirror_batches_dedupes_identical_duplicate_across_files(tmp_path):
    """Issue #358 repair (major): a kill mid ``bucket reclaim`` mirror
    reconcile can leave BOTH a stale batch file and its freshly written
    replacement on disk at once (write-new-then-remove-stale --
    ``bucket_reclaim_search._reconcile_mirror_for_project``). An unchanged
    event's two copies share one content-addressed ``event_id`` (``batch_
    id``/``writer`` sit outside the hash), so the reader must collapse them
    to a single yield rather than surface a duplicate that would break a
    downstream contiguous-sequence consumer such as ``import_event_log``."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_round(repo, 2, "dup")
    sync_events_mirror(repo, repo_id="proj-dup")

    batches_dir = bucket_dir() / "events" / "v1" / "batches"
    (only_file,) = list(batches_dir.glob("*.jsonl.gz"))
    body = only_file.read_bytes()

    # Simulate the crash-window superset: the SAME content re-appears under
    # a second, differently-numbered file (a stale pre-reconcile copy that
    # write-new-then-remove-stale had not gotten to removing yet).
    stray = batches_dir / "000000000099-stray.jsonl.gz"
    stray.write_bytes(body)

    events = list(read_events_mirror_batches())
    ids = [e.event_id for e in events]
    assert len(ids) == len(set(ids))
    assert len(events) == 2  # the two events from _append_round, not 4


def test_read_events_mirror_batches_raises_on_genuine_event_id_conflict(tmp_path):
    """If two files carry the SAME ``event_id`` but genuinely different
    replay-relevant content -- real corruption, not the reclaim crash
    window above -- the reader must raise rather than silently pick one
    copy; arbitrating between conflicting copies is not this function's
    job."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _append_round(repo, 1, "conflict")
    sync_events_mirror(repo, repo_id="proj-conflict")

    batches_dir = bucket_dir() / "events" / "v1" / "batches"
    (only_file,) = list(batches_dir.glob("*.jsonl.gz"))
    event_dict = json.loads(gzip.decompress(only_file.read_bytes()).decode("utf-8").strip())
    event_dict["trace_id"] = "corrupted-tampering"  # content-material field; event_id left stale

    stray = batches_dir / "000000000099-stray.jsonl.gz"
    stray.write_bytes(_gzip_deterministic((json.dumps(event_dict) + "\n").encode("utf-8")))

    with pytest.raises(ValueError, match="conflicting copies"):
        list(read_events_mirror_batches())
