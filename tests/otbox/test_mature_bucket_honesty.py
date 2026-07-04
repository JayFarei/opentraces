"""World-honesty guard for the ``c-mature-bucket`` scale world (issue #213, W5).

Loopcraft condition #4: ``verify_provides`` must REFUSE to cache a mature world
whose outlier ``trail.jsonl.gz`` companion is below the declared floor — a
dishonest world (cloned envelopes with no genuine large companion) would let
pre-fix, O(companion) seal code short-circuit and stay green, defeating the
whole point of the checkpoint. This proves the refusal mechanism directly and
cheaply (a real on-disk file measured against the floor), rather than paying a
14-minute cold build to observe it.

The full-build observation is real too: during development a build whose outlier
companion was wiped to 3056 bytes was refused with exactly this error, before
the post-repair companion-writer fix landed.
"""

from __future__ import annotations

import gzip
import shutil

import pytest

from tests.otbox.checkpoints import Checkpoint, CheckpointError, verify_provides
from tests.otbox.drivers.local import LocalDriver
from tests.otbox.env import Box, ensure_state_root, new_box_id
from tests.otbox.probes import _probe_outlier_trail_companion_min_bytes

_FLOOR = 50 * 1024 * 1024  # the checkpoint's declared 50 MB outlier floor


@pytest.fixture()
def box_with_small_trail():
    if shutil.which("bash") is None:  # pragma: no cover - unix dev/CI only
        pytest.skip("bash not available")
    ensure_state_root()
    driver = LocalDriver()
    box = Box(box_id=new_box_id(), driver="local")
    driver.provision(box)
    box.save()
    # Plant a SMALL trail companion where the probe looks
    # ($HOME/.opentraces/bucket/traces/v1/<proj>/<trace>/trail.jsonl.gz).
    trace_dir = box.home / ".opentraces" / "bucket" / "traces" / "v1" / "proj" / "t0"
    trace_dir.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=open(trace_dir / "trail.jsonl.gz", "wb"), mtime=0
    ) as gz:
        gz.write(b'{"event": "small"}\n')
    try:
        yield driver, box
    finally:
        if box.root.exists():
            driver.teardown(box)


def test_outlier_probe_fails_below_floor_passes_above(box_with_small_trail):
    driver, box = box_with_small_trail
    # A small companion is BELOW the real 50 MB floor -> probe fails (dishonest).
    ok, message = _probe_outlier_trail_companion_min_bytes(driver, box, _FLOOR)
    assert not ok, message
    assert "dishonest" in message
    # The SAME file clears a tiny floor -> probe passes (it measures the real
    # file, it does not trust a claim).
    ok2, message2 = _probe_outlier_trail_companion_min_bytes(driver, box, 10)
    assert ok2, message2


def test_verify_provides_refuses_undersized_world(box_with_small_trail):
    driver, box = box_with_small_trail
    # A checkpoint advertising the 50 MB outlier floor over this small-companion
    # world must be REFUSED (never cached) — the honesty guard.
    lying = Checkpoint(
        name="c-mature-bucket-undersized-fixture",
        provides={"outlier_trail_companion_min_bytes": _FLOOR},
    )
    with pytest.raises(CheckpointError) as exc:
        verify_provides(driver, box, lying)
    assert "refusing to cache a lying world" in str(exc.value)
    assert "outlier_trail_companion_min_bytes" in str(exc.value)

    # The honest world (companion clears the declared floor) is accepted.
    honest = Checkpoint(
        name="c-mature-bucket-honest-fixture",
        provides={"outlier_trail_companion_min_bytes": 10},
    )
    verify_provides(driver, box, honest)  # must not raise
