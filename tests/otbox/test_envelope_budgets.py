"""B1-deterministic — envelope token budgets on the sentinel world.

"Bounded packets" becomes a number per command: every agent-facing
``--json`` read surface measured on the ``c-captured-real-session`` world
must stay inside its committed budget (``envelope_budgets.json``,
approx-tokens = stdout bytes / 4). A growing envelope is real context
cost for every agent driving the CLI — exceeding the budget is a
deliberate, reviewed decision (raise the committed number in the same
PR), never an accident.

Budgets were seeded at roughly 2x the measured size on the sentinel
world, so ordinary additive evolution fits and bloat regressions fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .checkpoints import resolve_checkpoint
from .drivers import get_driver

BUDGETS_PATH = Path(__file__).parent / "envelope_budgets.json"

_CHECKPOINT = "c-captured-real-session"


def _approx_tokens(text: str) -> int:
    return len(text) // 4


@pytest.fixture(scope="module")
def measured():
    budgets = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    driver = get_driver("local")
    cp = resolve_checkpoint(driver, _CHECKPOINT)
    box = cp.box
    try:
        audit = box.notes.get("c_captured_session_audit") or {}
        trace_id = str(audit.get("trace_id") or "")
        out: dict[str, dict] = {}
        for label, spec in budgets.items():
            argv = [
                part.replace("{trace_id}", trace_id) for part in spec["argv"]
            ]
            res = driver.exec(box, [*driver.cli_argv(box), *argv])
            out[label] = {
                "rc": res.returncode,
                "tokens": _approx_tokens(res.stdout or ""),
                "budget": int(spec["max_tokens"]),
            }
        return out
    finally:
        if box.root.exists():
            driver.teardown(box)


def test_budget_file_exists():
    assert BUDGETS_PATH.exists()
    budgets = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    assert budgets, "envelope_budgets.json must define at least one surface"
    for label, spec in budgets.items():
        assert spec.get("argv"), f"{label}: argv required"
        assert int(spec.get("max_tokens", 0)) > 0, f"{label}: max_tokens required"


def test_envelopes_within_budget(measured):
    over = {
        label: row
        for label, row in measured.items()
        if row["rc"] == 0 and row["tokens"] > row["budget"]
    }
    assert not over, (
        "agent-facing envelopes exceeded their committed token budgets "
        "(raising a budget is a reviewed contract change in "
        f"envelope_budgets.json): {over}"
    )


def test_budgeted_surfaces_run(measured):
    failed = {label: row for label, row in measured.items() if row["rc"] != 0}
    assert not failed, f"budgeted surfaces failed to run: {failed}"
