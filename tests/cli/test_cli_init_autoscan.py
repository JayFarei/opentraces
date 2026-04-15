"""``ot init`` autoscan (Phase 1 of live-session ingestion).

After the marker file is written and the project is registered, init
invokes ``scan_project`` so any pre-existing Claude Code sessions for
this repo land in the inbox immediately — no separate "now run scan"
step required. Controlled by the existing ``--import-existing`` /
``--start-fresh`` flag.

Preserves the existing progress bar UX while switching the underlying
ingestion mechanism to the generation-aware core.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli import main


def _git(*args, cwd):
    subprocess.check_call(["git", "-C", str(cwd), *args],
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)


def _init_git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "init", "-q", "-b", "main", str(root)],
                          stdout=subprocess.DEVNULL)
    _git("config", "user.email", "t@t", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    (root / "README.md").write_text("hi\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "initial", cwd=root)
    return root


def _write_synthetic_session(dir: Path, session_id: str, turns: int = 3) -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / f"{session_id}.jsonl"
    with path.open("w") as f:
        for i in range(1, turns + 1):
            ts = f"2026-04-15T07:00:{i:02d}Z"
            user = {"type": "user", "sessionId": session_id, "timestamp": ts,
                    "message": {"role": "user", "content": f"prompt {i}"}}
            asst = {"type": "assistant", "sessionId": session_id, "timestamp": ts,
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": f"tu_{i}",
                                     "name": "Read", "input": {"file_path": "x.py"}}],
                        "usage": {"input_tokens": 10, "output_tokens": 10},
                    }}
            result = {"type": "user", "sessionId": session_id, "timestamp": ts,
                      "message": {
                          "role": "user",
                          "content": [{"type": "tool_result", "tool_use_id": f"tu_{i}",
                                       "content": "ok"}],
                      }}
            for line in (user, asst, result):
                f.write(json.dumps(line) + "\n")
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_init_with_import_existing_populates_inbox_via_scan(
    tmp_path, runner, monkeypatch
) -> None:
    """`ot init --import-existing` stages pre-existing JSONLs into the inbox."""
    project = _init_git_repo(tmp_path / "repo")

    corpus = tmp_path / "synthetic_corpus"
    a = _write_synthetic_session(corpus, "sess-A", turns=3)
    b = _write_synthetic_session(corpus, "sess-B", turns=3)

    # Redirect both the legacy corpus discovery AND the new one to our
    # synthetic files. This lets the test exercise the init autoscan
    # regardless of whether the legacy path is still wired in parallel.
    monkeypatch.setattr(
        "opentraces.core.ingest.discover_claude_jsonl_corpus",
        lambda _repo: [a, b],
    )
    monkeypatch.setattr(
        "opentraces.cli._current_project_session_dir",
        lambda *a, **k: corpus,
    )
    monkeypatch.setattr(
        "opentraces.cli._is_interactive_terminal", lambda: False
    )
    monkeypatch.chdir(project)

    result = runner.invoke(main, [
        "init",
        "--review-policy", "review",
        "--remote", "test/opentraces",
        "--no-hook",
        "--import-existing",
    ])
    assert result.exit_code == 0, result.output

    # Marker wrote successfully.
    assert (project / ".opentraces.json").exists()

    # Inbox is populated via the generation model.
    from opentraces.core.state import StateManager
    from opentraces.core.config import get_project_state_path
    state = StateManager(state_path=get_project_state_path(project))

    sess_a = state.get_session("sess-A")
    sess_b = state.get_session("sess-B")
    assert sess_a is not None, "sess-A must be staged via scan_project"
    assert sess_b is not None, "sess-B must be staged via scan_project"
    assert len(sess_a.generations) == 1
    assert len(sess_b.generations) == 1


def test_init_start_fresh_leaves_inbox_empty(
    tmp_path, runner, monkeypatch
) -> None:
    """--start-fresh must not import existing sessions."""
    project = _init_git_repo(tmp_path / "repo")

    corpus = tmp_path / "synthetic_corpus"
    _write_synthetic_session(corpus, "sess-X", turns=3)

    monkeypatch.setattr(
        "opentraces.core.ingest.discover_claude_jsonl_corpus",
        lambda _repo: list(corpus.glob("*.jsonl")),
    )
    monkeypatch.setattr(
        "opentraces.cli._current_project_session_dir",
        lambda *a, **k: corpus,
    )
    monkeypatch.setattr(
        "opentraces.cli._is_interactive_terminal", lambda: False
    )
    monkeypatch.chdir(project)

    result = runner.invoke(main, [
        "init",
        "--review-policy", "review",
        "--remote", "test/opentraces",
        "--no-hook",
        "--start-fresh",
    ])
    assert result.exit_code == 0, result.output

    from opentraces.core.state import StateManager
    from opentraces.core.config import get_project_state_path
    state = StateManager(state_path=get_project_state_path(project))
    assert state.get_session("sess-X") is None, (
        "--start-fresh must not invoke scan_project"
    )
