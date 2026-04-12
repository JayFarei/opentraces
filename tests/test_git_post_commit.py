"""End-to-end tests for the post-commit orchestration (plan 041 phase 3)."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from opentraces.enrichment.git import notes_store, post_commit
from opentraces_schema import Agent, Step, TokenUsage, ToolCall, TraceRecord


def _sh(cmd: list[str], cwd: Path) -> None:
    subprocess.check_call(
        cmd, cwd=str(cwd),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _trace(
    trace_id: str, edits: list[tuple[str, str, str]], when: datetime,
) -> TraceRecord:
    steps = []
    for i, (fp, old, new) in enumerate(edits):
        steps.append(Step(
            step_index=i, role="agent",
            tool_calls=[ToolCall(
                tool_call_id=f"tc{i}", tool_name="Edit",
                input={"file_path": fp, "old_string": old, "new_string": new},
            )],
            observations=[],
            token_usage=TokenUsage(),
        ))
    return TraceRecord(
        trace_id=trace_id, session_id="s", agent=Agent(name="claude-code"),
        steps=steps,
        timestamp_end=when.isoformat(),
    )


@pytest.fixture
def repo(tmp_path):
    _sh(["git", "init", "-q", "-b", "main"], tmp_path)
    _sh(["git", "config", "user.email", "t@t.t"], tmp_path)
    _sh(["git", "config", "user.name", "t"], tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("hello\n")
    _sh(["git", "add", "-A"], tmp_path)
    _sh(["git", "commit", "-qm", "init"], tmp_path)
    return tmp_path


class TestPostCommit:
    def test_tool_emitted_trace_gets_note(self, repo):
        # Agent's edit + matching commit.
        (repo / "src" / "app.py").write_text("hello\nGREETING_LINE\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "add greeting"], repo)

        trace = _trace(
            "trace-alpha",
            [("src/app.py", "hello", "hello\nGREETING_LINE")],
            datetime.now(timezone.utc),
        )
        results = post_commit.run(repo, [trace])
        assert len(results) == 1
        trace_id, links = results[0]
        assert trace_id == "trace-alpha"
        assert links[0].tier == "tool_emitted"

        sha = post_commit.head_sha(repo)
        assert "opentraces:trace-alpha" in notes_store.read(sha, repo)

    def test_merge_commit_skipped(self, repo):
        # Create a feature branch with a distinct commit.
        _sh(["git", "checkout", "-q", "-b", "feat"], repo)
        (repo / "b.py").write_text("b\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "feat commit"], repo)
        _sh(["git", "checkout", "-q", "main"], repo)
        # Divergent change on main.
        (repo / "c.py").write_text("c\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "main commit"], repo)
        # Merge feat into main; --no-ff forces a merge commit.
        _sh(["git", "merge", "--no-ff", "-q", "-m", "merge", "feat"], repo)

        trace = _trace(
            "trace-beta", [("b.py", "x", "b")],
            datetime.now(timezone.utc),
        )
        results = post_commit.run(repo, [trace])
        assert results == []

        sha = post_commit.head_sha(repo)
        assert notes_store.read(sha, repo) == []

    def test_orphan_trace_not_noted(self, repo):
        # Truly empty trace (no edits at all) → tier orphan → no note.
        (repo / "src" / "app.py").write_text("hello\nGREETING\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "unrelated commit"], repo)

        trace = TraceRecord(
            trace_id="trace-gamma", session_id="s",
            agent=Agent(name="claude-code"), steps=[],
            timestamp_end=datetime.now(timezone.utc).isoformat(),
        )
        results = post_commit.run(repo, [trace])
        assert results == []
        sha = post_commit.head_sha(repo)
        assert notes_store.read(sha, repo) == []

    def test_overlapping_trace_is_noted(self, repo):
        # Agent edited src/other.py but commit was on src/app.py —
        # time-window coincidence + honest overlapping tier. Still
        # worth surfacing as evidence.
        (repo / "src" / "app.py").write_text("hello\nAPP_LINE\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "app change"], repo)

        trace = _trace(
            "trace-overlap", [("src/other.py", "x", "y")],
            datetime.now(timezone.utc),
        )
        results = post_commit.run(repo, [trace])
        assert len(results) == 1
        assert results[0][1][0].tier == "overlapping"

    def test_window_excludes_old_traces(self, repo):
        (repo / "src" / "app.py").write_text("hello\nGREETING\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c"], repo)

        old = _trace(
            "trace-ancient", [("src/app.py", "hello", "hello\nGREETING")],
            datetime.now(timezone.utc) - timedelta(days=7),
        )
        results = post_commit.run(repo, [old], window_hours=1.0)
        assert results == []

    def test_append_only_survives_multiple_calls(self, repo):
        (repo / "src" / "app.py").write_text("hello\nGREETING\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c"], repo)

        now = datetime.now(timezone.utc)
        t1 = _trace("one", [("src/app.py", "hello", "hello\nGREETING")], now)
        t2 = _trace("two", [("src/app.py", "hello", "hello\nGREETING")], now)
        post_commit.run(repo, [t1])
        post_commit.run(repo, [t2])
        sha = post_commit.head_sha(repo)
        lines = notes_store.read(sha, repo)
        assert "opentraces:one" in lines
        assert "opentraces:two" in lines
