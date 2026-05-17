"""Deterministic Claude Code session module — ``context-tree-linear``.

Baseline fixture for plan 077 (Context Tree substrate). One user prompt,
three tool calls (Read, Edit, Bash), one assistant response, no branching.
The active path is exactly one chain root-to-leaf; orphan branches must
be empty; ``branch_type`` for every node must be ``linear`` after the root.

    user prompt: "tidy up greeting() and confirm with a test run"
        -> assistant turn 1: Read tool_use against src/greeting.py
        -> assistant turn 2: Edit tool_use that trims trailing whitespace
        -> assistant turn 3: Bash tool_use that runs `python -m unittest`
        -> assistant turn 4: confirmation + Stop

Every JSONL record carries ``parentUuid`` so the Context Tree parser can
walk the parent graph deterministically. The chain is strictly linear:

    system.init  ->  user-1  ->  asst-1 (Read)  ->  tool_result-1
                 ->  asst-2 (Edit)  ->  tool_result-2
                 ->  asst-3 (Bash)  ->  tool_result-3
                 ->  asst-4 (close)

Same Pre/PostToolUse hook policy as ``simple-refactor.session``; the
Stop hook is deliberately omitted because its ``main()`` fire-and-forgets
a detached ``_ingest-session`` subprocess that would race the checkpoint
ordering (ingest -> commit -> mature).
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SESSION_ID = "sess-otbox-context-tree-linear"
MODEL = "claude-sonnet-4-5"
HARNESS_VERSION = "otbox-fake-1.0"
TOOL_READ_ID = "toolu_otbox_ctx_linear_read"
TOOL_EDIT_ID = "toolu_otbox_ctx_linear_edit"
TOOL_BASH_ID = "toolu_otbox_ctx_linear_bash"

_SOURCE_BEFORE = (
    "def greeting(name: str) -> str:   \n"
    "    return f\"hi, {name}\"   \n"
)
_SOURCE_AFTER = (
    "def greeting(name: str) -> str:\n"
    "    return f\"hi, {name}\"\n"
)

_BASH_STDOUT = (
    "..\n"
    "----------------------------------------------------------------------\n"
    "Ran 2 tests in 0.003s\n"
    "\n"
    "OK\n"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append(transcript: Path, payload: dict) -> None:
    transcript.parent.mkdir(parents=True, exist_ok=True)
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _invoke_hook(main_func: Callable[[], None], payload: dict) -> None:
    """Run a Claude Code hook's ``main()`` with the stdin payload set."""
    old_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps(payload))
        main_func()
    finally:
        sys.stdin = old_stdin


def run(project: Path, transcript: Path) -> None:
    src_dir = project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    target = src_dir / "greeting.py"
    target.write_text(_SOURCE_BEFORE, encoding="utf-8")

    prior_cwd = Path.cwd()
    os.chdir(project)
    try:
        _run_session(project, target, transcript)
    finally:
        os.chdir(prior_cwd)


