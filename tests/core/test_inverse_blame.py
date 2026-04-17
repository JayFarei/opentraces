"""Inverse-blame core module — contract tests.

Covers the four cases the surface callers (CLI + web) rely on:

1. Empty: no attribution cache, no git_links → empty result.
2. Attribution-cache only: line counts, no tier.
3. git_links only: hook-linked commits, tier set, lines_in_commit=0.
4. Both merged: cache wins line count, tier annotated from git_links.

Also asserts the ``include_overlapping`` gate filters noise by default
and that session_id↔trace_id legacy matching works — exercising the
real-world foot-gun where the attribution cache keys on session_id.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from opentraces.core import inverse_blame as _ib


@pytest.fixture
def ot_home(tmp_path, monkeypatch):
    """Redirect ``~/.opentraces`` at the module level.

    ``core.paths`` + ``core.config`` both bind their per-user roots at
    import time from ``Path.home()``, so patching HOME alone is not enough.
    """
    root = tmp_path / "ot-home"
    ot_dir = root / ".opentraces"
    projects = ot_dir / "projects"
    projects.mkdir(parents=True)
    import opentraces.core.config as _config
    import opentraces.core.paths as _paths
    monkeypatch.setattr(_paths, "OPENTRACES_DIR", ot_dir)
    monkeypatch.setattr(_paths, "PROJECTS_DIR", projects)
    monkeypatch.setattr(_paths, "CONFIG_PATH", ot_dir / "config.json")
    monkeypatch.setattr(_paths, "CREDENTIALS_PATH", ot_dir / "credentials")
    monkeypatch.setattr(_config, "PROJECTS_DIR", projects)
    monkeypatch.setattr(_config, "OPENTRACES_DIR", ot_dir)
    # Bust the trace_meta resolver cache between tests — it's keyed on
    # the project's on-disk path which we recycle across runs.
    from opentraces.core.trace_meta import clear_cache
    clear_cache()
    return root


def _init_repo(repo: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()


def _opt_in(repo: Path) -> None:
    from opentraces.core.config import _write_marker
    _write_marker(repo, uuid.uuid4().hex, {})


def _write_attribution(repo: Path, sha: str, traces: list[dict],
                       files: dict | None = None) -> None:
    from opentraces.core.cache import AttributionCache
    cache = AttributionCache(repo)
    cache.write_attribution(sha, {
        "traces": traces,
        "files": files or {},
        "coverage": {
            "attributed": sum(t.get("line_count", 0) for t in traces),
            "total": max(sum(t.get("line_count", 0) for t in traces), 1),
            "ratio": 1.0,
        },
    })


def _write_trace_record(repo: Path, trace_id: str, session_id: str | None = None,
                        git_links: list[dict] | None = None,
                        timestamp_end: str = "2026-04-01T00:00:00Z") -> None:
    from opentraces.core.config import get_project_traces_dir
    staging = get_project_traces_dir(repo)
    staging.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "0.1.0",
        "trace_id": trace_id,
        "session_id": session_id or trace_id,
        "content_hash": "0" * 64,
        "timestamp_start": "2026-04-01T00:00:00Z",
        "timestamp_end": timestamp_end,
        "task": {"description": "test"},
        "agent": {"name": "claude-code", "model": "claude-opus-4-6"},
        "environment": {},
        "system_prompts": {},
        "tool_definitions": [],
        "steps": [],
        "outcome": {},
        "metrics": {},
        "security": {},
        "attribution": None,
        "dependencies": [],
        "metadata": {},
        "git_links": git_links or [],
        "lifecycle": "provisional",
    }
    (staging / f"{trace_id}.jsonl").write_text(json.dumps(record) + "\n")


def _make_commit(repo: Path, path: str, content: str, msg: str) -> str:
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.run(["git", "add", path], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()


class TestInverseBlame:
    def test_empty_returns_empty_result(self, tmp_path, ot_home, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        result = _ib.compute(repo, "a" * 16)
        assert result.trace_id == "a" * 16
        assert result.commits == []
        assert result.files == []
        assert result.total_lines == 0

    def test_attribution_only_returns_line_counts(self, tmp_path, ot_home, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _make_commit(repo, "a.py", "x = 1\ny = 2\n", "add a")
        trace_id = "t" * 32
        _write_attribution(repo, sha, [
            {"trace_id": trace_id, "line_count": 2, "files": ["a.py"]}
        ])

        result = _ib.compute(repo, trace_id)
        assert len(result.commits) == 1
        commit = result.commits[0]
        assert commit.sha == sha
        assert commit.source == "attribution"
        assert commit.lines_in_commit == 2
        assert commit.tier is None
        assert result.total_lines == 2
        assert result.files == [("a.py", 2)]

    def test_git_links_only_returns_tier_rows(self, tmp_path, ot_home, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _make_commit(repo, "a.py", "x = 1\n", "add a")
        trace_id = "b" * 36
        _write_trace_record(repo, trace_id, git_links=[
            {"vcs_type": "git", "revision": sha, "tier": "tool_emitted"},
        ])

        result = _ib.compute(repo, trace_id)
        assert len(result.commits) == 1
        commit = result.commits[0]
        assert commit.sha == sha
        assert commit.source == "git_links"
        assert commit.tier == "tool_emitted"
        assert commit.lines_in_commit == 0  # unknown, not fabricated

    def test_attribution_and_git_links_merge(self, tmp_path, ot_home, monkeypatch):
        """When a commit appears in both sources, attribution wins the
        line count and git_links contributes the tier annotation."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha1 = _make_commit(repo, "a.py", "x = 1\n", "add a")
        sha2 = _make_commit(repo, "b.py", "y = 2\n", "add b")
        trace_id = "c" * 36
        _write_attribution(repo, sha1, [
            {"trace_id": trace_id, "line_count": 5, "files": ["a.py"]}
        ])
        # sha2 appears only in git_links.
        _write_trace_record(repo, trace_id, git_links=[
            {"vcs_type": "git", "revision": sha1, "tier": "tool_emitted"},
            {"vcs_type": "git", "revision": sha2,
             "tier": "tool_emitted_with_divergence"},
        ])

        result = _ib.compute(repo, trace_id)
        assert len(result.commits) == 2
        attr = next(c for c in result.commits if c.sha == sha1)
        links = next(c for c in result.commits if c.sha == sha2)
        assert attr.source == "attribution"
        assert attr.lines_in_commit == 5
        assert attr.tier == "tool_emitted"  # annotated from git_links merge
        assert links.source == "git_links"
        assert links.tier == "tool_emitted_with_divergence"
        assert links.lines_in_commit == 0

    def test_overlapping_filtered_by_default(self, tmp_path, ot_home, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha_tool = _make_commit(repo, "a.py", "x = 1\n", "tool-emit")
        sha_lap = _make_commit(repo, "b.py", "y = 2\n", "overlap-only")
        trace_id = "d" * 36
        _write_trace_record(repo, trace_id, git_links=[
            {"vcs_type": "git", "revision": sha_tool, "tier": "tool_emitted"},
            {"vcs_type": "git", "revision": sha_lap, "tier": "overlapping"},
        ])

        default = _ib.compute(repo, trace_id)
        assert {c.sha for c in default.commits} == {sha_tool}

        with_lap = _ib.compute(repo, trace_id, include_overlapping=True)
        assert {c.sha for c in with_lap.commits} == {sha_tool, sha_lap}

    def test_attribution_cache_matches_via_session_id(
        self, tmp_path, ot_home, monkeypatch,
    ):
        """Legacy caches stored the session_id in the ``trace_id`` field.
        ``compute()`` must still find those rows when asked by real trace_id."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _make_commit(repo, "a.py", "x = 1\n", "add a")
        trace_id = "real-trace-" + "1" * 24
        session_id = "sess-" + "2" * 30
        # Attribution cache (legacy): keys on session_id as "trace_id".
        _write_attribution(repo, sha, [
            {"trace_id": session_id, "line_count": 10, "files": ["a.py"]}
        ])
        # Trace record: real trace_id plus session_id field.
        _write_trace_record(repo, trace_id, session_id=session_id)

        # Query by the real trace_id — must still match the legacy cache row.
        result = _ib.compute(repo, trace_id)
        assert len(result.commits) == 1
        assert result.commits[0].lines_in_commit == 10
        assert result.total_lines == 10

    def test_sort_order_attribution_first_then_tiers(
        self, tmp_path, ot_home, monkeypatch,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha1 = _make_commit(repo, "a.py", "x = 1\n" * 20, "big")
        sha2 = _make_commit(repo, "b.py", "y = 2\n" * 5, "small")
        sha3 = _make_commit(repo, "c.py", "z = 3\n", "link only")
        trace_id = "e" * 36
        _write_attribution(repo, sha1, [
            {"trace_id": trace_id, "line_count": 20, "files": ["a.py"]}
        ])
        _write_attribution(repo, sha2, [
            {"trace_id": trace_id, "line_count": 5, "files": ["b.py"]}
        ])
        _write_trace_record(repo, trace_id, git_links=[
            {"vcs_type": "git", "revision": sha3, "tier": "tool_emitted"},
        ])

        result = _ib.compute(repo, trace_id)
        order = [c.sha for c in result.commits]
        assert order == [sha1, sha2, sha3]
        # Attribution rows precede hook-linked ones and sort by lines desc.
        assert [c.source for c in result.commits] == [
            "attribution", "attribution", "git_links",
        ]
