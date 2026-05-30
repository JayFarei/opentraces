"""The capsule-as-a-test proof (article: "open data becomes the replayable test").

Builds a real golden fixture: a tiny git repo with a bug + a failing test, then a
fix commit. A capsule carrying the repro command must:
  - reproduce the failure at the buggy commit (troubleshoot), and
  - report fixed at the fix commit (re-test).

Fully hermetic: a temp git repo, run via the stdlib unittest module (no pytest
dependency inside the fixture).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from opentraces.core.capsule.run import run_capsule_test
from opentraces.core.capsule.test_extract import declared_test


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture(scope="module")
def fixture_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("capsule_fixture_repo")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    # Buggy: add() subtracts. The test asserts add(2, 3) == 5 -> fails.
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "buggy: add subtracts")
    buggy = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()

    # Fix.
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix: add adds")
    fixed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return {"repo": repo, "buggy": buggy, "fixed": fixed}


def _capsule_with_test():
    # A minimal capsule carrying a declared repro command.
    return {
        "schema_version": "opentraces.capsule.v1",
        "capsule_id": "fixturecap000001",
        "repo_pin": {"commit_sha": "buggy"},
        "test": declared_test(
            f'{sys.executable} -c "from calc import add; assert add(2,3)==5"',
            "AssertionError",
        ),
    }


def test_capsule_reproduces_at_buggy_commit(fixture_repo):
    cap = _capsule_with_test()
    res = run_capsule_test(cap, repo_dir=fixture_repo["repo"], target_ref=fixture_repo["buggy"])
    assert res["runnable"] is True
    assert res["verdict"] == "reproduces", res
    assert res["signal_present"] is True


def test_capsule_is_fixed_at_fix_commit(fixture_repo):
    cap = _capsule_with_test()
    res = run_capsule_test(cap, repo_dir=fixture_repo["repo"], target_ref=fixture_repo["fixed"])
    assert res["verdict"] == "fixed", res
    assert res["exit_code"] == 0
    assert res["signal_present"] is False


def test_no_executable_test_is_inconclusive(fixture_repo):
    res = run_capsule_test({"test": None}, repo_dir=fixture_repo["repo"], target_ref=fixture_repo["fixed"])
    assert res["runnable"] is False
    assert res["verdict"] == "inconclusive"


def test_worktree_is_cleaned_up(fixture_repo):
    # After a run, `git worktree list` should show only the main worktree.
    cap = _capsule_with_test()
    run_capsule_test(cap, repo_dir=fixture_repo["repo"], target_ref=fixture_repo["fixed"])
    out = subprocess.run(
        ["git", "-C", str(fixture_repo["repo"]), "worktree", "list"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()
    assert len(out) == 1, f"leaked worktrees: {out}"


def test_declared_test_shapes():
    assert declared_test(None, None) is None
    t = declared_test("pytest x", "AssertionError")
    assert t["expected"] == {"kind": "error_string", "value": "AssertionError"}
    t2 = declared_test("make", None)
    assert t2["expected"] == {"kind": "nonzero_exit"}
