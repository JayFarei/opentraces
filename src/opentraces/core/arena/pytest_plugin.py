"""Thin pytest adapter for the durable :mod:`opentraces.core.arena` engine."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from .engine import Bench, ScenarioSource, extract_claim
from .run_store import RunStore


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Emit the minimal phase facts the parent runner needs for honest cleanup."""

    path_value = os.environ.get("OT_BENCH_PYTEST_PHASE_REPORT")
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "nodeid": str(report.nodeid),
        "when": str(report.when),
        "outcome": str(report.outcome),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _git(repository: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def _public_scenario_path(source_path: Path, repository: Path) -> str:
    """Return a repository coordinate or a content-derived private fallback."""

    try:
        return source_path.relative_to(repository).as_posix()
    except ValueError:
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        return f"external/{digest[:12]}{source_path.suffix}"


def _scenario_source(request: pytest.FixtureRequest, repository: Path) -> ScenarioSource:
    function = request.node.function
    source_path = Path(str(request.node.path)).resolve()
    scenario_path = _public_scenario_path(source_path, repository)
    remote = _git(repository, "remote", "get-url", "origin")
    repository_identity = remote or repository.name
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--", scenario_path],
        cwd=repository,
        capture_output=True,
        check=False,
    ).stdout
    dirty_digest = f"sha256:{hashlib.sha256(diff).hexdigest()}" if diff else None
    return ScenarioSource(
        nodeid=request.node.nodeid,
        claim=extract_claim(function),
        source_path=source_path,
        scenario_path=scenario_path,
        repository=repository_identity,
        commit=_git(repository, "rev-parse", "HEAD"),
        dirty_diff_digest=dirty_digest,
    )


@pytest.fixture
def bench(request: pytest.FixtureRequest) -> Bench:
    """Bind one discovered pytest scenario to the deep bench engine."""

    repository = Path(os.environ.get("OT_BENCH_REPOSITORY", Path.cwd())).resolve()
    root_value = os.environ.get("OT_BENCH_RUN_ROOT")
    store = RunStore(Path(root_value)) if root_value else RunStore()
    return Bench(
        source=_scenario_source(request, repository),
        store=store,
        repository_path=repository,
    )
