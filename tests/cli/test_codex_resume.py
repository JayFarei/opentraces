"""Codex CLI native resume handoff tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def codex_resume_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".opentraces.json").write_text(
        '{"project_id": "codex-resume", "policy": {}}'
    )
    monkeypatch.chdir(project)

    from opentraces.core import config as _config

    traces_dir = _config.get_project_traces_dir(project)
    traces_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "schema_version": "0.3.0",
        "trace_id": "c0dex9c8-full-id-1234",
        "session_id": "codex-session-5678",
        "agent": {"name": "codex-cli", "model": "openai/gpt-5-codex"},
        "task": {"description": "resume a Codex session"},
        "timestamp_start": "2026-05-21T00:00:00Z",
        "timestamp_end": "2026-05-21T00:05:00Z",
        "steps": [
            {
                "step_index": 1,
                "role": "user",
                "content": "start",
                "call_type": "main",
                "parent_step": None,
            }
        ],
        "metrics": {},
    }
    (traces_dir / "c0dex9c8-full-id-1234.jsonl").write_text(
        json.dumps(record) + "\n"
    )

    from opentraces.core import state as _state

    st = _state.StateManager(_config.get_project_state_path(project))
    st._state.setdefault("traces", {})["c0dex9c8-full-id-1234"] = {
        "trace_id": "c0dex9c8-full-id-1234",
        "session_id": "codex-session-5678",
        "status": "parsed",
        "created_at": 0.0,
    }
    st.save()

    return project


def test_codex_resumer_registered() -> None:
    from opentraces import capture
    from opentraces.capture.codex_cli.resume import CodexCliResumer

    assert capture.get_resumer("codex-cli") is CodexCliResumer
    assert capture.get_resumer("codex") is CodexCliResumer
    assert CodexCliResumer.supports_at_step is False


def test_codex_resume_dry_run_prints_native_command(
    runner: CliRunner,
    codex_resume_project: Path,
) -> None:
    from opentraces.cli import main

    with patch("shutil.which", return_value="/usr/local/bin/codex"):
        result = runner.invoke(main, ["trail", "resume", "t:c0de", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "/usr/local/bin/codex resume codex-session-5678" in result.output


def test_codex_resume_exec_calls_subprocess(
    runner: CliRunner,
    codex_resume_project: Path,
) -> None:
    from opentraces.cli import main

    calls: list[tuple[list[str], Path | None]] = []

    def fake_call(argv: list[str], cwd: Path | None = None) -> int:
        calls.append((argv, cwd))
        return 23

    with patch("shutil.which", return_value="/usr/local/bin/codex"), patch(
        "opentraces.capture.codex_cli.resume.subprocess.call",
        side_effect=fake_call,
    ):
        result = runner.invoke(main, ["trail", "resume", "t:c0de"])

    assert result.exit_code == 23, result.output
    assert calls == [
        (
            ["/usr/local/bin/codex", "resume", "codex-session-5678"],
            codex_resume_project,
        )
    ]


def test_codex_resume_at_step_is_explicitly_unsupported(
    runner: CliRunner,
    codex_resume_project: Path,
) -> None:
    from opentraces.cli import main

    result = runner.invoke(
        main,
        ["trail", "resume", "t:c0de", "--at-step", "s1", "--dry-run"],
    )

    assert result.exit_code == 2, result.output
    assert "--at-step resume is not supported for codex-cli traces." in result.output
