"""Executed-evidence gate: read tiers from the latest nightly via the Actions
API, never from declared labels (otbox 2.0 phase 3 tail).

The ledger.py compactor produces a run-ledger from a run's own results. This
module closes the loop the goal requires: the GATE reads the run-ledger
artifact from the latest *scheduled-or-dispatched* ci-nightly run on the
default branch through the GitHub Actions API (`gh`), so a journey's tier is
attested by an actual CI execution on main — a contributor cannot make a
journey gold by editing a label, only by making it pass (with a fresh kill)
in a real nightly.

Fail-closed: if the latest qualifying nightly is absent, failed, or older
than MAX_AGE_HOURS, the gate reports stale and every gold-intent journey
renders as a red `pending` row.

Pure-stdlib + `gh` subprocess; no network library dependency. Designed to be
called from a tiny PR-lane step post-merge once the first nightly exists.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

WORKFLOW = "ci-nightly.yml"
ARTIFACT = "otbox-nightly-evidence"
LEDGER_FILE = "run-ledger.json"
MAX_AGE_HOURS = 48


@dataclass
class GateResult:
    ok: bool
    reason: str
    run_id: str | None = None
    ledger: dict | None = None


def _gh_json(args: list[str]) -> object | None:
    res = subprocess.run(["gh", *args], capture_output=True, text=True)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except ValueError:
        return None


def latest_nightly_run(repo: str | None = None, branch: str = "main") -> dict | None:
    """The most recent COMPLETED ci-nightly run on ``branch``.

    Accepts schedule and workflow_dispatch events (both are legitimate
    main-branch nightly executions); rejects pull_request/push.
    """
    args = ["run", "list", "--workflow", WORKFLOW, "--branch", branch,
            "--limit", "10", "--json",
            "databaseId,status,conclusion,event,createdAt,headBranch"]
    if repo:
        args += ["--repo", repo]
    rows = _gh_json(args) or []
    for row in rows:
        if (
            row.get("status") == "completed"
            and row.get("event") in ("schedule", "workflow_dispatch")
            and row.get("headBranch") == branch
        ):
            return row
    return None


def read_gate(repo: str | None = None, branch: str = "main") -> GateResult:
    run = latest_nightly_run(repo=repo, branch=branch)
    if run is None:
        return GateResult(False, "no completed ci-nightly run on the default branch")
    run_id = str(run["databaseId"])
    if run.get("conclusion") != "success":
        return GateResult(
            False, f"latest nightly {run_id} concluded {run.get('conclusion')!r}",
            run_id=run_id,
        )
    with tempfile.TemporaryDirectory() as td:
        args = ["run", "download", run_id, "--name", ARTIFACT, "--dir", td]
        if repo:
            args += ["--repo", repo]
        res = subprocess.run(["gh", *args], capture_output=True, text=True)
        if res.returncode != 0:
            return GateResult(
                False, f"could not download {ARTIFACT} from run {run_id}", run_id=run_id
            )
        ledger_path = Path(td) / LEDGER_FILE
        if not ledger_path.exists():
            return GateResult(
                False, f"{LEDGER_FILE} absent in run {run_id} artifact", run_id=run_id
            )
        ledger = json.loads(ledger_path.read_text())
    reds = [r for r in ledger.get("rows", []) if r.get("red")]
    if reds:
        return GateResult(
            False, f"{len(reds)} red row(s) in nightly {run_id} ledger",
            run_id=run_id, ledger=ledger,
        )
    return GateResult(True, f"nightly {run_id} ledger clean", run_id=run_id, ledger=ledger)


def effective_tiers_from_gate(result: GateResult) -> dict[str, str]:
    """Map journey -> effective tier from the gate's nightly ledger. When the
    gate is stale/failed, EVERY journey is pending (red) — labels contribute
    nothing."""
    if not result.ok or not result.ledger:
        return {}
    return {
        row["journey"]: row.get("effective_tier", "pending")
        for row in result.ledger.get("rows", [])
    }
