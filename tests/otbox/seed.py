"""Seed harness — the reusable seeded-world builder (spec M1 / R1).

A *seed* scenario materializes a fully-populated, deterministic
opentraces world inside a box: an initialized project on a real git
repo, traces replayed from committed fixtures, a built Trace Index, and
a fake HF remote root. Seeds are imperative world-builders (Python);
*journeys* are the declarative TOML layer on top (see journey.py).

Reuses the building blocks in ``tests/e2e/_smoke_helpers.py`` —
``trace_record`` is the shared canonical TraceRecord factory — rather
than hand-rolling trace payloads here.
"""

from __future__ import annotations

import json
import sys
from typing import Callable

from .drivers.base import Driver, ExecResult
from .env import REPO_ROOT, Box, resolve_cli_argv

# Reuse, don't duplicate: the canonical trace factory lives with the
# existing e2e smoke helpers.
sys.path.insert(0, str(REPO_ROOT / "tests"))
from e2e._smoke_helpers import trace_record  # noqa: E402


class SeedError(Exception):
    pass


# --------------------------------------------------------------------------
# low-level helpers
# --------------------------------------------------------------------------
def _git(driver: Driver, box: Box, *args: str, check: bool = True) -> ExecResult:
    result = driver.exec(box, ["git", *args])
    if check and not result.ok:
        raise SeedError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def _cli(driver: Driver, box: Box, *args: str, check: bool = True) -> ExecResult:
    result = driver.exec(box, [*resolve_cli_argv(), *args])
    if check and not result.ok:
        raise SeedError(
            f"opentraces {' '.join(args)} failed ({result.returncode}):\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return result


def _find_state_dir(driver: Driver, box: Box) -> str:
    """Locate the single per-project state dir created by ``init``.

    Driver-mediated so the seed works on Tier 0 (laptop fs) and Tier 1
    (remote fs) without branching.
    """
    paths = driver.paths(box)
    candidates = driver.glob(box, f"{paths['opentraces_dir']}/projects/*")
    if len(candidates) != 1:
        raise SeedError(
            f"expected exactly one opted-in project under "
            f"{paths['opentraces_dir']}/projects, found {len(candidates)}"
        )
    return candidates[0]


def _init_git_project(driver: Driver, box: Box) -> str:
    """Make the box project a real git repo with one real commit."""
    project = driver.paths(box)["project"]
    driver.mkdir(box, project)
    driver.put_text(
        box, f"{project}/README.md",
        "# otbox seeded project\n\nDeterministic fixture project for otbox journeys.\n",
    )
    driver.mkdir(box, f"{project}/src")
    driver.put_text(
        box, f"{project}/src/app.py",
        'def greet(name: str) -> str:\n    return f"hello, {name}"\n',
    )
    _git(driver, box, "init", "-q")
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "seed: initial project")
    return _git(driver, box, "rev-parse", "HEAD").stdout.strip()


def _place_traces(driver: Driver, box: Box, count: int) -> list[str]:
    """Drop replayed traces into the project's canonical traces/ layer."""
    state_dir = _find_state_dir(driver, box)
    traces_dir = f"{state_dir}/traces"
    driver.mkdir(box, traces_dir)
    trace_ids: list[str] = []
    for i in range(count):
        trace_id = f"otbox{i:012d}"
        session_id = f"otbox-seed-session-{i:03d}"
        record = trace_record(
            trace_id=trace_id,
            session_id=session_id,
            task_description=f"otbox seeded journey trace {i}: refactor greet() helper",
        )
        driver.put_text(box, f"{traces_dir}/{trace_id}.jsonl", json.dumps(record) + "\n")
        trace_ids.append(trace_id)
    return trace_ids


# --------------------------------------------------------------------------
# seed scenarios
# --------------------------------------------------------------------------
def _seed_smoke(driver: Driver, box: Box) -> dict:
    """Minimal but *real* world: project + git + replayed traces + index.

    This is the seed the thin vertical slice runs against.
    """
    report: dict = {"scenario": "smoke", "steps": []}

    head = _init_git_project(driver, box)
    report["steps"].append({"step": "git_project", "head": head})

    _cli(driver, box, "init", "--start-fresh", "--agent", "claude-code")
    state_dir = _find_state_dir(driver, box)
    report["steps"].append({"step": "init", "state_dir": state_dir})

    trace_ids = _place_traces(driver, box, count=3)
    report["steps"].append({"step": "place_traces", "trace_ids": trace_ids})

    index = _cli(driver, box, "trace", "index", "rebuild")
    report["steps"].append(
        {"step": "trace_index_rebuild", "returncode": index.returncode}
    )

    report["state_dir"] = state_dir
    report["trace_ids"] = trace_ids
    report["head_commit"] = head
    return report


def _seed_world(driver: Driver, box: Box) -> dict:
    """Fuller world: smoke + git post-commit hook + a second commit.

    Used by the broader catalogue journeys (M3). Extends, never forks,
    the smoke seed.
    """
    report = _seed_smoke(driver, box)
    report["scenario"] = "world"

    hook = _cli(driver, box, "setup", "git", check=False)
    report["steps"].append(
        {"step": "setup_git_hook", "returncode": hook.returncode}
    )

    # A second commit so commit-correlation journeys have history to walk.
    project = driver.paths(box)["project"]
    driver.put_text(
        box, f"{project}/src/app.py",
        'def greet(name: str) -> str:\n'
        '    return f"hello, {name}!"\n\n'
        'def farewell(name: str) -> str:\n'
        '    return f"bye, {name}"\n',
    )
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "seed: add farewell() helper")
    report["second_commit"] = _git(driver, box, "rev-parse", "HEAD").stdout.strip()
    return report


SEEDS: dict[str, Callable[[Driver, Box], dict]] = {
    "smoke": _seed_smoke,
    "world": _seed_world,
}


def available_seeds() -> list[str]:
    return sorted(SEEDS)


def run_seed(driver: Driver, box: Box, scenario: str) -> dict:
    """Materialize a seed scenario inside an already-provisioned box."""
    try:
        builder = SEEDS[scenario]
    except KeyError:
        raise SeedError(
            f"unknown seed scenario {scenario!r}; available: {available_seeds()}"
        ) from None
    report = builder(driver, box)
    box.seed = scenario
    box.status = "seeded"
    box.notes["seed_report"] = report
    box.save()
    return report
