from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opentraces.core.arena.engine import Bench
from opentraces.core.arena.run_store import RunStore
from tests.core.arena.test_browser_drive import PublicBrowserSession, _scenario
from tests.core.arena.test_engine import RecordingBoxRuntime


def test_execution_timeline_survives_conflicting_directory_and_mtime_order(
    tmp_path: Path,
) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=RecordingBoxRuntime(),
        repository_path=tmp_path,
        browser_factory=PublicBrowserSession,
    )

    def cross_surface_journey(run):
        before = run.terminal.exec("printf", "before")
        browser = run.browser.inspect("main")
        after = run.terminal.exec("printf", "after")
        assert before.returncode == 0
        assert browser.state["text"] == "Pending"
        assert after.returncode == 0
        return {"evidence_refs": [before.result_ref, browser.result_ref, after.result_ref]}

    with bench.run(app_state="install-only") as run:
        run.verify(cross_surface_journey)
        assert run.draft is not None
        actions = run.draft.path / "actions"
        # A post-hoc reader now sees two contradictory plausible orders:
        # directory names retain execution order while mtimes say the reverse.
        for ordinal, seconds in ((1, 300), (2, 200), (3, 100)):
            os.utime(actions / f"{ordinal:04d}", (seconds, seconds))
        lexical_order = [path.name for path in sorted(actions.iterdir())]
        mtime_order = [
            path.name
            for path in sorted(actions.iterdir(), key=lambda path: path.stat().st_mtime)
        ]
        assert lexical_order == ["0001", "0002", "0003"]
        assert mtime_order == ["0003", "0002", "0001"]

    assert run.result["recordings"]["timeline_ref"] == "recordings/timeline.jsonl"
    timeline_path = run.final_path / "recordings/timeline.jsonl"
    rows = [json.loads(line) for line in timeline_path.read_text(encoding="utf-8").splitlines()]
    assert [row["sequence"] for row in rows] == list(range(1, len(rows) + 1))
    assert [row["offset_ms"] for row in rows] == sorted(row["offset_ms"] for row in rows)

    starts = [row for row in rows if row["event"] == "action_started"]
    assert [row["surface"] for row in starts] == ["terminal", "browser", "terminal"]
    assert [row["action_ref"] for row in starts] == [
        "actions/0001",
        "actions/0002",
        "actions/0003",
    ]
    assert starts[0]["causal_refs"] == []
    assert starts[1]["causal_refs"] == ["actions/0001"]
    assert starts[2]["causal_refs"] == ["actions/0002"]
    for row in starts:
        invocation = json.loads(
            (run.final_path / row["action_ref"] / "invocation.json").read_text(encoding="utf-8")
        )
        assert row["recorded_at"] == invocation["started_at"]

    focus = [row for row in rows if row["event"] == "focus_changed"]
    assert [row["surface"] for row in focus] == ["terminal", "browser", "terminal"]
    assert [row["action_ref"] for row in focus] == [
        "actions/0001",
        "actions/0002",
        "actions/0003",
    ]
    manifest = json.loads(
        (run.final_path / ".integrity.json").read_text(encoding="utf-8")
    )
    assert "recordings/timeline.jsonl" in manifest["files"]
    assert bench.store.verify(run.final_path) is True
    assert run.draft is not None
    with pytest.raises(RuntimeError, match="finalized"):
        run.draft.append_jsonl("recordings/timeline.jsonl", {"sequence": 999})
    with pytest.raises(PermissionError):
        timeline_path.write_text(timeline_path.read_text() + "{}\n", encoding="utf-8")
    assert bench.store.verify(run.final_path) is True


@pytest.mark.parametrize("damage", ["missing", "dangling-causal-ref"])
def test_timeline_damage_only_removes_rewatchability(
    tmp_path: Path,
    damage: str,
) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=RecordingBoxRuntime(),
        repository_path=tmp_path,
        browser_factory=PublicBrowserSession,
    )

    def journey(run):
        terminal = run.terminal.exec("printf", "before")
        browser = run.browser.inspect("main")
        return {"evidence_refs": [terminal.result_ref, browser.result_ref]}

    with bench.run(app_state="install-only") as run:
        run.verify(journey)
        assert run.draft is not None
        timeline_path = run.draft.path / "recordings/timeline.jsonl"
        if damage == "missing":
            timeline_path.unlink()
        else:
            rows = [
                json.loads(line)
                for line in timeline_path.read_text(encoding="utf-8").splitlines()
            ]
            browser_start = next(
                row
                for row in rows
                if row["event"] == "action_started" and row["surface"] == "browser"
            )
            browser_start["causal_refs"] = ["actions/9999"]
            timeline_path.write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )

    recordings = run.result["recordings"]
    assert run.result["verdict"] == "pass"
    assert run.result["evidence"]["complete"] is True
    assert recordings["rewatchable"] is False
    assert recordings["timeline"]["complete"] is False
    assert damage.split("-", 1)[0] in recordings["timeline"]["reason"]
    assert all(channel["complete"] for channel in recordings["channels"])
