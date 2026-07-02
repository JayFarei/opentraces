#!/usr/bin/env python3
"""PROBE C2 — worktree snapshot dedup (epic #169, hygiene gate).

Committed, grader-owned, location-relative version of forge `c2_worktree_dedup.py`.
Drives the SHIPPED `_event_cache_path` (`core/trails/event_log.py`) from N>=2
genuinely-linked worktrees of a hermetic repo and asserts they all resolve to
ONE shared snapshot under the COMMON git dir (the fix), not per-worktree copies
(the 22-copy `--git-dir` bug, RED on main).

Tamper guard: the expected path is computed INDEPENDENTLY via a separate
`git rev-parse --git-common-dir` subprocess, never via the function under test.
exit 0 GREEN / 1 RED / 2 SETUP-INVALID.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


def _g(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _run() -> int:
    from opentraces.core.trails.event_log import _event_cache_path

    tmp = Path(tempfile.mkdtemp(prefix="c2-dedup-")).resolve()
    repo = tmp / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "a@b.c"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f.txt").write_text("seed\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)
    wt1, wt2 = tmp / "wt1", tmp / "wt2"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(wt1)], check=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(wt2)], check=True)
    worktrees = {"main": repo, "wt1": wt1, "wt2": wt2}

    # SETUP VALIDITY: worktrees must be genuinely distinct linked worktrees.
    per_wt_gitdir = {
        name: Path(_g(d, "rev-parse", "--absolute-git-dir")).resolve()
        for name, d in worktrees.items()
    }
    if len({str(p) for p in per_wt_gitdir.values()}) != 3:
        print(f"[SETUP-INVALID] worktrees not distinct: {per_wt_gitdir}")
        return 2
    if not all("/worktrees/" in str(per_wt_gitdir[w]) for w in ("wt1", "wt2")):
        print(f"[SETUP-INVALID] linked worktrees lack a per-worktree git dir: {per_wt_gitdir}")
        return 2

    common = Path(_g(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    expected = (common / "opentraces" / "event_log_snapshot.pkl").resolve()

    resolved = {}
    for name, d in worktrees.items():
        p = _event_cache_path(d)
        resolved[name] = p.resolve() if p is not None else None

    failures = []
    for name, p in resolved.items():
        if p is None:
            failures.append(f"{name}: resolver returned None")
        elif p != expected:
            failures.append(f"{name}: {p} != shared {expected}")
        elif "/worktrees/" in str(p):
            failures.append(f"{name}: snapshot inside per-worktree git dir {p}")
    distinct = {str(p) for p in resolved.values() if p is not None}
    if len(distinct) != 1:
        failures.append(f"worktrees do NOT dedup: {sorted(distinct)}")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("RED: per-worktree snapshot duplication (the --git-dir bug)")
        for f in failures:
            print("  -", f)
        return 1
    print(f"GREEN: all worktrees share ONE snapshot under the common git dir ({expected})")
    return 0


def test_probe_c2_worktree_dedup():
    rc = _run()
    if rc == 2:
        pytest.skip("C2 SETUP-INVALID (hermetic worktree scenario failed to build)")
    assert rc == 0, "C2 RED: per-worktree snapshot duplication (--git-dir not --git-common-dir)"


if __name__ == "__main__":
    sys.exit(_run())
