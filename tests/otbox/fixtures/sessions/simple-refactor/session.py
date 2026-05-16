"""Deterministic Claude Code session module — ``simple-refactor``.

The session walks the smallest end-to-end shape that still exercises
the full Trace Trails chain:

    user prompt: "add a farewell() helper to src/app.py"
        -> assistant turn 1: Read tool_use against src/app.py
        -> assistant turn 2: Edit tool_use that adds the helper
        -> assistant turn 3: confirmation + Stop

The harness imports ``run(project, transcript)`` and the module:

  * Appends every JSONL line (system / user / assistant / user-tool-
    result / result) to ``transcript`` in real Claude Code shape.
  * Invokes the opentraces PreToolUse / PostToolUse / Stop hooks with
    live payloads, so ``hook_pre_tool_use`` + ``hook_post_tool_use``
    show up in the parsed trace metadata (this is what
    ``emit_step_window_events_from_record`` requires to emit Trace
    Patch events into ``refs/opentraces/local/events/v1``).
  * Performs the deterministic file mutation between the pre + post
    hooks of the Edit tool call — the same ordering a real Claude
    session produces.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SESSION_ID = "sess-otbox-simple-refactor"
MODEL = "claude-sonnet-4-5"
HARNESS_VERSION = "otbox-fake-1.0"
TOOL_READ_ID = "toolu_otbox_simple_refactor_read"
TOOL_EDIT_ID = "toolu_otbox_simple_refactor_edit"

_SOURCE_BEFORE = 'def greet(name: str) -> str:\n    return f"hello, {name}"\n'
_SOURCE_AFTER = (
    'def greet(name: str) -> str:\n'
    '    return f"hello, {name}"\n'
    '\n'
    '\n'
    'def farewell(name: str) -> str:\n'
    '    return f"bye, {name}"\n'
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


def _trail_state(project: Path, tool_name: str, tool_input: dict) -> dict:
    """Best-effort trail snapshot — same primitive the real hooks use."""
    from opentraces.capture.claude_code.hooks._trails import trail_state

    return trail_state(str(project), tool_name, tool_input)


def run(project: Path, transcript: Path) -> None:
    # Make sure src/app.py starts in the canonical pre-state.
    src_dir = project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    target = src_dir / "app.py"
    target.write_text(_SOURCE_BEFORE, encoding="utf-8")

    # Real Claude Code runs with cwd=project so its hooks can open
    # relative ``file_path`` values (``src/app.py``). The harness
    # mirrors that: chdir into the project for the duration of the
    # session module so ``_handle_edit`` can read disk.
    prior_cwd = Path.cwd()
    os.chdir(project)
    try:
        _run_session(project, target, transcript)
    finally:
        os.chdir(prior_cwd)


def _run_session(project: Path, target: Path, transcript: Path) -> None:

    # opentraces capture/* lives on sys.path via the same `.testvenv`
    # the c-installed-source checkpoint builds. Import lazily so the
    # smoke pytest can exercise the JSONL-emission contract without
    # opentraces being importable in the harness invocation context.
    # We deliberately do NOT invoke the SessionEnd (Stop) hook here —
    # its `main()` fire-and-forgets a detached `_ingest-session`
    # subprocess that would race with the deterministic checkpoint
    # ordering (ingest -> commit -> mature). The checkpoint invokes
    # `_ingest-session` explicitly instead.
    from opentraces.capture.claude_code.hooks import on_pre_tool_use, on_tool_use

    # 1. System init line.
    _append(transcript, {
        "type": "system",
        "subtype": "init",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "cwd": str(project),
        "model": MODEL,
        "claude_code_version": HARNESS_VERSION,
        "tools": [
            {"name": "Read", "description": "Read a project file."},
            {"name": "Edit", "description": "Apply an exact-match edit."},
        ],
        "permissionMode": "default",
        "gitBranch": "main",
    })

    # 2. User prompt.
    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": "user-otbox-simple-refactor-1",
        "message": {
            "role": "user",
            "content": (
                "Please add a `farewell(name)` helper to src/app.py that "
                "mirrors greet(). Commit when done."
            ),
        },
    })

    # 3. Assistant: Read tool_use.
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": "asst-otbox-simple-refactor-1",
        "requestId": "req-otbox-simple-refactor-1",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Reading src/app.py to see greet()."},
                {
                    "type": "tool_use",
                    "id": TOOL_READ_ID,
                    "name": "Read",
                    "input": {"file_path": "src/app.py"},
                },
            ],
            "usage": {"input_tokens": 120, "output_tokens": 40},
        },
    })

    # 3a. Pre-Read hook — emits opentraces_hook/PreToolUse line.
    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": TOOL_READ_ID,
        "tool_input": {"file_path": "src/app.py"},
    })

    # 3b. (No file mutation for Read.) Read the content for the tool_result.
    read_content = target.read_text(encoding="utf-8")

    # 3c. Post-Read hook.
    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": TOOL_READ_ID,
        "tool_input": {"file_path": "src/app.py"},
        "tool_response": {"content": read_content},
    })

    # 3d. tool_result back to assistant.
    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": "user-otbox-simple-refactor-2",
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
                "filePath": str((project / "src" / "app.py")),
                "content": read_content,
                "numLines": read_content.count("\n"),
                "startLine": 1,
                "totalLines": read_content.count("\n"),
            },
        },
    })

    # 4. Assistant: Edit tool_use.
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": "asst-otbox-simple-refactor-2",
        "requestId": "req-otbox-simple-refactor-2",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Adding the farewell helper after greet()."},
                {
                    "type": "tool_use",
                    "id": TOOL_EDIT_ID,
                    "name": "Edit",
                    "input": {
                        "file_path": "src/app.py",
                        "old_string": _SOURCE_BEFORE,
                        "new_string": _SOURCE_AFTER,
                    },
                },
            ],
            "usage": {"input_tokens": 160, "output_tokens": 80},
        },
    })

    # 4a. Pre-Edit hook (file still pre-state).
    edit_tool_input = {
        "file_path": "src/app.py",
        "old_string": _SOURCE_BEFORE,
        "new_string": _SOURCE_AFTER,
    }
    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": TOOL_EDIT_ID,
        "tool_input": edit_tool_input,
    })

    # 4b. Apply the actual file mutation between the pre + post hooks —
    #     same ordering a real Claude session produces.
    target.write_text(_SOURCE_AFTER, encoding="utf-8")

    # 4c. Post-Edit hook (file now post-state; the hook reads disk and
    #     records the exact post-edit line range + content hash).
    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": TOOL_EDIT_ID,
        "tool_input": edit_tool_input,
        "tool_response": {"content": "The file src/app.py has been updated."},
    })

    # 4d. tool_result back to assistant.
    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": "user-otbox-simple-refactor-3",
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": TOOL_EDIT_ID,
                "content": "The file src/app.py has been updated.",
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

    # 5. Final assistant turn (close the conversation).
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": "asst-otbox-simple-refactor-3",
        "requestId": "req-otbox-simple-refactor-3",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": "Added the farewell helper.",
            "usage": {"input_tokens": 80, "output_tokens": 12},
        },
    })

    # 5a. (See module-level note: we skip the Stop hook on purpose.)

    # 6. Result message — session-end marker the parser uses to set
    #    timestamp_end / stop_reason.
    _append(transcript, {
        "type": "result",
        "subtype": "success",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "duration_ms": 7000,
        "num_turns": 3,
        "stop_reason": "stop",
        "total_cost_usd": 0.0,
    })
