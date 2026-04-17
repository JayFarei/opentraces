"""Commit-mode blame merges refs/notes/opentraces for hook-linked traces.

Symmetric to the trace-mode inverse-blame change: a commit's blame
view should surface trace_ids recorded on ``refs/notes/opentraces``
even when the attribution cache has no per-line data for them.

Three scenarios:

1. Attribution-only commit — existing path, no hook-linked section.
2. Notes-only commit — no attribution cache entry but the hook wrote
   a note; CLI renders a notes-only view rather than exiting.
3. Both — attribution section rendered first, hook-linked section
   listing only the trace_ids that AREN'T in the attribution cache.
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


def _write_note(repo: Path, sha: str, trace_id: str) -> None:
    from opentraces.enrichment.git import notes_store
    notes_store.append(sha, [notes_store.format_link(trace_id)], repo)


def _write_attribution(repo: Path, sha: str, trace_id: str, lines: int) -> None:
    from opentraces.core.cache import AttributionCache
    AttributionCache(repo).write_attribution(sha, {
        "traces": [{"trace_id": trace_id, "line_count": lines, "files": ["a.py"]}],
        "files": {},
        "coverage": {"attributed": lines, "total": lines, "ratio": 1.0},
    })


def _stage_trace(repo: Path, trace_id: str, session_id: str | None = None) -> None:
    from opentraces.core.config import get_project_traces_dir
    staging = get_project_traces_dir(repo)
    staging.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "0.1.0",
        "trace_id": trace_id,
        "session_id": session_id or trace_id,
        "content_hash": "0" * 64,
        "timestamp_start": "2026-04-01T00:00:00Z",
        "timestamp_end": "2026-04-01T01:00:00Z",
        "task": {"description": "test"},
        "agent": {"name": "claude-code", "model": "claude-opus-4-6"},
        "environment": {}, "system_prompts": {}, "tool_definitions": [],
        "steps": [], "outcome": {}, "metrics": {}, "security": {},
        "attribution": None, "dependencies": [], "metadata": {},
        "git_links": [], "lifecycle": "provisional",
    }
    (staging / f"{trace_id}.jsonl").write_text(json.dumps(record) + "\n")


def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(blame_cmd, args + ["--project", str(cwd), "--no-color"])
    return result.exit_code, result.output


class TestCommitModeNotes:
    def test_notes_only_commit_renders_hook_linked_section(
        self, tmp_path, monkeypatch, ot_home,
    ):
        """Commit has no attribution cache entry but has a note — the
        CLI should render a header + hook-linked trace rather than
        prompting for backfill."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _commit(repo, "a.py", "x = 1\n", "add a")
        trace_id = "aaaaaaaa-1234-4000-8000-000000000001"
        _stage_trace(repo, trace_id)
        _write_note(repo, sha, trace_id)
        # NO attribution — just the note.

        code, out = _run([f"c:{sha}", "--json"], repo)
        assert code == 0, out
        payload = json.loads(out)
        assert "hookLinked" in payload
        hook_ids = [h["trace_id"] for h in payload["hookLinked"]]
        assert trace_id in hook_ids

    def test_hook_linked_filtered_when_already_in_attribution(
        self, tmp_path, monkeypatch, ot_home,
    ):
        """Attribution + notes reference the same trace — the notes
        entry should not appear in ``hookLinked`` (already rendered
        in the main traces block with per-line data)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _commit(repo, "a.py", "x = 1\n", "add a")
        trace_id = "bbbbbbbb-1234-4000-8000-000000000002"
        _write_attribution(repo, sha, trace_id, lines=3)
        _write_note(repo, sha, trace_id)

        code, out = _run([f"c:{sha}", "--json"], repo)
        assert code == 0, out
        payload = json.loads(out)
        assert payload["hookLinked"] == []

    def test_hook_linked_dedups_by_session_id(
        self, tmp_path, monkeypatch, ot_home,
    ):
        """Legacy caches key on session_id. If attribution references
        session_id X but notes reference canonical trace_id Y (whose
        session_id is X), they're the same session and Y should NOT
        appear as hook-linked."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _commit(repo, "a.py", "x = 1\n", "add a")
        canonical_tid = "cccccccc-1234-4000-8000-000000000003"
        session_id = "ssssssss-1234-4000-8000-000000000099"
        _write_attribution(repo, sha, session_id, lines=7)  # legacy form
        _stage_trace(repo, canonical_tid, session_id=session_id)
        _write_note(repo, sha, canonical_tid)

        code, out = _run([f"c:{sha}", "--json"], repo)
        assert code == 0, out
        payload = json.loads(out)
        # Canonical trace_id should be filtered out — same session.
        hook_ids = [h["trace_id"] for h in payload["hookLinked"]]
        assert canonical_tid not in hook_ids

    def test_both_attribution_and_distinct_notes_surface_both(
        self, tmp_path, monkeypatch, ot_home,
    ):
        """Attribution has trace A with 5 lines; notes additionally
        reference trace B (no attribution). Both should surface: A in
        the traces block, B in hookLinked."""
        monkeypatch.setenv("HOME", str(tmp_path))
        repo = tmp_path / "proj"
        repo.mkdir()
        _init_repo(repo)
        _opt_in(repo)

        sha = _commit(repo, "a.py", "x = 1\n", "add a")
        trace_a = "aaaaaaaa-1234-4000-8000-000000000004"
        trace_b = "bbbbbbbb-1234-4000-8000-000000000005"
        _write_attribution(repo, sha, trace_a, lines=5)
        _stage_trace(repo, trace_a)
        _stage_trace(repo, trace_b)
        _write_note(repo, sha, trace_a)
        _write_note(repo, sha, trace_b)

        code, out = _run([f"c:{sha}", "--json"], repo)
        assert code == 0, out
        payload = json.loads(out)
        attr_tids = {t["trace_id"] for t in payload["traces"]}
        hook_tids = {h["trace_id"] for h in payload["hookLinked"]}
        assert trace_a in attr_tids
        assert trace_b in hook_tids
        assert trace_b not in attr_tids
        assert trace_a not in hook_tids
