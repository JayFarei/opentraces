"""Deterministic Claude Code session module — ``session-with-secrets``.

Same end-to-end shape as the ``simple-refactor`` corpus, but the
Edit tool call writes a ``src/config.py`` whose content carries
realistic-looking (but synthetic) credentials. The point is to
exercise the opentraces security pipeline (regex + entropy
detectors) end-to-end through a real captured session, so a
checkpoint downstream can prove that secrets either get redacted
out of the on-disk trace JSONL or — at minimum — get fingerprinted
under ``metadata.security``.

The simulated agent flow:

    user prompt: "add config.py with the API keys we discussed"
        -> assistant turn 1: Read tool_use against src/app.py
        -> assistant turn 2: Edit tool_use that creates src/config.py
        -> assistant turn 3: confirmation + Stop

Same harness contract as ``simple-refactor.session``: the
PreToolUse + PostToolUse hooks fire around the actual file
mutation so ``emit_step_window_events_from_record`` has a real
hook trail to anchor patches against. The Stop hook is
deliberately not invoked (the checkpoint runs the ingest +
commit + mature steps explicitly).
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SESSION_ID = "sess-otbox-session-with-secrets"
MODEL = "claude-sonnet-4-5"
HARNESS_VERSION = "otbox-fake-1.0"
TOOL_READ_ID = "toolu_otbox_secrets_read"
TOOL_EDIT_ID = "toolu_otbox_secrets_write"

# Existing src/app.py (pre-state, identical to simple-refactor) — gives
# the agent something to Read so the hook trail has a non-Edit step.
_APP_SOURCE = 'def greet(name: str) -> str:\n    return f"hello, {name}"\n'

# config.py post-state — synthetic, realistic-looking credentials whose
# shapes match the standard regex/entropy detector fingerprints. None
# of these are real secrets: the OpenAI/GitHub literals are well-known
# test shapes, and the INTERNAL_SECRET is a hand-rolled hex blob.
_CONFIG_BEFORE = ""
_CONFIG_AFTER = (
    '"""Application configuration — secrets pulled from env in real life.\n'
    'These literals are synthetic and only exist to drive the security pipeline.\n'
    '"""\n'
    '\n'
    'OPENAI_API_KEY = "sk-test-abc123def456ghi789jkl012mno345pqr678stu901"\n'
    'GITHUB_TOKEN = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"\n'
    'INTERNAL_SECRET = "0x" + "deadbeef" * 8\n'
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
    # Seed src/app.py so the Read tool_use has something to return.
    src_dir = project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    app_target = src_dir / "app.py"
    app_target.write_text(_APP_SOURCE, encoding="utf-8")
    # config.py must NOT exist pre-state — the Edit creates it.
    config_target = src_dir / "config.py"
    if config_target.exists():
        config_target.unlink()

    # Real Claude Code runs with cwd=project so its hooks resolve
    # relative ``file_path`` values. Mirror that here.
    prior_cwd = Path.cwd()
    os.chdir(project)
    try:
        _run_session(project, app_target, config_target, transcript)
    finally:
        os.chdir(prior_cwd)


def _run_session(
    project: Path,
    app_target: Path,
    config_target: Path,
    transcript: Path,
) -> None:
    # Import lazily — see simple-refactor.session for rationale.
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
        "uuid": "user-otbox-secrets-1",
        "message": {
            "role": "user",
            "content": (
                "Please drop a src/config.py with the API keys we discussed "
                "(OpenAI + GitHub + the internal secret). Commit when done."
            ),
        },
    })

    # 3. Assistant: Read tool_use (look at src/app.py to ground the agent).
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": "asst-otbox-secrets-1",
        "requestId": "req-otbox-secrets-1",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Reading src/app.py to confirm the project layout."},
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

    # 3a. Pre-Read hook.
    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": TOOL_READ_ID,
        "tool_input": {"file_path": "src/app.py"},
    })

    # 3b. Read content.
    read_content = app_target.read_text(encoding="utf-8")

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
        "uuid": "user-otbox-secrets-2",
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

    # 4. Assistant: Edit tool_use that creates src/config.py.
    #    Use Edit with empty old_string (the standard Claude Code shape
    #    for a brand-new file via Edit, matching what the security
    #    pipeline sees from real sessions).
    edit_tool_input = {
        "file_path": "src/config.py",
        "old_string": _CONFIG_BEFORE,
        "new_string": _CONFIG_AFTER,
    }
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": "asst-otbox-secrets-2",
        "requestId": "req-otbox-secrets-2",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Creating src/config.py with the requested secrets."},
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

    # 4a. Pre-Edit hook (file does not exist yet — that is fine, the hook
    #     records the pre-state, which may legitimately be empty).
    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": TOOL_EDIT_ID,
        "tool_input": edit_tool_input,
    })

    # 4b. Apply the actual file creation between the pre + post hooks.
    config_target.write_text(_CONFIG_AFTER, encoding="utf-8")

    # 4c. Post-Edit hook.
    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": TOOL_EDIT_ID,
        "tool_input": edit_tool_input,
        "tool_response": {"content": "The file src/config.py has been created."},
    })

    # 4d. tool_result back to assistant.
    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": "user-otbox-secrets-3",
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": TOOL_EDIT_ID,
                "content": "The file src/config.py has been created.",
            }],
        },
        "toolUseResult": {
            "oldString": _CONFIG_BEFORE,
            "newString": _CONFIG_AFTER,
            "originalFile": _CONFIG_BEFORE,
            "userModified": False,
            "replaceAll": False,
            "structuredPatch": [],
        },
    })

    # 5. Final assistant turn.
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": "asst-otbox-secrets-3",
        "requestId": "req-otbox-secrets-3",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": "Created src/config.py with the requested secrets.",
            "usage": {"input_tokens": 80, "output_tokens": 12},
        },
    })

    # 6. Result message — session-end marker.
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
