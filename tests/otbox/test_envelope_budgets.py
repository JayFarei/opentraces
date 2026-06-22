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

Measurement is path-normalized (issue #51): absolute box paths embedded
in envelopes (e.g. ``search_snapshot.path``) are collapsed to ``<ROOT>``
via the shared ``tests/envelope_measure.py`` helper before counting, so
identical envelopes measure identically across checkouts. Normalization
only SHRINKS measured values, so all previously committed budgets stay
valid without a re-seed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.envelope_measure import approx_tokens

from .checkpoints import resolve_checkpoint
from .drivers import get_driver

BUDGETS_PATH = Path(__file__).parent / "envelope_budgets.json"

_CHECKPOINT = "c-captured-real-session"


def _render(part: str, subs: dict[str, str]) -> str:
    for key, value in subs.items():
        part = part.replace(key, value)
    return part


def _first_ctx_node_id(driver, box, trace_id: str) -> str:
    """Probe the captured world for a real ContextNode id.

    ``ctx show`` needs a node id that no checkpoint audit field carries;
    resolve it the way an agent would — ``ctx tree <trace> --json`` and
    take the first node on the active path. A failed probe returns ""
    so the budget row runs with an empty argument and fails loudly in
    ``test_budgeted_surfaces_run`` (never a silent skip).
    """
    res = driver.exec(box, [*driver.cli_argv(box), "ctx", "tree", trace_id, "--json"])
    if res.returncode != 0:
        return ""
    try:
        nodes = json.loads(res.stdout).get("nodes") or []
    except ValueError:
        return ""
    if not nodes:
        return ""
    return str(nodes[0].get("node_id") or "")


@pytest.fixture(scope="module")
def measured():
    budgets = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    driver = get_driver("local")
    cp = resolve_checkpoint(driver, _CHECKPOINT)
    box = cp.box
    try:
        audit = box.notes.get("c_captured_session_audit") or {}
        # Placeholder vocabulary available to envelope_budgets.json argv
        # templates. Audit-derived values address the captured trace; the
        # ctx node id is probe-resolved (see _first_ctx_node_id).
        subs = {
            "{trace_id}": str(audit.get("trace_id") or ""),
            "{commit_sha}": str(audit.get("commit_sha") or ""),
            "{edit_step_index}": str(audit.get("edit_step_index") or ""),
        }
        subs["{ctx_node_id}"] = _first_ctx_node_id(
            driver, box, subs["{trace_id}"]
        )
        # Path-normalized measurement (issue #51): both raw and resolved
        # forms of each box root (macOS /var vs /private/var).
        roots: list[str] = []
        for raw in (str(box.root), str(box.home)):
            roots.append(raw)
            try:
                roots.append(str(Path(raw).resolve()))
            except OSError:
                pass
        out: dict[str, dict] = {}
        for label, spec in budgets.items():
            argv = [_render(part, subs) for part in spec["argv"]]
            res = driver.exec(box, [*driver.cli_argv(box), *argv])
            out[label] = {
                "rc": res.returncode,
                "tokens": approx_tokens(res.stdout or "", roots),
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
    # Budget every surface that produced its envelope, regardless of exit code
    # (see test_budgeted_surfaces_run for why a non-zero exit can be a valid
    # run). A crashed surface has 0 tokens < budget, so it never trips this;
    # this also budget-checks surfaces that legitimately exit non-zero.
    over = {
        label: row
        for label, row in measured.items()
        if row["tokens"] > row["budget"]
    }
    assert not over, (
        "agent-facing envelopes exceeded their committed token budgets "
        "(raising a budget is a reviewed contract change in "
        f"envelope_budgets.json): {over}"
    )


def test_budgeted_surfaces_run(measured):
    # A surface "ran" if it emitted its envelope. A non-zero exit WITH real
    # output is a valid run for budget purposes — notably `doctor` exits 3 on a
    # captured-session sentinel whose deployed glue is older than the current
    # CLI (version-drift), which is the realistic captured-then-upgraded world
    # and still prints the full report. A genuine failure is a non-zero exit
    # with NO output (a crash). Exit-code correctness for doctor is covered by
    # the doctor journeys + unit tests, not here.
    failed = {
        label: row
        for label, row in measured.items()
        if row["rc"] != 0 and row["tokens"] == 0
    }
    assert not failed, f"budgeted surfaces failed to run (non-zero exit, no output): {failed}"
