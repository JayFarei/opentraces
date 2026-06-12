"""Issue #50 — otbox snapshot ``.partial`` name races across concurrent runs.

Two concurrent pytest runs that both cold-build the same content-addressed
checkpoint open the SAME deterministic ``.partial`` temp path. The first
writer's ``tmp.replace(archive)`` consumes the shared path; the losing
writer raises ``FileNotFoundError``. The fix gives every writer a unique
temp path (``<name>.<pid>-<rand>.partial``) so each rename moves a complete
archive that writer alone wrote — last-write-wins, never interleaved.

Hermetic: everything happens under ``tmp_path``; no box provisioning,
no network, no real ``.otbox`` state root.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

import tests.otbox.env as env_mod
import tests.otbox.snapshot as snapshot_mod
from tests.otbox.env import Box

REAL_TARFILE_OPEN = tarfile.open


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """Point the snapshot module and box layout at tmp_path."""
    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()
    boxes_dir = tmp_path / "boxes"
    boxes_dir.mkdir()
    # snapshot.py imports SNAPSHOTS_DIR by value; Box.root reads
    # env.BOXES_DIR at call time.
    monkeypatch.setattr(snapshot_mod, "SNAPSHOTS_DIR", snapshots_dir)
    monkeypatch.setattr(snapshot_mod, "ensure_state_root", lambda: None)
    monkeypatch.setattr(env_mod, "BOXES_DIR", boxes_dir)
    return snapshots_dir


def _make_box(marker: str) -> Box:
    box = Box(box_id=env_mod.new_box_id())
    box.root.mkdir(parents=True)
    (box.root / "marker.txt").write_text(marker)
    return box


class _InterleavingTar:
    """Wraps a real tarfile; runs a callback after close, before rename."""

    def __init__(self, real_tar, on_closed):
        self._real = real_tar
        self._on_closed = on_closed

    def __enter__(self):
        return self._real.__enter__()

    def __exit__(self, *exc):
        result = self._real.__exit__(*exc)
        self._on_closed()
        return result


def test_two_writer_interleave_same_snapshot_name_last_write_wins(
    sandbox, monkeypatch
):
    """Writer B's full create_snapshot runs in the window between writer A
    closing its tar and A's ``tmp.replace(archive)``.

    Without per-writer temps: B renames the shared ``.partial`` away and A
    dies with FileNotFoundError. With the fix: no exception, A replaced
    last, archive is complete and valid.
    """
    snapshots_dir = sandbox
    box_a = _make_box("A\n")
    box_b = _make_box("B\n")

    fired = {"done": False}

    def _interleave_b():
        if fired["done"]:
            return
        fired["done"] = True
        # Writer B runs to completion inside A's close→rename window.
        snapshot_mod.create_snapshot(box_b, "race-snap", overwrite=True)

    def patched_open(*args, **kwargs):
        return _InterleavingTar(REAL_TARFILE_OPEN(*args, **kwargs), _interleave_b)

    monkeypatch.setattr(snapshot_mod.tarfile, "open", patched_open)

    # Without the fix this raises FileNotFoundError from tmp.replace(archive).
    info = snapshot_mod.create_snapshot(box_a, "race-snap", overwrite=True)

    assert snapshot_mod.snapshot_exists("race-snap")
    loaded = snapshot_mod.load_snapshot("race-snap")
    assert loaded.name == "race-snap"

    # The final archive is a complete, valid tar whose marker is A's —
    # A replaced last under this interleave (deterministic last-write-wins).
    assert info.archive.exists()
    with REAL_TARFILE_OPEN(info.archive, "r:gz") as tar:
        member = tar.extractfile("./marker.txt")
        assert member is not None
        assert member.read().decode() == "A\n"

    # No .partial residue left behind by either writer.
    assert list(snapshots_dir.glob("*.partial")) == []


def test_unique_partial_path_per_writer(sandbox):
    target = snapshot_mod._archive_path("some-snap")
    p1 = snapshot_mod._unique_partial_path(target)
    p2 = snapshot_mod._unique_partial_path(target)
    assert p1 != p2
    for p in (p1, p2):
        assert p.parent == sandbox
        assert p.name.endswith(".partial")
        # A leftover temp must never satisfy the snapshot-listing glob.
        assert not p.match("*.tar.gz")


def test_create_snapshot_unlinks_partial_on_failure(sandbox, monkeypatch):
    box = _make_box("X\n")

    def failing_open(path, *args, **kwargs):
        # Simulate a tar that dies mid-build: the temp file exists on disk
        # before the failure surfaces.
        Path(path).write_bytes(b"junk")
        raise OSError("simulated mid-build tar failure")

    monkeypatch.setattr(snapshot_mod.tarfile, "open", failing_open)

    with pytest.raises(OSError, match="simulated mid-build tar failure"):
        snapshot_mod.create_snapshot(box, "broken-snap", overwrite=True)

    assert list(sandbox.glob("*.partial")) == []
    assert not snapshot_mod.snapshot_exists("broken-snap")
