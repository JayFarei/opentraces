"""Opt-in real Claude/tmux Trace Trails provenance scenarios.

These are not default regression tests. They drive a real Claude Code REPL,
install the dev opentraces hooks, create real Git commits, and then assert the
client-facing ``otd trail ... --json`` contract. Run them only when doing local
UAT for Trace Trails.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


SCENARIOS_DIR = Path(__file__).parent / "trail_scenarios"
HARNESS_PATH = Path(__file__).parent / "harness" / "attribution_v2_harness.py"


def _scenario_files() -> list[Path]:
    if not SCENARIOS_DIR.is_dir():
        return []
    return sorted(SCENARIOS_DIR.glob("*.toml"))


def _scenario_has_claude_step(path: Path) -> bool:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return any(s.get("type") == "claude" for s in data.get("steps", []))


def _build_params():
    params = []
    for path in _scenario_files():
        markers = [pytest.mark.trail_real_repl]
        if _scenario_has_claude_step(path):
            markers.append(pytest.mark.real_repl)
        params.append(pytest.param(path, id=path.stem, marks=markers))
    return params


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """Real Claude/tmux scenarios must use the user's real HOME.

    The root test fixture redirects HOME to isolate normal tests. That breaks
    Claude Code discovery because the tmux shell no longer sees the user's
    zsh aliases, Claude settings, or real session corpus.
    """
    yield


@pytest.mark.parametrize("scenario_path", _build_params())
def test_trail_real_repl_scenario(scenario_path: Path) -> None:
    if os.environ.get("OT_TRAIL_REAL_REPL") != "1":
        pytest.skip("trail real_repl scenario; set OT_TRAIL_REAL_REPL=1 to run")
    if not shutil.which("tmux"):
        pytest.skip("tmux not on PATH")

    result = subprocess.run(
        [sys.executable, str(HARNESS_PATH), str(scenario_path)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(
            f"{scenario_path.stem} failed with {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
