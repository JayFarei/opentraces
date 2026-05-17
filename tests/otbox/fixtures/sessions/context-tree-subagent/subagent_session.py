"""Sub-agent JSONL writer for the ``context-tree-subagent`` fixture.

Plan 077 fixture: this is the child JSONL the parent session's Task
tool_use invokes. The parser at ``opentraces.capture.claude_code.parse``
finds subagent JSONLs under ``<parent-transcript-stem>/subagents/<id>.jsonl``
and pairs them with the Task tool_use by ``description`` via the
sibling ``<id>.meta.json`` file.

``run(project, transcript, description)`` writes both the JSONL and the
accompanying meta.json so the parser can match the description back to
the parent's Task input. It returns a structured summary dict the
parent uses to render its post-subagent tool_result.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SUBAGENT_SESSION_ID = "sess-otbox-context-tree-subagent-child"
MODEL = "claude-sonnet-4-5"
HARNESS_VERSION = "otbox-fake-1.0"
TOOL_READ_ID = "toolu_otbox_ctx_subagent_child_read"
TOOL_GREP_ID = "toolu_otbox_ctx_subagent_child_grep"

_SUBAGENT_TYPE = "general-purpose"


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


def run(project: Path, transcript: Path, description: str) -> dict:
    """Write the subagent JSONL + meta.json next to ``transcript``.

    The parent passes the same `description` it set on its Task tool_use
    input so the parser's meta.json match step can wire the pair together.
    Returns a structured summary dict for the parent to render.
    """
    transcript.parent.mkdir(parents=True, exist_ok=True)
    # Sibling meta.json — the parser matches `description` against the
    # parent Task tool_use's input to pair the subagent JSONL.
    meta_path = transcript.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "session_id": SUBAGENT_SESSION_ID,
                "description": description,
                "subagent_type": _SUBAGENT_TYPE,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    # Real Claude Code launches the subagent with cwd=project; mirror that.
    prior_cwd = Path.cwd()
    os.chdir(project)
    try:
        summary = _run_subagent(project, transcript, description)
    finally:
        os.chdir(prior_cwd)
    return summary


def _run_subagent(project: Path, transcript: Path, description: str) -> dict:
    from opentraces.capture.claude_code.hooks import on_pre_tool_use, on_tool_use

    target = project / "src" / "lib.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(
            "def helper_one(x: int) -> int:\n"
            "    return x + 1\n"
            "\n"
            "def helper_two(x: int) -> int:\n"
            "    return x * 2\n",
            encoding="utf-8",
        )

    sys_uuid = "sys-otbox-ctx-subagent-child-init"
    user_uuid = "user-otbox-ctx-subagent-child-1"
    asst_read_uuid = "asst-otbox-ctx-subagent-child-read"
    tr_read_uuid = "user-otbox-ctx-subagent-child-read-result"
    asst_grep_uuid = "asst-otbox-ctx-subagent-child-grep"
    tr_grep_uuid = "user-otbox-ctx-subagent-child-grep-result"
    asst_close_uuid = "asst-otbox-ctx-subagent-child-close"

    # 1. System init line for the child session.
    _append(transcript, {
        "type": "system",
        "subtype": "init",
        "sessionId": SUBAGENT_SESSION_ID,
        "timestamp": _now(),
        "uuid": sys_uuid,
        "parentUuid": None,
        "cwd": str(project),
        "model": MODEL,
        "claude_code_version": HARNESS_VERSION,
        "tools": [
            {"name": "Read", "description": "Read a project file."},
            {"name": "Bash", "description": "Run a shell command."},
        ],
        "permissionMode": "default",
        "gitBranch": "main",
        "subagentType": _SUBAGENT_TYPE,
    })

    # 2. User prompt synthesized from the parent's Task description.
    _append(transcript, {
        "type": "user",
        "sessionId": SUBAGENT_SESSION_ID,
        "timestamp": _now(),
        "uuid": user_uuid,
        "parentUuid": sys_uuid,
        "message": {
            "role": "user",
            "content": description,
        },
    })

    # 3. Assistant: Read src/lib.py.
    _append(transcript, {
        "type": "assistant",
        "sessionId": SUBAGENT_SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_read_uuid,
        "parentUuid": user_uuid,
        "requestId": "req-otbox-ctx-subagent-child-1",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Reading src/lib.py to count helpers."},
                {
                    "type": "tool_use",
                    "id": TOOL_READ_ID,
                    "name": "Read",
                    "input": {"file_path": "src/lib.py"},
                },
            ],
            "usage": {"input_tokens": 80, "output_tokens": 30},
        },
    })

    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SUBAGENT_SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": TOOL_READ_ID,
        "tool_input": {"file_path": "src/lib.py"},
    })

    read_content = target.read_text(encoding="utf-8")

    _invoke_hook(on_tool_use.main, {
        "session_id": SUBAGENT_SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": TOOL_READ_ID,
        "tool_input": {"file_path": "src/lib.py"},
        "tool_response": {"content": read_content},
    })

    _append(transcript, {
        "type": "user",
        "sessionId": SUBAGENT_SESSION_ID,
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
                "filePath": str(target),
                "content": read_content,
                "numLines": read_content.count("\n"),
                "startLine": 1,
                "totalLines": read_content.count("\n"),
            },
        },
    })

    # 4. Assistant: Bash grep for helpers.
    grep_command = "grep -E '^def ' src/lib.py"
    grep_input = {"command": grep_command, "description": "Count helpers in src/lib.py."}
    _append(transcript, {
        "type": "assistant",
        "sessionId": SUBAGENT_SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_grep_uuid,
        "parentUuid": tr_read_uuid,
        "requestId": "req-otbox-ctx-subagent-child-2",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Confirming with a grep."},
                {
                    "type": "tool_use",
                    "id": TOOL_GREP_ID,
                    "name": "Bash",
                    "input": grep_input,
                },
            ],
            "usage": {"input_tokens": 60, "output_tokens": 30},
        },
    })

    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SUBAGENT_SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_use_id": TOOL_GREP_ID,
        "tool_input": grep_input,
    })

    grep_stdout = "def helper_one(x: int) -> int:\ndef helper_two(x: int) -> int:\n"

    _invoke_hook(on_tool_use.main, {
        "session_id": SUBAGENT_SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_use_id": TOOL_GREP_ID,
        "tool_input": grep_input,
        "tool_response": {"stdout": grep_stdout, "stderr": "", "interrupted": False},
    })

    _append(transcript, {
        "type": "user",
        "sessionId": SUBAGENT_SESSION_ID,
        "timestamp": _now(),
        "uuid": tr_grep_uuid,
        "parentUuid": asst_grep_uuid,
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": TOOL_GREP_ID,
                "content": grep_stdout,
            }],
        },
        "toolUseResult": {
            "stdout": grep_stdout,
            "stderr": "",
            "interrupted": False,
            "isImage": False,
        },
    })

    # 5. Subagent close — the text the parent will pull back as its
    #    Task tool_result content.
    summary_text = (
        "src/lib.py defines 2 top-level helpers: helper_one and helper_two. "
        "Neither is exported from a public __all__."
    )
    _append(transcript, {
        "type": "assistant",
        "sessionId": SUBAGENT_SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_close_uuid,
        "parentUuid": tr_grep_uuid,
        "requestId": "req-otbox-ctx-subagent-child-3",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": summary_text,
            "usage": {"input_tokens": 100, "output_tokens": 36},
        },
    })

    _append(transcript, {
        "type": "result",
        "subtype": "success",
        "sessionId": SUBAGENT_SESSION_ID,
        "timestamp": _now(),
        "duration_ms": 5000,
        "num_turns": 2,
        "stop_reason": "stop",
        "total_cost_usd": 0.0,
    })

    return {
        "summary_text": summary_text,
        "session_id": SUBAGENT_SESSION_ID,
        "leaf_uuid": asst_close_uuid,
    }
