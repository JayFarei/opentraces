"""The nightly `scale` CI lane (issue #213, seal-family W5).

The `scale` lane cold-builds the ~600-trace / ~50K-event `c-mature-bucket`
world (5-12 min / 1.5-4 GB) and runs the `mature-bucket-perf` perf recurrence
guard against it. This is a CATASTROPHIC-regression catch for the O(corpus)
class (#87/#121/#137/#208) that is literally never executed on the 1-2 trace
per-PR checkpoints.

It is EXPENSIVE and OFF by default: gated behind ``OT_OTBOX_SCALE=1`` and run
via ``make otbox-scale`` in the nightly workflow only — never on the per-PR
gate, never in default ``pytest tests/otbox/``.
"""

from __future__ import annotations

import os

import pytest

from tests.otbox.drivers import get_driver
from tests.otbox.journey import available_journeys, run_journey

_SCALE_ENABLED = os.environ.get("OT_OTBOX_SCALE") == "1"

_SCALE_JOURNEYS = [
    j["name"] for j in available_journeys() if j.get("ci_lane") == "scale"
]


pytestmark = pytest.mark.skipif(
    not _SCALE_ENABLED,
    reason="scale lane is opt-in: set OT_OTBOX_SCALE=1 (via `make otbox-scale`)",
)


@pytest.fixture(scope="module")
def driver():
    return get_driver("local")


def test_scale_lane_is_non_empty():
    # A silent empty lane would be graveyard formation — the guard must exist.
    assert _SCALE_JOURNEYS, "no journeys declare ci_lane='scale'"


@pytest.mark.parametrize("journey_name", _SCALE_JOURNEYS)
def test_scale_journey(driver, journey_name):
    """Cold-build the mature world once and PASS the perf guard.

    Precondition #5 (issue #213): a precondition failure is an ERROR, never a
    silent SKIP — a dishonest / undersized world must fail loudly here.
    """
    from tests.otbox.checkpoints import resolve_checkpoint

    meta = {j["name"]: j for j in available_journeys()}[journey_name]
    from_checkpoints = meta.get("from_checkpoints") or []
    assert from_checkpoints, f"{journey_name}: scale journeys must pin a checkpoint"

    cp_result = resolve_checkpoint(driver, from_checkpoints[0])
    box = cp_result.box
    try:
        result = run_journey(driver, box, journey_name)
        if result.verdict != "PASS":
            tag = f"[otbox:{journey_name}]"
            print(f"\n{tag} verdict={result.verdict} reason={result.reason}", flush=True)
            for s in result.steps:
                if not s.ok:
                    res = s.result
                    tail = ((res.stderr or res.stdout) if res else "")[-500:]
                    print(
                        f"{tag} STEP FAIL {s.step_id}: rc="
                        f"{res.returncode if res else '?'} {s.message} :: {tail}",
                        flush=True,
                    )
            for a in getattr(result, "assertions", []) or []:
                if not a.ok:
                    print(f"{tag} ASSERT FAIL [{a.kind}] {a.message[:240]}", flush=True)
        # An ERROR (precondition_unmet) or FAIL both hard-fail here — never a skip.
        assert result.verdict == "PASS", f"{journey_name}: {result.verdict}: {result.reason}"
    finally:
        # Keep the cached snapshot; only tear the forked box down.
        if box.root.exists():
            driver.teardown(box)
