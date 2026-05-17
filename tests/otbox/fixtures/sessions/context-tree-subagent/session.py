"""Deterministic Claude Code session module — ``context-tree-subagent``.

Plan 077 fixture for cross-session linkage. The parent session spawns a
sub-agent via a Task tool_use; the sub-agent runs in a separate JSONL
the Claude Code parser discovers at
``<parent-transcript-stem>/subagents/<id>.jsonl`` (paired with a sibling
``<id>.meta.json`` via the parent Task's ``description``). After the
sub-agent terminates the parent appends a Task tool_result containing
the sub-agent's summary, then closes.

    parent.system.init
        |
    parent.user-1 (prompt)
        |
    parent.assistant-1 (Task tool_use, description="...")
        |
    [child JSONL runs in subagents/<id>.jsonl]
        |
    parent.user-2 (Task tool_result, content=child.summary_text)
        |
    parent.assistant-2 (close)
        |
    parent.result

The child JSONL is authored by the sibling module
``subagent_session.py`` so each side stays self-contained and testable.
The parent loads it via importlib so the hyphenated fixture directory
does not need to be a Python package.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SESSION_ID = "sess-otbox-context-tree-subagent"
MODEL = "claude-sonnet-4-5"
HARNESS_VERSION = "otbox-fake-1.0"
TASK_TOOL_ID = "toolu_otbox_ctx_subagent_task"

# Stable subagent JSONL stem inside <transcript-stem>/subagents/
_SUBAGENT_JSONL_STEM = "subagent-001-helper-survey"

_TASK_DESCRIPTION = "Survey src/lib.py and report how many top-level helpers it defines."


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


def _load_subagent_module():
    """Import the sibling ``subagent_session.py`` by file path.

    The fixture directory has a hyphen in its name so it is not importable
    as a Python package; spec_from_file_location keeps the loader contract
    matching the fake-claude harness.
    """
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "context_tree_subagent_child",
        here / "subagent_session.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load subagent_session.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(project: Path, transcript: Path) -> None:
    src_dir = project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    lib_path = src_dir / "lib.py"
    lib_path.write_text(
        "def helper_one(x: int) -> int:\n"
        "    return x + 1\n"
        "\n"
        "def helper_two(x: int) -> int:\n"
        "    return x * 2\n",
        encoding="utf-8",
    )

    prior_cwd = Path.cwd()
    os.chdir(project)
    try:
        _run_session(project, transcript)
    finally:
        os.chdir(prior_cwd)


def _run_session(project: Path, transcript: Path) -> None:
    from opentraces.capture.claude_code.hooks import on_pre_tool_use, on_tool_use

    sys_uuid = "sys-otbox-ctx-subagent-init"
    user_uuid = "user-otbox-ctx-subagent-1"
    asst_task_uuid = "asst-otbox-ctx-subagent-task"
    tr_task_uuid = "user-otbox-ctx-subagent-task-result"
    asst_close_uuid = "asst-otbox-ctx-subagent-close"

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
            {"name": "Task", "description": "Launch a sub-agent."},
        ],
        "permissionMode": "default",
        "gitBranch": "main",
    })

    # 2. User prompt asking for a delegation.
    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": user_uuid,
        "parentUuid": sys_uuid,
        "message": {
            "role": "user",
            "content": (
                "Please use a sub-agent to survey src/lib.py and report how "
                "many top-level helpers it defines. Just summarise the count."
            ),
        },
    })

    # 3. Assistant: Task tool_use — invokes the sub-agent.
    task_input = {
        "description": _TASK_DESCRIPTION,
        "prompt": (
            "Read src/lib.py, list every top-level function, and report the "
            "count plus the names."
        ),
        "subagent_type": "general-purpose",
    }
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_task_uuid,
        "parentUuid": user_uuid,
        "requestId": "req-otbox-ctx-subagent-task",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Delegating the survey to a sub-agent."},
                {
                    "type": "tool_use",
                    "id": TASK_TOOL_ID,
                    "name": "Task",
                    "input": task_input,
                },
            ],
            "usage": {"input_tokens": 120, "output_tokens": 60},
        },
    })

    # Pre-Task hook fires for the parent's tool_use.
    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Task",
        "tool_use_id": TASK_TOOL_ID,
        "tool_input": task_input,
    })

    # 4. Run the sub-agent in its own JSONL under
    #    <transcript-stem>/subagents/<id>.jsonl, with a sibling
    #    <id>.meta.json the parser uses to match the parent Task's
    #    description back to the child file.
    subagents_dir = transcript.with_suffix("") / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    child_transcript = subagents_dir / f"{_SUBAGENT_JSONL_STEM}.jsonl"

    subagent_mod = _load_subagent_module()
    subagent_summary = subagent_mod.run(project, child_transcript, _TASK_DESCRIPTION)

    # Post-Task hook with the sub-agent's summary as tool_response.
    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Task",
        "tool_use_id": TASK_TOOL_ID,
        "tool_input": task_input,
        "tool_response": {"content": subagent_summary["summary_text"]},
    })

    # 5. Task tool_result back to parent — carries the sub-agent's summary.
    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": tr_task_uuid,
        "parentUuid": asst_task_uuid,
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": TASK_TOOL_ID,
                "content": subagent_summary["summary_text"],
            }],
        },
        "toolUseResult": {
            "subagent_session_id": subagent_summary["session_id"],
            "subagent_leaf_uuid": subagent_summary["leaf_uuid"],
            "summary": subagent_summary["summary_text"],
        },
    })

    # 6. Parent closes.
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_close_uuid,
        "parentUuid": tr_task_uuid,
        "requestId": "req-otbox-ctx-subagent-close",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": (
                "The sub-agent reports two top-level helpers in src/lib.py "
                "(helper_one and helper_two)."
            ),
            "usage": {"input_tokens": 80, "output_tokens": 24},
        },
    })

    # 7. Result message.
    _append(transcript, {
        "type": "result",
        "subtype": "success",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "duration_ms": 12000,
        "num_turns": 2,
        "stop_reason": "stop",
        "total_cost_usd": 0.0,
    })
