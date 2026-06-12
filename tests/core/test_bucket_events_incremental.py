"""#65 — events-mirror sync reads only the appended suffix.

``sync_events_mirror`` was write-incremental but READ-full: every changed
tick materialised the whole log to filter it down to the suffix. These tests
pin that the incremental path (a) never calls the full reader and (b) writes
batch files byte-identical to a from-scratch rebuild (the replay-equals-git
invariant).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import opentraces.core.trails as trails_pkg
from opentraces.core.bucket_events import sync_events_mirror
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
