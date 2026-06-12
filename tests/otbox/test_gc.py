"""Tests for otbox box-leak teardown + `otbox gc` sweep (issue #53).

Two layers under test:

* in-run teardown — a failed checkpoint cold build and a matrix error
  row must not leak partial boxes under ``.otbox/boxes/``;
* the ``gc`` sweep — killed-run residue (dead-pid boxes, meta-less
  stubs, aged ``_capture-refresh-*`` snapshots) is collectable, while
  the current box, live-pid boxes, fresh boxes, and the
  ``_checkpoint-*`` cache are never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from tests.otbox.gc import sweep


# ---------------------------------------------------------------------------
# in-run teardown (red-without-fix tests)
# ---------------------------------------------------------------------------
def test_resolve_checkpoint_failure_tears_down_partial_box():
    """A failed cold build must not leak its partially built box."""
    from tests.otbox.checkpoints import (
        REGISTRY,
        Checkpoint,
        register,
        resolve_checkpoint,
    )
    from tests.otbox.drivers import get_driver
    from tests.otbox.env import BOXES_DIR, ensure_state_root

    def _boom(driver, box):  # noqa: ARG001 - builder signature
        raise RuntimeError("boom: deliberate builder failure")

    register(Checkpoint(name="c-gc-test-boom", builder=_boom, cache=False))
    ensure_state_root()
    before = set(BOXES_DIR.iterdir())
    try:
        with pytest.raises(RuntimeError, match="boom"):
            resolve_checkpoint(get_driver("local"), "c-gc-test-boom")
        assert set(BOXES_DIR.iterdir()) == before, (
            "failed checkpoint build leaked a partial box"
        )
    finally:
        REGISTRY.pop("c-gc-test-boom", None)
        # Hygiene even when the assertion fails (pre-fix RED run): never
        # leave the throwaway box behind in the working checkout.
        for entry in set(BOXES_DIR.iterdir()) - before:
            shutil.rmtree(entry, ignore_errors=True)


def test_matrix_error_row_tears_down_box(monkeypatch):
    """A journey that raises mid-run must still tear its box down."""
    from tests.otbox import matrix
    from tests.otbox.checkpoints import CheckpointResult
    from tests.otbox.drivers import get_driver
    from tests.otbox.env import Box, new_box_id

    driver = get_driver("local")
    box = Box(box_id=new_box_id(), driver="local")
    driver.provision(box)
    box.save()

    monkeypatch.setattr(
        matrix,
        "available_journeys",
        lambda: [
            {
                "name": "gc-test-journey",
                "description": "",
                "lane": "core",
                "tier": 0,
                "seed": None,
                "from_checkpoints": ["c-gc-test-base"],
                "persona": "agent",
                "requires": [],
                "preconditions": {},
                "tier_label": "bronze",
                "trajectories": [],
            }
        ],
    )
    monkeypatch.setattr(matrix, "_check_journey_trajectories", lambda _j: [])
    monkeypatch.setattr(
        matrix,
        "resolve_checkpoint",
        lambda _driver, name: CheckpointResult(
            name=name,
            box=box,
            cache_hit=False,
            cold_build_seconds=0.0,
            parent=None,
            snapshot_name="_checkpoint-gc-test",
        ),
    )

    def _raise(*_args, **_kwargs):
        raise RuntimeError("journey exploded")

    monkeypatch.setattr(matrix, "run_journey", _raise)

    try:
        report = matrix.run_matrix(driver, journey_pattern="gc-test-journey")
        assert report.error_count == 1
        assert report.rows[0].verdict == "ERROR"
        assert "journey exploded" in report.rows[0].reason
        assert not box.root.exists(), "matrix error row leaked its box"
    finally:
        driver.teardown(box)


# ---------------------------------------------------------------------------
# gc sweep (pure tmp_path, default-CI-safe)
# ---------------------------------------------------------------------------
_OLD_ISO = "2026-01-01T00:00:00+00:00"


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _make_box_dir(
    boxes_dir,
    box_id: str,
    *,
    owner_pid: int | None = None,
    meta: bool = True,
    aged: bool = True,
):
    root = boxes_dir / box_id
    (root / "home").mkdir(parents=True)
    if meta:
        (root / "meta.json").write_text(
            json.dumps(
                {
                    "box_id": box_id,
                    "driver": "local",
                    "created": _OLD_ISO if aged else None,
                    "owner_pid": owner_pid,
                    "notes": {},
                }
            )
        )
    if aged:
        old = time.time() - 48 * 3600
        os.utime(root, (old, old))
    return root


@pytest.fixture()
def state(tmp_path):
    boxes = tmp_path / "boxes"
    snaps = tmp_path / "snapshots"
    boxes.mkdir()
    snaps.mkdir()
    return boxes, snaps


def test_gc_sweeps_aged_dead_pid_box(state):
    boxes, snaps = state
    root = _make_box_dir(boxes, "otb_dead", owner_pid=_dead_pid())
    result = sweep(
        boxes_dir=boxes, snapshots_dir=snaps, current_box_id=None
    )
    assert result["removed_boxes"] == ["otb_dead"]
    assert not root.exists()


def test_gc_keeps_live_pid_box(state):
    boxes, snaps = state
    root = _make_box_dir(boxes, "otb_live", owner_pid=os.getpid())
    result = sweep(
        boxes_dir=boxes, snapshots_dir=snaps, current_box_id=None
    )
    assert result["removed_boxes"] == []
    assert root.exists()
    assert any(
        k["box_id"] == "otb_live" and "alive" in k["reason"]
        for k in result["kept"]
    )


def test_gc_keeps_current_box_even_when_aged_and_dead(state):
    boxes, snaps = state
    root = _make_box_dir(boxes, "otb_current", owner_pid=_dead_pid())
    result = sweep(
        boxes_dir=boxes, snapshots_dir=snaps, current_box_id="otb_current"
    )
    assert result["removed_boxes"] == []
    assert root.exists()
    assert result["kept"] == [{"box_id": "otb_current", "reason": "current box"}]


def test_gc_keeps_fresh_box_inside_age_grace(state):
    boxes, snaps = state
    root = _make_box_dir(boxes, "otb_fresh", owner_pid=_dead_pid(), aged=False)
    result = sweep(
        boxes_dir=boxes, snapshots_dir=snaps, current_box_id=None
    )
    assert result["removed_boxes"] == []
    assert root.exists()
    assert any(
        k["box_id"] == "otb_fresh" and "grace" in k["reason"]
        for k in result["kept"]
    )


def test_gc_sweeps_aged_metaless_stub(state):
    """The `down --all` blind spot: crash stubs with no meta.json."""
    boxes, snaps = state
    root = _make_box_dir(boxes, "otb_stub", meta=False)
    result = sweep(
        boxes_dir=boxes, snapshots_dir=snaps, current_box_id=None
    )
    assert result["removed_boxes"] == ["otb_stub"]
    assert not root.exists()


def test_gc_dry_run_reports_without_deleting(state):
    boxes, snaps = state
    root = _make_box_dir(boxes, "otb_dead", owner_pid=_dead_pid())
    old = time.time() - 48 * 3600
    snap = snaps / "_capture-refresh-foo-otb_dead.tar.gz"
    snap.write_bytes(b"x")
    os.utime(snap, (old, old))
    result = sweep(
        boxes_dir=boxes, snapshots_dir=snaps, current_box_id=None, dry_run=True
    )
    assert result["dry_run"] is True
    assert result["removed_boxes"] == ["otb_dead"]
    assert result["removed_snapshots"] == ["_capture-refresh-foo-otb_dead.tar.gz"]
    assert root.exists()
    assert snap.exists()


def test_gc_sweeps_aged_capture_refresh_snapshots_only(state):
    boxes, snaps = state
    old = time.time() - 48 * 3600
    aged_capture = snaps / "_capture-refresh-scenario-otb_x.tar.gz"
    aged_capture_meta = snaps / "_capture-refresh-scenario-otb_x.json"
    fresh_capture = snaps / "_capture-refresh-scenario-otb_y.tar.gz"
    checkpoint = snaps / "_checkpoint-c-empty-abc123def456.tar.gz"
    user_named = snaps / "my-snapshot.tar.gz"
    for p in (aged_capture, aged_capture_meta, fresh_capture, checkpoint, user_named):
        p.write_bytes(b"x")
    for p in (aged_capture, aged_capture_meta, checkpoint, user_named):
        os.utime(p, (old, old))
    result = sweep(
        boxes_dir=boxes, snapshots_dir=snaps, current_box_id=None
    )
    assert result["removed_snapshots"] == [
        "_capture-refresh-scenario-otb_x.json",
        "_capture-refresh-scenario-otb_x.tar.gz",
    ]
    assert not aged_capture.exists()
    assert not aged_capture_meta.exists()
    assert fresh_capture.exists()
    assert checkpoint.exists()
    assert user_named.exists()


def test_box_roundtrips_owner_pid():
    from tests.otbox.env import Box

    box = Box(box_id="otb_pidtest")
    assert box.owner_pid == os.getpid()
    data = box.to_dict()
    assert data["owner_pid"] == os.getpid()
    assert Box.from_dict(data).owner_pid == os.getpid()
    # Legacy meta without the key → None, never a crash.
    legacy = {k: v for k, v in data.items() if k != "owner_pid"}
    assert Box.from_dict(legacy).owner_pid is None
