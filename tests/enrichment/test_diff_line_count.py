"""Unit tests for ``enrichment.git.blame.diff_line_count`` (plan 047)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opentraces.enrichment.git.blame import diff_line_count


def _init_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=tmp_path, check=True)


def _commit(tmp_path: Path, msg: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=tmp_path, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True,
    ).strip()


def test_add_file_counts_inserted_lines(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    sha = _commit(tmp_path, "add a")
    assert diff_line_count(tmp_path, sha) == 3


def test_modify_counts_only_insertions(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    _commit(tmp_path, "initial")
    # Modify middle line only (1 delete + 1 insert in numstat).
    (tmp_path / "a.txt").write_text("one\nTWO\nthree\n")
    sha = _commit(tmp_path, "tweak")
    # Insertions-only denominator: counts the new line, not the deleted
    # one. Dividing by adds+deletes would halve coverage unfairly.
    assert diff_line_count(tmp_path, sha) == 1


def test_binary_file_excluded(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02\xff" * 32)
    sha = _commit(tmp_path, "add binary")
    # Binary files render as ``-\t-\t`` in numstat → excluded.
    assert diff_line_count(tmp_path, sha) == 0


def test_unknown_sha_returns_zero(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("x\n")
    _commit(tmp_path, "init")
    assert diff_line_count(tmp_path, "deadbeefdeadbeef") == 0


def test_multi_file_sums_across_files(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    (tmp_path / "b.txt").write_text("alpha\nbeta\ngamma\n")
    sha = _commit(tmp_path, "add both")
    assert diff_line_count(tmp_path, sha) == 5
