from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _minimal_trace() -> dict[str, object]:
    return {
        "trace_id": "capture-trace",
        "session_id": "capture-session",
        "timestamp_start": "2026-04-01T10:00:00Z",
        "task": {"description": "TUI capture regression"},
        "agent": {"name": "capture-agent", "model": "capture-model"},
        "steps": [
            {"role": "user", "content": "CAPTURE_USER_SENTINEL_1234567890", "step_index": 0},
            {
                "role": "assistant",
                "content": "CAPTURE_AGENT_SENTINEL_ABCDEFGHIJ",
                "step_index": 1,
            },
        ],
        "metrics": {"total_steps": 2, "total_input_tokens": 1, "total_output_tokens": 1},
    }


def _tool_call_trace() -> dict[str, object]:
    return {
        "trace_id": "capture-tool-trace",
        "session_id": "capture-tool-session",
        "timestamp_start": "2026-04-01T10:00:00Z",
        "task": {"description": "TUI capture tool-call regression"},
        "agent": {"name": "capture-agent", "model": "capture-model"},
        "steps": [
            {"role": "user", "content": "CAPTURE_TOOL_USER_SENTINEL", "step_index": 0},
            {
                "role": "assistant",
                "content": "",
                "step_index": 1,
                "tool_calls": [
                    {
                        "tool_name": "Read",
                        "input": {"file_path": "CAPTURE_TOOL_PATH.md"},
                        "status": "success",
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "",
                "step_index": 2,
                "tool_calls": [
                    {
                        "tool_name": "Bash",
                        "input": {"command": "printf 'CAPTURE_TOOL_BASH_SENTINEL'"},
                        "status": "success",
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "CAPTURE_TOOL_AGENT_SENTINEL",
                "step_index": 3,
            },
        ],
        "metrics": {"total_steps": 4, "total_input_tokens": 1, "total_output_tokens": 1},
    }


def _task_tool_trace() -> dict[str, object]:
    return {
        "trace_id": "capture-task-trace",
        "session_id": "capture-task-session",
        "timestamp_start": "2026-04-01T10:00:00Z",
        "task": {"description": "TUI capture task rendering regression"},
        "agent": {"name": "capture-agent", "model": "capture-model"},
        "steps": [
            {"role": "user", "content": "CAPTURE_TASK_USER_SENTINEL", "step_index": 0},
            {
                "role": "assistant",
                "content": "",
                "step_index": 1,
                "tool_calls": [
                    {
                        "tool_name": "TaskCreate",
                        "input": {
                            "subject": "CAPTURE_TASK_PHASE_ONE",
                            "description": "CAPTURE_TASK_DESCRIPTION_SENTINEL",
                        },
                        "status": "success",
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "",
                "step_index": 2,
                "tool_calls": [
                    {
                        "tool_name": "TaskUpdate",
                        "input": {
                            "taskId": "1",
                            "status": "in_progress",
                            "activeForm": "CAPTURE_TASK_ACTIVE_FORM",
                        },
                        "status": "success",
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "",
                "step_index": 3,
                "tool_calls": [
                    {
                        "tool_name": "delegate_task",
                        "input": {
                            "tasks": [
                                {
                                    "goal": "CAPTURE_TASK_GOAL_ONE",
                                    "context": "CAPTURE_TASK_CONTEXT_ONE",
                                    "toolsets": ["web"],
                                }
                            ]
                        },
                        "status": "success",
                    }
                ],
            },
        ],
        "metrics": {"total_steps": 4, "total_input_tokens": 1, "total_output_tokens": 1},
    }


def _todo_tool_trace() -> dict[str, object]:
    return {
        "trace_id": "capture-todo-trace",
        "session_id": "capture-todo-session",
        "timestamp_start": "2026-04-01T10:00:00Z",
        "task": {"description": "TUI capture todo rendering regression"},
        "agent": {"name": "capture-agent", "model": "capture-model"},
        "steps": [
            {"role": "user", "content": "CAPTURE_TODO_USER_SENTINEL", "step_index": 0},
            {
                "role": "assistant",
                "content": "",
                "step_index": 1,
                "tool_calls": [
                    {
                        "tool_name": "todo",
                        "input": {
                            "todos": [
                                {"id": "1", "content": "CAPTURE_TODO_ITEM_ONE", "status": "pending"},
                                {"id": "2", "content": "CAPTURE_TODO_ITEM_TWO", "status": "in_progress"},
                                {"id": "3", "content": "CAPTURE_TODO_ITEM_THREE", "status": "completed"},
                            ]
                        },
                        "status": "success",
                    }
                ],
            },
        ],
        "metrics": {"total_steps": 2, "total_input_tokens": 1, "total_output_tokens": 1},
    }


def test_tui_capture_tool_generates_live_snapshots(tmp_path: Path) -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is required for live TUI capture")

    repo_root = Path(__file__).resolve().parents[1]
    staging = tmp_path / ".opentraces" / "staging"
    staging.mkdir(parents=True)
    (staging / "capture-trace.jsonl").write_text(json.dumps(_minimal_trace()))

    output_dir = tmp_path / "capture-output"
    command = "cd {repo} && PYTHONPATH={src} {python} -m opentraces.clients.tui --staging-dir {staging}".format(
        repo=shlex.quote(str(repo_root)),
        src=shlex.quote(str(repo_root / "src")),
        python=shlex.quote(sys.executable),
        staging=shlex.quote(str(staging)),
    )

    subprocess.run(
        [
            sys.executable,
            str(repo_root / "tools" / "tui_capture.py"),
            "--output-dir",
            str(output_dir),
            "--command",
            command,
            "--startup-wait",
            "2.0",
            "--step-wait",
            "2.0",
            "--step",
            "open=Enter",
        ],
        check=True,
        cwd=repo_root,
    )

    manifest = json.loads((output_dir / "manifest.json").read_text())
    assert len(manifest["snapshots"]) == 2

    initial_text = (output_dir / "00_initial.txt").read_text()
    open_text = (output_dir / "01_open.txt").read_text()
    open_svg = (output_dir / "01_open.svg").read_text()

    assert "Enter to inspect" in initial_text
    assert "CAPTURE_USER_SENTINEL_1234567890" in open_text
    assert "CAPTURE_AGENT_SENTINEL_ABCDEFGHIJ" in open_text
    assert "1234567890" in open_svg
    assert "ABCDEFGHIJ" in open_svg


def test_tui_capture_shows_tool_call_only_steps(tmp_path: Path) -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is required for live TUI capture")

    repo_root = Path(__file__).resolve().parents[1]
    staging = tmp_path / ".opentraces" / "staging"
    staging.mkdir(parents=True)
    (staging / "capture-tool-trace.jsonl").write_text(json.dumps(_tool_call_trace()))

    output_dir = tmp_path / "capture-tool-output"
    command = "cd {repo} && PYTHONPATH={src} {python} -m opentraces.clients.tui --staging-dir {staging}".format(
        repo=shlex.quote(str(repo_root)),
        src=shlex.quote(str(repo_root / "src")),
        python=shlex.quote(sys.executable),
        staging=shlex.quote(str(staging)),
    )

    subprocess.run(
        [
            sys.executable,
            str(repo_root / "tools" / "tui_capture.py"),
            "--output-dir",
            str(output_dir),
            "--command",
            command,
            "--startup-wait",
            "2.0",
            "--step-wait",
            "2.0",
            "--step",
            "open=Enter",
        ],
        check=True,
        cwd=repo_root,
    )

    open_text = (output_dir / "01_open.txt").read_text()
    open_svg = (output_dir / "01_open.svg").read_text()

    assert "CAPTURE_TOOL_USER_SENTINEL" in open_text
    assert "Read" in open_text
    assert "CAPTURE_TOOL_PATH.md" in open_text
    assert "Bash" in open_text
    assert "CAPTURE_TOOL_BASH_SENTINEL" in open_text
    assert "CAPTURE_TOOL_AGENT_SENTINEL" in open_text
    assert "CAPTURE_TOOL_PATH.md" in open_svg
    assert "CAPTURE_TOOL_BASH_SENTINEL" in open_svg


def test_tui_capture_shows_task_tools_as_task_list(tmp_path: Path) -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is required for live TUI capture")

    repo_root = Path(__file__).resolve().parents[1]
    staging = tmp_path / ".opentraces" / "staging"
    staging.mkdir(parents=True)
    (staging / "capture-task-trace.jsonl").write_text(json.dumps(_task_tool_trace()))

    output_dir = tmp_path / "capture-task-output"
    command = "cd {repo} && PYTHONPATH={src} {python} -m opentraces.clients.tui --staging-dir {staging}".format(
        repo=shlex.quote(str(repo_root)),
        src=shlex.quote(str(repo_root / "src")),
        python=shlex.quote(sys.executable),
        staging=shlex.quote(str(staging)),
    )

    subprocess.run(
        [
            sys.executable,
            str(repo_root / "tools" / "tui_capture.py"),
            "--output-dir",
            str(output_dir),
            "--command",
            command,
            "--startup-wait",
            "2.0",
            "--step-wait",
            "2.0",
            "--step",
            "open=Enter",
        ],
        check=True,
        cwd=repo_root,
    )

    open_text = (output_dir / "01_open.txt").read_text()
    open_svg = (output_dir / "01_open.svg").read_text()

    assert "CAPTURE_TASK_USER_SENTINEL" in open_text
    assert "NEW" in open_text
    assert "DOING" in open_text
    assert "CAPTURE_TASK_PHASE_ONE" in open_text
    assert "CAPTURE_TASK_GOAL_ONE" in open_text
    assert "CAPTURE_TASK_CONTEXT_ONE" in open_text
    assert "subject=" not in open_text
    assert "taskId=" not in open_text
    assert "CAPTURE_TASK_PHASE_ONE" in open_svg
    assert "CAPTURE_TASK_GOAL_ONE" in open_svg


def test_tui_capture_shows_todo_tools_as_checklist(tmp_path: Path) -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is required for live TUI capture")

    repo_root = Path(__file__).resolve().parents[1]
    staging = tmp_path / ".opentraces" / "staging"
    staging.mkdir(parents=True)
    (staging / "capture-todo-trace.jsonl").write_text(json.dumps(_todo_tool_trace()))

    output_dir = tmp_path / "capture-todo-output"
    command = "cd {repo} && PYTHONPATH={src} {python} -m opentraces.clients.tui --staging-dir {staging}".format(
        repo=shlex.quote(str(repo_root)),
        src=shlex.quote(str(repo_root / "src")),
        python=shlex.quote(sys.executable),
        staging=shlex.quote(str(staging)),
    )

    subprocess.run(
        [
            sys.executable,
            str(repo_root / "tools" / "tui_capture.py"),
            "--output-dir",
            str(output_dir),
            "--command",
            command,
            "--startup-wait",
            "2.0",
            "--step-wait",
            "2.0",
            "--step",
            "open=Enter",
        ],
        check=True,
        cwd=repo_root,
    )

    open_text = (output_dir / "01_open.txt").read_text()
    open_svg = (output_dir / "01_open.svg").read_text()

    assert "CAPTURE_TODO_USER_SENTINEL" in open_text
    assert "TODOS" in open_text
    assert "CAPTURE_TODO_ITEM_ONE" in open_text
    assert "CAPTURE_TODO_ITEM_TWO" in open_text
    assert "CAPTURE_TODO_ITEM_THREE" in open_text
    assert "\"todos\"" not in open_text
    assert "CAPTURE_TODO_ITEM_ONE" in open_svg
    assert "CAPTURE_TODO_ITEM_TWO" in open_svg
