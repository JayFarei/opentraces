"""Tests for the git notes wrapper (plan 041 phase 3 R24)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opentraces.git import notes_store


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.check_call(
        cmd, cwd=str(cwd),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def repo(tmp_path):
    _run(["git", "init", "-q", "-b", "main"], tmp_path)
    _run(["git", "config", "user.email", "t@t.t"], tmp_path)
    _run(["git", "config", "user.name", "t"], tmp_path)
    (tmp_path / "f.py").write_text("hi\n")
    _run(["git", "add", "-A"], tmp_path)
    _run(["git", "commit", "-qm", "init"], tmp_path)
    return tmp_path


def _head(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), text=True,
    ).strip()


class TestNotesStore:
    def test_empty_note_returns_empty_list(self, repo):
        assert notes_store.read(_head(repo), repo) == []

    def test_append_single_line(self, repo):
        sha = _head(repo)
        written = notes_store.append(sha, ["opentraces:abc123"], repo)
        assert written == 1
        assert notes_store.read(sha, repo) == ["opentraces:abc123"]

    def test_append_is_deduped(self, repo):
        sha = _head(repo)
        notes_store.append(sha, ["opentraces:abc"], repo)
        written = notes_store.append(sha, ["opentraces:abc"], repo)
        assert written == 0
        assert notes_store.read(sha, repo) == ["opentraces:abc"]

    def test_append_multiple_preserves_order(self, repo):
        sha = _head(repo)
        notes_store.append(sha, ["opentraces:first", "opentraces:second"], repo)
        lines = notes_store.read(sha, repo)
        assert lines[0] == "opentraces:first"
        assert "opentraces:second" in lines


class TestFormatAndParse:
    def test_format_without_url(self):
        assert notes_store.format_link("abc") == "opentraces:abc"

    def test_format_with_url(self):
        got = notes_store.format_link("abc", "https://hf/x")
        assert got == "opentraces:abc https://hf/x"

    def test_parse_roundtrip(self):
        for (tid, url) in [("abc", None), ("abc", "https://hf/x")]:
            line = notes_store.format_link(tid, url)
            parsed = notes_store.parse_link(line)
            assert parsed == (tid, url)

    def test_parse_rejects_non_opentraces_line(self):
        assert notes_store.parse_link("something else") is None
        assert notes_store.parse_link("") is None
