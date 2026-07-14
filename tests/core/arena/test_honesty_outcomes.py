from __future__ import annotations

import json
from pathlib import Path

import pytest

from opentraces.core.arena.box import Box, BoxCommandResult
from opentraces.core.arena.engine import Bench, ScenarioSource
from opentraces.core.arena.page import render_evidence_page
from opentraces.core.arena.run_store import RunStore


class Runtime:
    def lease(self):
        return Box("box", "box", "local-container", "container", "host", "user", "22", "key")

    def materialize(self, box, app_state, *, repository):
        return {"name": app_state, "digest": "sha256:state", "provides": ["python3"]}

    def exec(self, box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        return BoxCommandResult(list(argv), 0, "ok\n", "", {})

    def release(self, box):
        return None


def _bench(tmp_path: Path) -> Bench:
    scenario = tmp_path / "test_honesty.py"
    scenario.write_text('def test_honesty(bench):\n    """The runner reports truthfully."""\n')
    return Bench(
        source=ScenarioSource(
            "test_honesty.py::test_honesty",
            "The runner reports truthfully.",
            scenario,
            "test_honesty.py",
            "JayFarei/opentraces",
            "abc123",
            None,
            "clean",
            None,
        ),
        store=RunStore(tmp_path / "runs" / "v1"),
        box_runtime=Runtime(),
        repository_path=tmp_path,
    )


@pytest.mark.parametrize(
    ("outcome", "execution_status", "verdict", "reason_code", "page_word"),
    [
        ("pass", "complete", "pass", None, "PASS"),
        ("fail", "complete", "fail", "assertion_failed", "ASSERTION_FAILED"),
        ("skip", "complete", "skip", "absent_prerequisite", "ABSENT_PREREQUISITE"),
        ("error", "error", None, "machinery_error", "MACHINERY_ERROR"),
    ],
)
def test_four_honesty_outcomes_are_distinguishable_on_disk_and_page(
    tmp_path: Path,
    outcome: str,
    execution_status: str,
    verdict: str | None,
    reason_code: str | None,
    page_word: str,
) -> None:
    bench = _bench(tmp_path)

    def verifier_succeeds(run):
        return {"evidence_refs": []}

    def execute():
        with bench.run(app_state="base-only") as run:
            if outcome == "pass":
                run.verify(verifier_succeeds)
            if outcome == "fail":
                assert False, "forced functional red"
            if outcome == "skip":
                run.skip("absent_prerequisite", "named prerequisite is absent")
            if outcome == "error":
                raise RuntimeError("forced machinery red")
        return run

    if outcome == "error":
        with pytest.raises(RuntimeError, match="forced machinery red"):
            execute()
        run = next(bench.store.root.glob("run_*"))
    else:
        run = execute().final_path

    result = json.loads((run / "result.json").read_text())
    page = render_evidence_page(run).read_text(encoding="utf-8")
    assert result["execution_status"] == execution_status
    assert result["verdict"] == verdict
    assert (result["reason"] or {}).get("code") == reason_code
    assert page_word in page


def test_machinery_error_names_incomplete_adjudication_evidence(tmp_path: Path) -> None:
    bench = _bench(tmp_path)

    with pytest.raises(RuntimeError, match="forced machinery red"):
        with bench.run(app_state="base-only"):
            raise RuntimeError("forced machinery red")

    run = next(bench.store.root.glob("run_*"))
    result = json.loads((run / "result.json").read_text())
    assert result["evidence"] == {
        "complete": False,
        "requirements": [
            {
                "name": "bench.adjudication",
                "complete": False,
                "evidence_refs": [],
            }
        ],
    }
