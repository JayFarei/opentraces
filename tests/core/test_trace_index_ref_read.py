"""Plan 087 U0 — direct event-log ref reads must match git semantics.

``_trail_event_ref_sha`` no longer spawns ``git show-ref`` on the common
path; it reads ``.git/refs/...`` and ``.git/packed-refs`` directly. These
cover the layouts present in the live 279-home bucket: a normal ``.git``
directory (loose ref), a packed ref, a worktree/submodule ``.git`` FILE
pointer, and a missing ref (a project that never captured a trail).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.trace_index import _resolve_git_dir, _trail_event_ref_sha
from opentraces.core.trails import EVENT_LOG_REF


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "f.py").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def _set_event_ref(repo: Path, sha: str) -> None:
    subprocess.run(
        ["git", "update-ref", EVENT_LOG_REF, sha],
        cwd=repo,
        check=True,
    )


def test_missing_ref_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    assert _trail_event_ref_sha(repo) is None


def test_loose_ref_matches_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    head = _init_repo(repo)
    _set_event_ref(repo, head)

    expected = subprocess.check_output(
        ["git", "show-ref", "--hash", EVENT_LOG_REF], cwd=repo, text=True
    ).strip()
    assert _trail_event_ref_sha(repo) == expected == head


def test_packed_ref_matches_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    head = _init_repo(repo)
    _set_event_ref(repo, head)
    # Pack refs so the loose file disappears.
    subprocess.run(["git", "pack-refs", "--all"], cwd=repo, check=True)

    loose = _resolve_git_dir(repo)
    for part in EVENT_LOG_REF.split("/"):
        loose = loose / part
    assert not loose.exists(), "expected loose ref to be packed away"

    expected = subprocess.check_output(
        ["git", "show-ref", "--hash", EVENT_LOG_REF], cwd=repo, text=True
    ).strip()
    assert _trail_event_ref_sha(repo) == expected == head


def test_worktree_git_file_pointer_matches_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    head = _init_repo(repo)
    _set_event_ref(repo, head)

    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(wt)],
        cwd=repo,
        check=True,
    )
    # The worktree's .git is a FILE pointer; the shared ref lives in the
    # common gitdir, so the read should still resolve it.
    assert (wt / ".git").is_file()
    assert _trail_event_ref_sha(wt) == head
