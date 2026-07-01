"""Dry-run-default ``bucket reclaim`` janitor for ``.git/**/opentraces/`` cruft."""
from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.bucket_reclaim import (
    CATEGORY_LEAKED_TMP,
    CATEGORY_ORPHAN_INDEX_PICKLE,
    CATEGORY_ORPHAN_SNAPSHOT,
    reclaim_repo,
    scan_repo,
)


def _seed_repo_with_cruft(root: Path) -> tuple[Path, Path, Path]:
    repo = root / "proj"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    subprocess.run(["git", "worktree", "add", "-q", str(root / "wt1")], cwd=repo, check=True)

    common = (repo / ".git").resolve()
    live_ot = common / "opentraces"
    (live_ot / "event_index").mkdir(parents=True, exist_ok=True)
    live_snapshot = live_ot / "event_log_snapshot.pkl"
    live_base = live_ot / "event_index" / "base.pkl"
    live_snapshot.write_bytes(b"LIVE" * 100)
    live_base.write_bytes(b"BASE" * 50)
    # Non-cruft accelerator state — must be left alone.
    (live_ot / "event_log_verified.json").write_text("{}")
    (live_ot / "survival_cache.json").write_text("{}")
    # Cruft.
    (live_ot / "event_log_snapshot.pkl.tmp").write_bytes(b"x" * 10)
    (live_ot / "tmpABCD.tmp").write_bytes(b"x" * 10)
    (live_ot / "event_index" / "base.pkl.tmp").write_bytes(b"x" * 10)
    wt_ot = common / "worktrees" / "wt1" / "opentraces"
    (wt_ot / "event_index").mkdir(parents=True, exist_ok=True)
    (wt_ot / "event_log_snapshot.pkl").write_bytes(b"orphan" * 10)
    (wt_ot / "event_index" / "base.pkl").write_bytes(b"orphan" * 10)
    return repo, live_snapshot, live_base


def _ot_files(common: Path) -> set[str]:
    out: set[str] = set()
    for ot in common.rglob("opentraces"):
        if ot.is_dir():
            for p in ot.rglob("*"):
                if p.is_file():
                    out.add(str(p.relative_to(common)))
    return out


def test_scan_categorizes_cruft_and_protects_live(tmp_path: Path) -> None:
    repo, live_snapshot, live_base = _seed_repo_with_cruft(tmp_path)
    report = scan_repo(repo)
    cats = {c.category for c in report.candidates}
    assert CATEGORY_LEAKED_TMP in cats
    assert CATEGORY_ORPHAN_SNAPSHOT in cats
    assert CATEGORY_ORPHAN_INDEX_PICKLE in cats
    paths = {Path(c.path).resolve() for c in report.candidates}
    # Live keepers + non-cruft state are NEVER candidates.
    assert live_snapshot.resolve() not in paths
    assert live_base.resolve() not in paths
    assert not any(p.name in {"event_log_verified.json", "survival_cache.json"} for p in paths)
    assert report.total_bytes == sum(c.size_bytes for c in report.candidates)


def test_dry_run_default_deletes_nothing(tmp_path: Path) -> None:
    repo, _, _ = _seed_repo_with_cruft(tmp_path)
    common = (repo / ".git").resolve()
    before = _ot_files(common)
    report = reclaim_repo(repo)  # apply defaults to False
    assert report.applied is False
    assert report.removed == []
    assert _ot_files(common) == before  # file set byte-for-byte unchanged


def test_apply_removes_cruft_keeps_live(tmp_path: Path) -> None:
    repo, live_snapshot, live_base = _seed_repo_with_cruft(tmp_path)
    common = (repo / ".git").resolve()
    report = reclaim_repo(repo, apply=True)
    assert report.applied is True
    assert report.removed and not report.errors
    # Every candidate is gone; live keepers + non-cruft state survive.
    remaining = _ot_files(common)
    assert "opentraces/event_log_snapshot.pkl" in remaining
    assert "opentraces/event_index/base.pkl" in remaining
    assert "opentraces/event_log_verified.json" in remaining
    assert "opentraces/survival_cache.json" in remaining
    assert "opentraces/tmpABCD.tmp" not in remaining
    assert "worktrees/wt1/opentraces/event_log_snapshot.pkl" not in remaining
    assert "worktrees/wt1/opentraces/event_index/base.pkl" not in remaining
    assert live_snapshot.exists() and live_base.exists()


def test_non_git_dir_reports_error_not_crash(tmp_path: Path) -> None:
    plain = tmp_path / "notgit"
    plain.mkdir()
    report = scan_repo(plain)
    assert report.candidates == []
    assert report.errors
