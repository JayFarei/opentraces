"""Plan 063 SSoT gate — lock the inventory/matrix drift checks into CI.

Three gates from plan 063 §A.3 (the deliverable's enforcement contract):

1. Every visible non-group Click command is mapped in 063.
2. Every 063 row corresponds to a Click command in the live registry.
3. Every journey TOML's declared trajectory is in 063's §1 inventory.

Plus the coverage gate — every non-deprecated, non-auto core-lane
command must be owned by at least one journey. With routine-22
coverage now closed, this gate is strict in CI.

These tests intentionally do NOT spin up a box. They parse 063 + walk
the Click registry + read every journey TOML, then assert ``drift.ok``.
The whole sweep runs in milliseconds and fails CI loudly the moment a
new Click command appears without a 063 row, a journey names an
unknown trajectory, or a 063 row references a non-existent Click
command.
"""

from __future__ import annotations

import pytest

from tests.otbox.inventory import build_inventory
from tests.otbox.jtbd import JTBD_PATH


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """Override conftest.py's HOME-isolation; SSoT checks don't touch HOME."""
    yield


def test_jtbd_drift_check_passes_strict():
    """The Click ↔ 063 ↔ journey-TOML triple-SSoT check must be clean."""
    import tempfile
    from pathlib import Path

    if not JTBD_PATH.exists():
        pytest.skip(f"kb checkout not present: {JTBD_PATH}")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tf:
        out = Path(tf.name)
    try:
        _, summary, drift = build_inventory(out_path=out, strict=True)
    finally:
        out.unlink(missing_ok=True)

    assert drift.missing_from_063 == [], (
        "Click commands missing from plan 063 — add them to the per-bucket "
        f"tables (§2..§7) of kb/plans/063-jtbd-command-map.md:\n"
        + "\n".join(f"  {p}" for p in drift.missing_from_063)
    )
    assert drift.phantom_in_063 == [], (
        "Plan 063 references commands not in the live Click registry — "
        "either fix the spelling in 063 or remove the row:\n"
        + "\n".join(f"  {p}" for p in drift.phantom_in_063)
    )
    assert drift.unknown_trajectories == [], (
        "Journey TOMLs name trajectories not in plan 063 §1 — add the "
        "trajectory to §1 or fix the journey's `trajectories = [...]`:\n"
        + "\n".join(f"  {j} → {t}" for j, t in drift.unknown_trajectories)
    )
    assert drift.unowned_commands == [], (
        "Core-lane Click commands have no owning journey — write a journey "
        "TOML under tests/otbox/catalogue/journeys/ that exercises:\n"
        + "\n".join(f"  {p}" for p in drift.unowned_commands)
    )

    # Plan 069 R5: tiered coverage gate. Trajectories listed in
    # AGENT_FACING_TRAJECTORIES_MIN_GOLD must be covered by at least one
    # ``tier_label = "gold"`` journey. The initial vocabulary is empty
    # so this assertion is a no-op until plan 070 starts populating it.
    assert drift.coverage_below_required_tier == [], (
        "Agent-facing trajectories below their required tier — add a "
        "`tier_label = \"gold\"` journey that exercises:\n"
        + "\n".join(
            f"  {t} (current {cur}, required {req})"
            for t, cur, req in drift.coverage_below_required_tier
        )
    )

    assert summary["public_owned"] >= 60, (
        f"plan 063 coverage regressed below 60/97 owned "
        f"(currently {summary['public_owned']}/{summary['public_total']})"
    )
