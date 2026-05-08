from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest


_LANE_ORDER = {"smoke": 0, "full": 1, "nightly": 2}


@dataclass(frozen=True)
class PerfRuntime:
    lane: str
    artifacts_dir: Path
    update_baselines: bool

    def includes(self, lane: str) -> bool:
        return _LANE_ORDER[lane] <= _LANE_ORDER[self.lane]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("opentraces-perf")
    group.addoption(
        "--perf-lane",
        action="store",
        default=os.environ.get("OT_PERF_LANE", "smoke"),
        choices=tuple(_LANE_ORDER.keys()),
        help="Performance lane to run: smoke, full, or nightly.",
    )
    group.addoption(
        "--perf-artifacts-dir",
        action="store",
        default=os.environ.get("OT_PERF_ARTIFACTS_DIR", "tests/perf/artifacts/latest"),
        help="Directory where performance JSON artifacts are written.",
    )
    group.addoption(
        "--update-perf-baselines",
        action="store_true",
        default=os.environ.get("OT_UPDATE_PERF_BASELINES") == "1",
        help="Update tests/perf/budgets.toml with the current measurements.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "perf: deterministic performance harness")
    config.addinivalue_line(
        "markers",
        "user_smoke: opt-in user-journey smoke coverage (tmux / browser automation)",
    )


@pytest.fixture
def perf_runtime(pytestconfig: pytest.Config) -> PerfRuntime:
    configured_artifacts_dir = Path(pytestconfig.getoption("--perf-artifacts-dir"))
    artifacts_dir = (
        configured_artifacts_dir
        if configured_artifacts_dir.is_absolute()
        else Path(pytestconfig.rootpath) / configured_artifacts_dir
    ).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return PerfRuntime(
        lane=pytestconfig.getoption("--perf-lane"),
        artifacts_dir=artifacts_dir,
        update_baselines=bool(pytestconfig.getoption("--update-perf-baselines")),
    )


@pytest.fixture
def perf_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home
