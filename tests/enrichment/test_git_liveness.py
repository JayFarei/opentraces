"""Plan 041 R32/R33 liveness tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import mmh3
import pytest

from opentraces.enrichment.git import liveness
from opentraces_schema import (
    Attribution, AttributionConversation, AttributionFile, AttributionRange,
)


def _sh(cmd, cwd):
    subprocess.check_call(
        cmd, cwd=str(cwd),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _hash(text: str) -> str:
    return f"murmur3:{mmh3.hash128(text.encode('utf-8'), signed=False):032x}"


@pytest.fixture
def repo(tmp_path):
    _sh(["git", "init", "-q", "-b", "main"], tmp_path)
    _sh(["git", "config", "user.email", "t@t"], tmp_path)
    _sh(["git", "config", "user.name", "t"], tmp_path)
    return tmp_path


class TestCommitReachable:
    def test_head_is_reachable(self, repo):
        (repo / "f.py").write_text("a\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c1"], repo)
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), text=True,
        ).strip()
        assert liveness.commit_reachable(head, repo) is True

    def test_nonexistent_sha_is_unreachable(self, repo):
        (repo / "f.py").write_text("a\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c1"], repo)
        assert liveness.commit_reachable("0" * 40, repo) is False

    def test_rewound_branch_unreachable(self, repo):
        (repo / "f.py").write_text("a\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c1"], repo)
        (repo / "f.py").write_text("b\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c2"], repo)
        c2 = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), text=True,
        ).strip()
        # Move HEAD back past c2.
        _sh(["git", "reset", "--hard", "HEAD~1"], repo)
        assert liveness.commit_reachable(c2, repo) is False


class TestContentAlive:
    def test_surviving_range_is_alive(self, repo):
        content = "line1\nline2\nLINE_ALIVE\nline4\n"
        (repo / "a.py").write_text(content)
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c"], repo)

        attr = Attribution(
            experimental=False,
            files=[AttributionFile(
                path="a.py",
                conversations=[AttributionConversation(
                    contributor={"type": "ai"},
                    ranges=[AttributionRange(
                        start_line=3, end_line=3,
                        content_hash=_hash("LINE_ALIVE"),
                        confidence="high",
                    )],
                )],
            )],
        )
        assert liveness.content_alive(attr, "HEAD", repo) is True

    def test_overwritten_range_is_dead(self, repo):
        (repo / "a.py").write_text("line1\nLINE_ALIVE\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c"], repo)
        (repo / "a.py").write_text("line1\nLINE_CHANGED\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c2"], repo)

        attr = Attribution(
            experimental=False,
            files=[AttributionFile(
                path="a.py",
                conversations=[AttributionConversation(
                    contributor={"type": "ai"},
                    ranges=[AttributionRange(
                        start_line=2, end_line=2,
                        content_hash=_hash("LINE_ALIVE"),
                        confidence="high",
                    )],
                )],
            )],
        )
        assert liveness.content_alive(attr, "HEAD", repo) is False
