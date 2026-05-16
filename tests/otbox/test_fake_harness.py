"""Tests for the fake Claude harness (plan 064 M64-1).

The harness is the seam between an "agent-like" actor and the captured
session JSONL. Production runs swap it for a real ``claude`` binary on
the box's PATH via ``OT_REAL_REPL=1`` (plan 064 R6). The fake harness
must keep that contract — same env, same outputs, same shape of
artefacts — so the swap is mechanical.

The harness is a Python executable (not a shell script): the session
module it loads invokes real opentraces PreToolUse / PostToolUse hooks
live against the box repo so the resulting transcript carries valid
``hook_pre_tool_use`` + ``hook_post_tool_use`` metadata, which is what
``emit_step_window_events_from_record`` needs to produce TrailEvents.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "fake_harnesses" / "claude"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sessions"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(env: dict, *, transcript_path: Path | None = None) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **env}
    # Run with the repo's `.venv` python so the harness imports the
    # editable opentraces install rather than the system python.
    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    interpreter = str(venv_py) if venv_py.exists() else sys.executable
    return subprocess.run(
        [interpreter, str(HARNESS)],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _init_git(project: Path) -> None:
    """Minimal real git repo so the hooks can compute tree_id / git_head."""
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "otbox@opentraces.local"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "otbox"], cwd=project, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=project, check=True)
    (project / "README.md").write_text("# harness smoke\n")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=project, check=True)


def test_harness_is_executable():
    assert HARNESS.exists(), HARNESS
    mode = HARNESS.stat().st_mode
    assert mode & stat.S_IXUSR, "harness must be executable"


def test_harness_requires_OTBOX_FAKE_SESSION(tmp_path: Path):
    result = _run({"OTBOX_PROJECT_DIR": str(tmp_path)})
    assert result.returncode == 2, result.stderr
    assert "OTBOX_FAKE_SESSION" in result.stderr


def test_harness_refuses_unknown_corpus(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    result = _run({
        "OTBOX_FAKE_SESSION": "does-not-exist",
        "OTBOX_PROJECT_DIR": str(project),
    })
    assert result.returncode == 3, result.stderr
    assert "does-not-exist" in result.stderr or "session.py" in result.stderr


def test_harness_runs_simple_refactor(tmp_path: Path):
    """The slice: harness writes transcript with hook lines + mutates file."""
    project = tmp_path / "project"
    project.mkdir()
    _init_git(project)
    transcript = tmp_path / "out" / "transcript.jsonl"
    result = _run({
        "OTBOX_FAKE_SESSION": "simple-refactor",
        "OTBOX_PROJECT_DIR": str(project),
        "OTBOX_TRANSCRIPT_PATH": str(transcript),
    })
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert transcript.exists(), "harness did not write OTBOX_TRANSCRIPT_PATH"

    lines = [json.loads(line) for line in transcript.read_text().splitlines() if line.strip()]
    types = {line.get("type") for line in lines}
    assert "system" in types
    assert "user" in types
    assert "assistant" in types
    assert "opentraces_hook" in types, (
        "harness must invoke PreToolUse/PostToolUse hooks live so the "
        "transcript carries opentraces_hook lines (otherwise the Trace "
        "Trails emitter sees no hook metadata)"
    )
    assert "result" in types

    # The Edit hook fires the actual file mutation.
    app_py = (project / "src" / "app.py").read_text()
    assert "def farewell(" in app_py, "harness did not apply the Edit"

    # PreToolUse + PostToolUse for both Read AND Edit.
    hook_events = [
        line for line in lines if line.get("type") == "opentraces_hook"
    ]
    pre_events = [e for e in hook_events if e.get("event") == "PreToolUse"]
    post_events = [e for e in hook_events if e.get("event") == "PostToolUse"]
    assert len(pre_events) >= 2, pre_events
    assert len(post_events) >= 2, post_events
    # Each PostToolUse for Edit must carry a content_hash + line range.
    edit_posts = [
        e for e in post_events
        if (e.get("data") or {}).get("tool") == "Edit"
    ]
    assert edit_posts, "no PostToolUse hook line for Edit"
    edit_post_data = edit_posts[-1]["data"]
    assert edit_post_data.get("content_hash")
    assert edit_post_data.get("start_line")
    assert edit_post_data.get("file_path", "").endswith("src/app.py")


def test_harness_streams_transcript_to_stdout(tmp_path: Path):
    """Without OTBOX_TRANSCRIPT_PATH the JSONL streams on stdout."""
    project = tmp_path / "project"
    project.mkdir()
    _init_git(project)
    result = _run({
        "OTBOX_FAKE_SESSION": "simple-refactor",
        "OTBOX_PROJECT_DIR": str(project),
    })
    assert result.returncode == 0, result.stderr
    lines = [
        json.loads(line) for line in result.stdout.splitlines() if line.strip()
    ]
    assert any(line.get("type") == "opentraces_hook" for line in lines), (
        "harness must emit hook lines on stdout in the no-output-path mode"
    )
    assert any(line.get("type") == "result" for line in lines)
