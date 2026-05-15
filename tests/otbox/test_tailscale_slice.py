"""Tier 1 vertical slice — opt-in pytest (plan 061 T1-M6).

Drives the full crabbox-style lease lifecycle against either:

  * a real tailnet-reachable target (set ``OT_OTBOX_SSH_TARGET`` to a
    host SSH understands), or
  * a per-test local sshd fixture spun up on a high port (no system
    "Remote Login" change needed) — this is the autonomous-delivery
    contract substrate, so a fresh agent can verify Tier 1 offline.

Always gated by ``OT_OTBOX_TIER1=1``. SKIPs cleanly otherwise — default
CI never runs this.

    OT_OTBOX_TIER1=1 .venv/bin/python -m pytest tests/otbox/test_tailscale_slice.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.otbox import _local_sshd
from tests.otbox.drivers.remote import RemoteDriver, TIER1_FLAG, tier1_enabled
from tests.otbox.env import Box, ensure_state_root, new_box_id
from tests.otbox.journey import run_journey
from tests.otbox.seed import run_seed
from tests.otbox.snapshot import delete_snapshot


# This module talks to real subprocesses against the working tree; opt
# out of conftest's autouse HOME monkeypatch.
@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    yield


@pytest.fixture(scope="module")
def tier1_substrate():
    """Return a dict of env vars that point the RemoteDriver at a target.

    Order of preference:
      1. ``OT_OTBOX_SSH_TARGET`` already set → use the operator's target.
      2. Spin up a local sshd on a high port and return its coords.
    """
    if not tier1_enabled():
        pytest.skip(f"set {TIER1_FLAG}=1 to opt into Tier 1 tests")

    operator_target = os.environ.get("OT_OTBOX_SSH_TARGET")
    if operator_target:
        yield {"target": operator_target, "owns_sshd": False}
        return

    sshd = _local_sshd.start()
    if sshd is None:
        pytest.skip("no sshd binary available to spin up the local Tier 1 fixture")
    env = {
        "OT_OTBOX_SSH_TARGET": sshd.target,
        "OT_OTBOX_SSH_PORT": str(sshd.port),
        "OT_OTBOX_SSH_KEY": str(sshd.client_key),
        # Same-machine fast path: reuse the repo venv as the remote venv
        # so we don't have to pip-install on every test run.
        "OT_OTBOX_REMOTE_VENV": str(Path(__file__).resolve().parents[2] / ".venv"),
        "OT_OTBOX_REMOTE_PYTHON": str(
            Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
        ),
    }
    for key, val in env.items():
        os.environ[key] = val
    try:
        yield {"target": sshd.target, "owns_sshd": True, **env}
    finally:
        sshd.stop()
        for key in env:
            os.environ.pop(key, None)


@pytest.fixture
def driver(tier1_substrate):
    ensure_state_root()
    return RemoteDriver()


def _fresh_box(driver) -> Box:
    box = Box(box_id=new_box_id(), driver="remote")
    driver.provision(box)
    box.save()
    return box


def test_tier1_vertical_slice(driver):
    """The 7-step slice end-to-end (spec section 'Thin Vertical Slice')."""
    snap_name = f"otbox-tier1-pytest-{os.getpid()}"
    b1 = _fresh_box(driver)              # step 1: warmup/provision
    b2: Box | None = None
    try:
        sync = driver.sync(b1)            # step 2
        assert sync.ok, f"sync rc={sync.returncode}: {sync.stderr[:200]}"

        report = run_seed(driver, b1, "smoke")   # step 3
        assert len(report["trace_ids"]) == 3

        info = driver.snapshot(b1, snap_name, overwrite=True)   # step 4
        assert info.archive.exists()
        # Tier 1 snapshots exclude source/+venv/, so the archive should
        # be small (under 1 MB) — proves the trim works.
        assert info.size_bytes < 1_000_000, f"snapshot too large: {info.size_bytes}B"

        driver.teardown(b1)
        assert not b1.root.exists()

        b2, meta = driver.restore(snap_name)          # step 5
        assert b2.box_id != b1.box_id
        assert meta["paths_rewritten"] >= 1
        assert meta["post_restore_sync"]["rc"] == 0

        result = run_journey(driver, b2, "cli-publish-happy-path")   # step 6
        assert result.verdict == "PASS", result.reason

        # step 7: artifacts — bundle uses the same code path as Tier 0
        from tests.otbox.artifacts import collect_artifacts

        out = collect_artifacts(b2, [result], run_label="tier1-slice")
        assert (out / "junit.xml").exists()
        assert (out / "transcripts").exists()
    finally:
        for b in (b1, b2):
            if b is not None:
                try:
                    driver.teardown(b)
                except Exception:  # noqa: BLE001
                    pass
        delete_snapshot(snap_name)


@pytest.mark.parametrize(
    "journey_name",
    [j["name"] for j in __import__("tests.otbox.journey", fromlist=["available_journeys"])
        .available_journeys() if j["tier"] == 1],
)
def test_tier1_catalogue_journey(driver, journey_name):
    """Every Tier 1 catalogue journey must PASS or SKIP cleanly."""
    from tests.otbox.journey import available_journeys

    meta = {j["name"]: j for j in available_journeys()}[journey_name]
    box = Box(box_id=new_box_id(), driver="remote")
    driver.provision(box)
    box.save()
    try:
        if meta.get("seed"):
            run_seed(driver, box, meta["seed"])
        result = run_journey(driver, box, journey_name)
        assert result.verdict in ("PASS", "SKIP"), f"{journey_name}: {result.reason}"
    finally:
        try:
            driver.teardown(box)
        except Exception:  # noqa: BLE001
            pass


def test_interchange_invariant_local_to_remote(driver, tier1_substrate):
    """Tier 0 snapshot must restore into a Tier 1 box (hard invariant R5)."""
    from tests.otbox.drivers import get_driver

    local = get_driver("local")
    local_box = Box(box_id=new_box_id(), driver="local")
    local.provision(local_box)
    local_box.save()
    snap = f"otbox-interchange-{os.getpid()}"
    restored: Box | None = None
    try:
        run_seed(local, local_box, "smoke")
        local.snapshot(local_box, snap, overwrite=True)

        # Critical: restore the local-driver snapshot into a remote box.
        restored, meta = driver.restore(snap)
        assert restored.driver == "remote"
        assert meta["paths_rewritten"] >= 1
        # The restored remote box has the seeded state from a Tier 0 snapshot.
        result = run_journey(driver, restored, "cli-publish-happy-path")
        assert result.verdict == "PASS", result.reason
    finally:
        for b in (local_box, restored):
            if b is not None:
                try:
                    get_driver(b.driver).teardown(b)
                except Exception:  # noqa: BLE001
                    pass
        delete_snapshot(snap)
