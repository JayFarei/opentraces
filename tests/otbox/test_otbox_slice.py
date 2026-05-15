"""CI guard for otbox itself.

otbox is the test environment the team uses across this product's
development — so otbox needs its *own* regression guard. This module
proves the thin vertical slice (spec step 1-6) and sweeps every Tier 0
catalogue journey, all offline against the `local` driver.

Run just this file:

    .venv/bin/python -m pytest tests/otbox/test_otbox_slice.py -v
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from tests.otbox.drivers import get_driver
from tests.otbox.env import Box, ensure_state_root, new_box_id
from tests.otbox.journey import available_journeys, run_journey
from tests.otbox.seed import run_seed
from tests.otbox.snapshot import (
    create_snapshot,
    delete_snapshot,
    restore_snapshot,
)


# Override tests/conftest.py's autouse HOME-isolation fixture (same name
# = local override, per its own docstring). otbox boxes isolate HOME
# themselves, per box, via the driver — and test_zero_host_residue must
# see the *real* ~/.opentraces to be meaningful.
@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    yield


@pytest.fixture
def driver():
    return get_driver("local")


def _fresh_box(driver) -> Box:
    ensure_state_root()
    box = Box(box_id=new_box_id(), driver="local")
    driver.provision(box)
    box.save()
    return box


def _hash_dir_listing(path: Path) -> str:
    if not path.exists():
        return "absent"
    names = sorted(p.name for p in path.iterdir())
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def test_vertical_slice(driver):
    """Spec steps 1-6: up -> seed -> snapshot -> down -> restore -> journey."""
    snap_name = f"otbox-pytest-{os.getpid()}"
    box = _fresh_box(driver)
    origin_id = box.box_id
    restored: Box | None = None
    try:
        # 2. seed
        report = run_seed(driver, box, "smoke")
        assert report["scenario"] == "smoke"
        assert len(report["trace_ids"]) == 3
        assert (box.opentraces_dir / "projects").exists()

        # 3. snapshot + down
        info = create_snapshot(box, snap_name, overwrite=True)
        assert info.archive.exists()
        driver.teardown(box)
        assert not box.root.exists()

        # 4. restore — must land a usable, distinct box
        restored, restore_meta = restore_snapshot(snap_name)
        driver.provision(restored)
        assert restored.box_id != origin_id
        assert restored.seed == "smoke"
        assert restore_meta["paths_rewritten"] >= 1  # config + marker repointed

        # 5. real CLI journey against the restored world
        result = run_journey(driver, restored, "cli-publish-happy-path")
        assert result.verdict == "PASS", result.reason
        # publish actually populated the fake HF remote
        assert (restored.fake_remote / "otbox-smoke" / "dataset" / "dataset_infos.json").exists()
    finally:
        for b in (box, restored):
            if b is not None and b.root.exists():
                get_driver(b.driver).teardown(b)
        delete_snapshot(snap_name)


@pytest.mark.parametrize(
    "journey_name",
    [j["name"] for j in available_journeys() if j["tier"] == 0],
)
def test_tier0_catalogue_journey(driver, journey_name):
    """Every Tier 0 catalogue journey must PASS (or SKIP on a missing dep)."""
    from tests.otbox.checkpoints import resolve_checkpoint

    meta = {j["name"]: j for j in available_journeys()}[journey_name]
    from_checkpoints = meta.get("from_checkpoints") or []
    if from_checkpoints:
        # Plan 062: prefer the declared base checkpoint; ignore seed.
        cp_result = resolve_checkpoint(driver, from_checkpoints[0])
        box = cp_result.box
    else:
        box = _fresh_box(driver)
        run_seed(driver, box, meta["seed"] or "smoke")
    try:
        result = run_journey(driver, box, journey_name)
        assert result.verdict in ("PASS", "SKIP"), f"{journey_name}: {result.reason}"
    finally:
        if box.root.exists():
            driver.teardown(box)


def test_zero_host_residue(driver):
    """A full up/seed/journey/down cycle must not mutate the real home."""
    real_home = Path(os.path.expanduser("~"))
    real_projects = real_home / ".opentraces" / "projects"
    real_config = real_home / ".opentraces" / "config.json"

    before_projects = _hash_dir_listing(real_projects)
    before_config = (
        hashlib.sha256(real_config.read_bytes()).hexdigest()
        if real_config.exists()
        else "absent"
    )

    box = _fresh_box(driver)
    try:
        run_seed(driver, box, "smoke")
        run_journey(driver, box, "cli-publish-happy-path")
    finally:
        driver.teardown(box)

    assert _hash_dir_listing(real_projects) == before_projects, "real ~/.opentraces/projects changed"
    after_config = (
        hashlib.sha256(real_config.read_bytes()).hexdigest()
        if real_config.exists()
        else "absent"
    )
    assert after_config == before_config, "real ~/.opentraces/config.json changed"
    assert not box.root.exists(), "box root left behind after teardown"
