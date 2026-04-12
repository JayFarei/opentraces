"""Plan 041 R30 blame tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import mmh3
import pytest

from opentraces.enrichment.git import blame, notes_store
from opentraces_schema import (
    Agent, Attribution, AttributionConversation, AttributionFile,
    AttributionRange, Step, TokenUsage, TraceRecord,
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
    (tmp_path / "a.py").write_text("line1\nLINE_TWO\nline3\n")
    _sh(["git", "add", "-A"], tmp_path)
    _sh(["git", "commit", "-qm", "c"], tmp_path)
    return tmp_path


def _trace(trace_id: str, file_path: str, start: int, end: int, content_hash: str):
    return TraceRecord(
        trace_id=trace_id, session_id="s",
        agent=Agent(name="claude-code"),
        steps=[Step(step_index=7, role="agent", tool_calls=[],
                    observations=[], token_usage=TokenUsage())],
        attribution=Attribution(
            experimental=False,
            files=[AttributionFile(
                path=file_path,
                conversations=[AttributionConversation(
                    contributor={"type": "ai"},
                    url=f"opentraces://{trace_id}/step_7",
                    ranges=[AttributionRange(
                        start_line=start, end_line=end,
                        content_hash=content_hash, confidence="high",
                    )],
                )],
            )],
        ),
    )


class TestBlame:
    def test_blame_resolves_trace_and_step(self, repo):
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), text=True,
        ).strip()
        trace = _trace("t-alpha", "a.py", 2, 2, _hash("LINE_TWO"))
        notes_store.append(sha, ["opentraces:t-alpha"], repo)

        hits = blame.blame("a.py", 2, {"t-alpha": trace}, repo)
        assert len(hits) == 1
        h = hits[0]
        assert h.trace_id == "t-alpha"
        assert h.step == 7
        assert h.revision == sha
        assert h.content_alive is True

    def test_blame_no_note_returns_empty(self, repo):
        hits = blame.blame("a.py", 2, {}, repo)
        assert hits == []

    def test_blame_dead_content_marked_false(self, repo):
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), text=True,
        ).strip()
        # Attribution hash does not match current file content.
        trace = _trace("t-dead", "a.py", 2, 2, _hash("something else"))
        notes_store.append(sha, ["opentraces:t-dead"], repo)

        hits = blame.blame("a.py", 2, {"t-dead": trace}, repo)
        assert len(hits) == 1
        assert hits[0].content_alive is False
