"""Opt-in real-agent SkillOpt scenario suite and gold-run helper."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from opentraces.consumers.skill_opt import engine as so

from tests.otbox.drivers import get_driver
from tests.otbox.skillopt_harness import (
    OtboxHarness,
    OtboxReRolloutRunner,
    OtboxSkillOptTask,
)

SENTINEL = "OPENTRACES_OK"
MARKER = "rl.status_sentinel"
REPO_ROOT = Path(__file__).resolve().parents[2]


def held_out_status_suite() -> tuple[
    list[OtboxSkillOptTask],
    list[OtboxSkillOptTask],
    list[OtboxSkillOptTask],
]:
    """Small Dtrain/Dsel/Dtest suite with file-content verifiers."""
    return (
        [
            _status_task(
                "train-status",
                "src/app.py",
                "status",
                "Update src/app.py so status() returns the project-specific status sentinel. Read CLAUDE.md for the exact sentinel.",
            )
        ],
        [
            _status_task(
                "sel-health",
                "src/health.py",
                "health",
                "Update src/health.py so health() returns the project-specific status sentinel. Read CLAUDE.md for the exact sentinel.",
            )
        ],
        [
            _status_task(
                "test-ready",
                "src/ready.py",
                "ready",
                "Update src/ready.py so ready() returns the project-specific status sentinel. Read CLAUDE.md for the exact sentinel.",
            )
        ],
    )


def status_sentinel_proposer(
    current_skill: str,
    _rows: list[so.RolloutRow],
    _budget: int,
    _rejected: list[dict],
    *,
    meta_skill: str = "",
) -> list[dict]:
    del meta_skill
    if so.RULE_MARKER.format(tag=MARKER) in current_skill:
        return []
    return [
        {
            "op": "append",
            "content": (
                f"- {so.RULE_MARKER.format(tag=MARKER)}: When a task asks for the "
                f"project-specific status sentinel, edit the requested function to "
                f"return the exact string {SENTINEL!r}."
            ),
            "support_count": 1,
            "source_type": "failure",
        }
    ]


def run_real_gold(binary: str, out_dir: Path, *, timeout_s: float = 180.0) -> dict:
    """Run one budget-capped real otbox optimization and write gold evidence."""
    if not shutil.which(binary):
        raise RuntimeError(f"{binary!r} not found on PATH")
    out_dir.mkdir(parents=True, exist_ok=True)
    train, selection, test = held_out_status_suite()
    runner = OtboxReRolloutRunner(
        get_driver("local"),
        binary=binary,
        agent="claude" if Path(binary).name == "claude" else "codex",
        output_root=out_dir / "rerollouts",
        turn_timeout_s=timeout_s,
    )
    harness = OtboxHarness(
        runner,
        train_tasks=train,
        selection_tasks=selection,
        test_tasks=test,
    )
    result = so.run_optimization(
        "# Real-agent SkillOpt starter\n",
        [],
        propose=status_sentinel_proposer,
        harness=harness,
        train_tasks=train,
        selection_tasks=selection,
        test_tasks=test,
        budget=1,
        budget_floor=1,
        schedule="constant",
        max_steps=1,
        epochs=1,
        slow_update=None,
        meta_update=None,
    )
    artifacts = result.state.export(out_dir)
    summary = {
        "schema_version": "opentraces.skill_opt.real_gold.v1",
        "binary": binary,
        "initial_selection_score": result.initial_score,
        "best_selection_score": result.best_score,
        "held_out_test_score": result.test_score,
        "accepted": result.accepted,
        "rejected": result.rejected,
        "best_skill": _repo_relative(artifacts["best_skill"]),
        "report": _repo_relative(artifacts["report"]),
        "tasks": {
            "train": [task.task_id for task in train],
            "selection": [task.task_id for task in selection],
            "test": [task.task_id for task in test],
        },
        "rerollout_logs": sorted(
            _repo_relative(p) for p in (out_dir / "rerollouts").glob("*/pane.log")
        ),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _status_task(task_id: str, rel_path: str, function_name: str, prompt: str) -> OtboxSkillOptTask:
    return OtboxSkillOptTask(
        task_id=task_id,
        prompt=prompt + " Do not ask questions; make the file change directly.",
        required_markers=(MARKER,),
        setup_files={
            rel_path: (
                f"def {function_name}():\n"
                "    return 'PENDING'\n"
            )
        },
        verify_path=rel_path,
        verify_contains=SENTINEL,
        source_trace_id=f"skillopt-real-{task_id}",
    )


def _repo_relative(path: str | Path) -> str:
    p = Path(path)
    try:
        return p.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(p)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", default="claude")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    summary = run_real_gold(args.binary, args.out, timeout_s=args.timeout)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
