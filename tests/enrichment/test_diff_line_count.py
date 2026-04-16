"""Unit tests for ``enrichment.git.blame.diff_line_count`` (plan 047)
and its per-path sibling ``diff_line_set`` (release-fix: diff-scoped
per-trace numerator)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opentraces.enrichment.git.blame import diff_line_count, diff_line_set


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


# --------------------------------------------------------------------------- #
# diff_line_set — per-path, per-line detail for the diff-scoped per-trace
# numerator used by ``ot blame`` to compute honest percentages.
# --------------------------------------------------------------------------- #


def test_diff_line_set_add_file(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "seed.txt").write_text("seed\n")
    _commit(tmp_path, "seed")
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    sha = _commit(tmp_path, "add a")
    assert diff_line_set(tmp_path, sha) == {"a.txt": {1, 2, 3}}


def test_diff_line_set_modify_only_changed_lines(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    _commit(tmp_path, "initial")
    (tmp_path / "a.txt").write_text("one\nTWO\nthree\n")
    sha = _commit(tmp_path, "tweak")
    # Only the single modified line appears in the new-file line set.
    assert diff_line_set(tmp_path, sha) == {"a.txt": {2}}


def test_diff_line_set_deletion_only_returns_empty(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    _commit(tmp_path, "initial")
    (tmp_path / "a.txt").unlink()
    sha = _commit(tmp_path, "delete a")
    # No post-image line set; renderer falls back to "coverage unavailable".
    assert diff_line_set(tmp_path, sha) == {}


def test_diff_line_set_rename_only_returns_empty(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "old.txt").write_text("content\n")
    _commit(tmp_path, "seed")
    subprocess.run(["git", "mv", "old.txt", "new.txt"], cwd=tmp_path, check=True)
    sha = _commit(tmp_path, "rename")
    # git emits +++ b/new.txt but no @@ hunks when content is unchanged,
    # so the destination path carries an empty set and is dropped.
    assert diff_line_set(tmp_path, sha) == {}


def test_diff_line_set_multi_file(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "seed.txt").write_text("s\n")
    _commit(tmp_path, "seed")
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    (tmp_path / "b.txt").write_text("alpha\nbeta\ngamma\n")
    sha = _commit(tmp_path, "add both")
    assert diff_line_set(tmp_path, sha) == {
        "a.txt": {1, 2},
        "b.txt": {1, 2, 3},
    }


def test_diff_line_set_binary_excluded(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "seed.txt").write_text("s\n")
    _commit(tmp_path, "seed")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02\xff" * 32)
    sha = _commit(tmp_path, "add binary")
    # git emits "Binary files differ" with no @@ hunks → no line set.
    assert diff_line_set(tmp_path, sha) == {}


def test_diff_line_set_unknown_sha_returns_empty(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("x\n")
    _commit(tmp_path, "init")
    assert diff_line_set(tmp_path, "deadbeefdeadbeef") == {}


def test_diff_line_set_counts_match_diff_line_count(tmp_path):
    """Invariant: sum of set sizes equals ``diff_line_count`` for
    insertion-only commits. Guards against regressions where the two
    helpers disagree on what counts as a "diff line"."""
    _init_repo(tmp_path)
    (tmp_path / "seed.txt").write_text("s\n")
    _commit(tmp_path, "seed")
    (tmp_path / "a.txt").write_text("x\ny\nz\n")
    (tmp_path / "b.txt").write_text("p\nq\n")
    sha = _commit(tmp_path, "add two")
    lset = diff_line_set(tmp_path, sha)
    total_from_set = sum(len(v) for v in lset.values())
    assert total_from_set == diff_line_count(tmp_path, sha) == 5
