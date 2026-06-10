"""Mutation kills: every guard journey must fail under its fault (phase 5).

The anti-vacuity layer. For each guarantees.toml entry: build the checkpoint
world, apply the fault, run the guard journey, and REQUIRE a non-PASS
verdict. A guard that stays green when its guarantee is broken is vacuous
and fails here. The healthy direction (guard passes on an unfaulted world)
is already covered by the tier-0 sweep.
"""

from __future__ import annotations

import pytest

from .drivers import get_driver
from .journey import available_journeys, run_journey
from .mutations import FAULTS, apply_fault, load_guarantees


@pytest.fixture(scope="module")
def driver():
    return get_driver("local")


def _record_kill(guard: str, fault: str) -> None:
    """Record a witnessed kill for the executed-evidence ledger, keyed to
    the guard's CURRENT content hash — the kill expires the moment the
    journey's semantics change (assertion-gutting-after-kill is pointless)."""
    import json
    import os
    from pathlib import Path

    out_dir = os.environ.get("OTBOX_LEDGER_DIR")
    if not out_dir:
        return
    from .journey import CATALOGUE_DIR
    from .ledger import journey_content_hash

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    record = {
        "journey": guard,
        "fault": fault,
        "content_hash": journey_content_hash(CATALOGUE_DIR / f"{guard}.toml"),
    }
    (Path(out_dir) / f"{guard}.kill.json").write_text(json.dumps(record) + "\n")


def test_guarantees_file_is_coherent():
    kills = load_guarantees()
    assert kills, "guarantees.toml is empty — the kill layer is not optional"
    names = {j["name"] for j in available_journeys()}
    for k in kills:
        assert k.fault in FAULTS, f"{k.guarantee}: unknown fault {k.fault!r}"
        unknown = [g for g in k.guards if g not in names]
        assert not unknown, f"{k.guarantee}: unknown guards {unknown}"


@pytest.mark.parametrize(
    "kill", load_guarantees(), ids=lambda k: f"{k.fault}->{'+'.join(k.guards)}"
)
def test_guard_goes_red_under_fault(driver, kill):
    from .checkpoints import resolve_checkpoint

    cp = resolve_checkpoint(driver, kill.checkpoint)
    box = cp.box
    try:
        note = apply_fault(kill.fault, driver, box)
        for guard in kill.guards:
            result = run_journey(driver, box, guard)
            assert result.verdict not in ("PASS", "SKIP"), (
                f"VACUOUS GUARD: {guard} stayed {result.verdict} while the world "
                f"had fault {kill.fault!r} ({note}) breaking guarantee: "
                f"{kill.guarantee}"
            )
            _record_kill(guard, kill.fault)
    finally:
        if box.root.exists():
            driver.teardown(box)
