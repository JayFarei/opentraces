"""CLI: ``ot blame <trace-id>`` — trace-mode (inverse blame) surface.

The commit-mode path has existing coverage in ``tests/cli/test_blame*``.
These tests focus on:

* Mode resolution — ``t:<id>`` / ``c:<sha>`` prefixes + bare-arg auto
  detection (UUID→trace, hex→commit first).
* Output shape — text headline + per-commit rows; ``--json`` shape.
* Guard rails — ``--lines`` and ``--entities`` reject on trace-mode.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli.blame import blame_cmd


@pytest.fixture
def ot_home(tmp_path, monkeypatch):
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
    from opentraces.core.trace_meta import clear_cache
    clear_cache()
    return root


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _opt_in(repo: Path) -> None:
    from opentraces.core.config import _write_marker
    _write_marker(repo, uuid.uuid4().hex, {})


def _commit(repo: Path, path: str, content: str, msg: str) -> str:
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.run(["git", "add", path], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()


def _seed(repo: Path, trace_id: str, sha: str, *,
          attribution_lines: int = 0,
          git_link_tier: str | None = None) -> None:
    """Write both the staged JSONL and (optionally) an attribution-cache
    entry for ``(trace_id, sha)`` so the trace-mode CLI can find commits."""
    from opentraces.core.cache import AttributionCache
    from opentraces.core.config import get_project_traces_dir

    staging = get_project_traces_dir(repo)
    staging.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "0.1.0",
        "trace_id": trace_id,
        "session_id": trace_id,
        "content_hash": "0" * 64,
        "timestamp_start": "2026-04-01T00:00:00Z",
        "timestamp_end": "2026-04-01T01:00:00Z",
        "task": {"description": "synthetic"},
        "agent": {"name": "claude-code", "model": "claude-opus-4-6"},
        "environment": {}, "system_prompts": {}, "tool_definitions": [],
        "steps": [], "outcome": {}, "metrics": {}, "security": {},
        "attribution": None, "dependencies": [], "metadata": {},
        "git_links": (
            [{"vcs_type": "git", "revision": sha, "tier": git_link_tier}]
            if git_link_tier else []
        ),
        "lifecycle": "provisional",
    }
    (staging / f"{trace_id}.jsonl").write_text(json.dumps(record) + "\n")

    if attribution_lines > 0:
        cache = AttributionCache(repo)
        cache.write_attribution(sha, {
            "traces": [{
                "trace_id": trace_id,
                "line_count": attribution_lines,
                "files": ["a.py"],
            }],
            "files": {},
            "coverage": {
                "attributed": attribution_lines,
                "total": attribution_lines,
                "ratio": 1.0,
            },
        })


def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(blame_cmd, args + ["--project", str(cwd), "--no-color"])
    return result.exit_code, result.output


class TestTraceModeCLI:
    def test_trace_prefix_resolves_and_renders(self, tmp_path, monkeypatch, ot_home):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _commit(repo, "a.py", "x = 1\n", "add a")
        trace_id = "deadbeef-1234-4000-8000-000000000001"
        _seed(repo, trace_id, sha, attribution_lines=3)

        code, out = _run([f"t:{trace_id[:8]}"], repo)
        assert code == 0, out
        assert "Trace: t:deadbeef" in out
        assert sha[:8] in out
        assert "add a" in out
        assert "3 of" in out  # "3 of N diff lines"

    def test_bare_uuid_is_trace_mode(self, tmp_path, monkeypatch, ot_home):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _commit(repo, "a.py", "x = 1\n", "add a")
        trace_id = "feedcafe-1234-4000-8000-000000000002"
        _seed(repo, trace_id, sha, git_link_tier="tool_emitted")

        code, out = _run([trace_id], repo)
        assert code == 0, out
        assert "Trace: t:feedcafe" in out
        assert "tool-emitted" in out

    def test_commit_prefix_forces_commit_mode(self, tmp_path, monkeypatch, ot_home):
        """``c:<sha>`` bypasses trace-mode even if the sha prefix collides
        with a trace-id prefix (possible with short hex strings)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _commit(repo, "a.py", "x = 1\n", "add a")
        trace_id = "cafebabe-1234-4000-8000-000000000003"
        _seed(repo, trace_id, sha, attribution_lines=1)

        code, out = _run([f"c:{sha}"], repo)
        assert code == 0, out
        # Commit-mode renders "Commit:" not "Trace:"
        assert "Commit:" in out or "commit" in out.lower()

    def test_trace_mode_json_shape(self, tmp_path, monkeypatch, ot_home):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _commit(repo, "a.py", "x = 1\n", "add a")
        trace_id = "12345678-1234-4000-8000-000000000004"
        _seed(repo, trace_id, sha, attribution_lines=7,
              git_link_tier="tool_emitted")

        code, out = _run([f"t:{trace_id}", "--json"], repo)
        assert code == 0, out
        data = json.loads(out)
        # Uniform L5 envelope header (additive; trace-mode shape version).
        assert data["status"] == "ok"
        assert data["schema_version"] == "opentraces.trail.blame_trace.v1"
        assert data["trace"]["trace_id"] == trace_id
        assert len(data["commits"]) == 1
        commit = data["commits"][0]
        assert commit["sha"] == sha
        assert commit["source"] == "attribution"
        assert commit["linesInCommit"] == 7

    def test_trace_mode_rejects_lines_flag(self, tmp_path, monkeypatch, ot_home):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _commit(repo, "a.py", "x = 1\n", "add a")
        trace_id = "abababab-1234-4000-8000-000000000005"
        _seed(repo, trace_id, sha, attribution_lines=1)

        code, out = _run([f"t:{trace_id}", "--lines"], repo)
        assert code == 2
        assert "commit-mode only" in out

    def test_unknown_id_errors(self, tmp_path, monkeypatch, ot_home):
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        code, out = _run(["t:nosuchtrace"], repo)
        assert code == 2
        assert "Unknown trace" in out or "Unknown id" in out

    def test_empty_inverse_blame_renders_gracefully(
        self, tmp_path, monkeypatch, ot_home,
    ):
        """A staged trace with no attribution and no git_links should
        render a header + "no commits attributed" line, not crash."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        trace_id = "7c7c7c7c-1234-4000-8000-000000000006"
        # No commit, no attribution, no git_links — just a bare staged
        # record. Real-world: a freshly-captured session before any
        # subsequent commit lands.
        _seed(repo, trace_id, sha="0" * 40, attribution_lines=0)

        code, out = _run([f"t:{trace_id}"], repo)
        assert code == 0, out
        assert "no commits attributed" in out.lower()