def _run_session(project: Path, target: Path, transcript: Path) -> None:
    # See simple-refactor.session for the Stop-hook rationale.
    from opentraces.capture.claude_code.hooks import on_pre_tool_use, on_tool_use

    sys_uuid = "sys-otbox-ctx-linear-init"
    user_uuid = "user-otbox-ctx-linear-1"
    asst_read_uuid = "asst-otbox-ctx-linear-1"
    tr_read_uuid = "user-otbox-ctx-linear-2"
    asst_edit_uuid = "asst-otbox-ctx-linear-2"
    tr_edit_uuid = "user-otbox-ctx-linear-3"
    asst_bash_uuid = "asst-otbox-ctx-linear-3"
    tr_bash_uuid = "user-otbox-ctx-linear-4"
    asst_close_uuid = "asst-otbox-ctx-linear-4"

    # 1. System init line.
    _append(transcript, {
        "type": "system",
        "subtype": "init",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": sys_uuid,
        "parentUuid": None,
        "cwd": str(project),
        "model": MODEL,
        "claude_code_version": HARNESS_VERSION,
        "tools": [
            {"name": "Read", "description": "Read a project file."},
            {"name": "Edit", "description": "Apply an exact-match edit."},
            {"name": "Bash", "description": "Run a shell command."},
        ],
        "permissionMode": "default",
        "gitBranch": "main",
    })

    # 2. User prompt.
    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": user_uuid,
        "parentUuid": sys_uuid,
        "message": {
            "role": "user",
            "content": (
                "Please tidy up src/greeting.py (drop the trailing spaces) "
                "and confirm with `python -m unittest discover` when done."
            ),
        },
    })

    # 3. Assistant: Read tool_use.
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_read_uuid,
        "parentUuid": user_uuid,
        "requestId": "req-otbox-ctx-linear-1",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Reading src/greeting.py to see the current shape."},
                {
                    "type": "tool_use",
                    "id": TOOL_READ_ID,
                    "name": "Read",
                    "input": {"file_path": "src/greeting.py"},
                },
            ],
            "usage": {"input_tokens": 120, "output_tokens": 40},
        },
    })

    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": TOOL_READ_ID,
        "tool_input": {"file_path": "src/greeting.py"},
    })

    read_content = target.read_text(encoding="utf-8")

    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": TOOL_READ_ID,
        "tool_input": {"file_path": "src/greeting.py"},
        "tool_response": {"content": read_content},
    })

    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": tr_read_uuid,
        "parentUuid": asst_read_uuid,
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": TOOL_READ_ID,
                "content": read_content,
            }],
        },
        "toolUseResult": {
            "file": {
                "filePath": str((project / "src" / "greeting.py")),
                "content": read_content,
                "numLines": read_content.count("\n"),
                "startLine": 1,
                "totalLines": read_content.count("\n"),
            },
        },
    })

    # 4. Assistant: Edit tool_use.
    edit_tool_input = {
        "file_path": "src/greeting.py",
        "old_string": _SOURCE_BEFORE,
        "new_string": _SOURCE_AFTER,
    }
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_edit_uuid,
        "parentUuid": tr_read_uuid,
        "requestId": "req-otbox-ctx-linear-2",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Dropping the trailing whitespace."},
                {
                    "type": "tool_use",
                    "id": TOOL_EDIT_ID,
                    "name": "Edit",
                    "input": edit_tool_input,
                },
            ],
            "usage": {"input_tokens": 160, "output_tokens": 80},
        },
    })

    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": TOOL_EDIT_ID,
        "tool_input": edit_tool_input,
    })

    target.write_text(_SOURCE_AFTER, encoding="utf-8")

    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": TOOL_EDIT_ID,
        "tool_input": edit_tool_input,
        "tool_response": {"content": "The file src/greeting.py has been updated."},
    })

    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": tr_edit_uuid,
        "parentUuid": asst_edit_uuid,
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": TOOL_EDIT_ID,
                "content": "The file src/greeting.py has been updated.",
            }],
        },
        "toolUseResult": {
            "oldString": _SOURCE_BEFORE,
            "newString": _SOURCE_AFTER,
            "originalFile": _SOURCE_BEFORE,
            "userModified": False,
            "replaceAll": False,
            "structuredPatch": [],
        },
    })

    # 5. Assistant: Bash tool_use (test run).
    bash_command = "python -m unittest discover -v"
    bash_tool_input = {"command": bash_command, "description": "Run the unit tests."}
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_bash_uuid,
        "parentUuid": tr_edit_uuid,
        "requestId": "req-otbox-ctx-linear-3",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Confirming with the unittest discover run."},
                {
                    "type": "tool_use",
                    "id": TOOL_BASH_ID,
                    "name": "Bash",
                    "input": bash_tool_input,
                },
            ],
            "usage": {"input_tokens": 80, "output_tokens": 40},
        },
    })

    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_use_id": TOOL_BASH_ID,
        "tool_input": bash_tool_input,
    })

    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_use_id": TOOL_BASH_ID,
        "tool_input": bash_tool_input,
        "tool_response": {"stdout": _BASH_STDOUT, "stderr": "", "interrupted": False},
    })

    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": tr_bash_uuid,
        "parentUuid": asst_bash_uuid,
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": TOOL_BASH_ID,
                "content": _BASH_STDOUT,
            }],
        },
        "toolUseResult": {
            "stdout": _BASH_STDOUT,
            "stderr": "",
            "interrupted": False,
            "isImage": False,
        },
    })

    # 6. Final assistant turn (close the conversation).
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_close_uuid,
        "parentUuid": tr_bash_uuid,
        "requestId": "req-otbox-ctx-linear-4",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": "Trimmed the trailing whitespace and the unittest suite passes.",
            "usage": {"input_tokens": 80, "output_tokens": 12},
        },
    })

    # 7. Result message — session-end marker.
    _append(transcript, {
        "type": "result",
        "subtype": "success",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "duration_ms": 9000,
        "num_turns": 4,
        "stop_reason": "stop",
        "total_cost_usd": 0.0,
    })
