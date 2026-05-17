"""Deterministic Claude Code session module — ``context-tree-multi-turn``.

Plan 077 fixture. Three user turns, mixed tool use across turns, three
Edits in three separate commits worth of operations (one Edit per turn,
each on a distinct file). Used to validate Context Tree layer dedup
(repeated CLAUDE.md / tool registry across turns must hash identically),
message-list growth (one ``messages`` layer per active-path node), and
temporal anchor precision (each Edit's node carries a ``trail_anchor_hint``
that resolves to that Edit's own commit, not the next or previous).

    user turn 1: "add helper alpha() to src/mod_a.py"
        -> assistant: Read -> Edit -> assistant close (turn 1)
    user turn 2: "now add beta() to src/mod_b.py too"
        -> assistant: Read -> Edit -> assistant close (turn 2)
    user turn 3: "and finally gamma() to src/mod_c.py"
        -> assistant: Read -> Edit -> Bash (sanity check) -> assistant close (turn 3)

The chain stays strictly linear (no branching) but the conversation runs
long enough that layer dedup and per-turn projection assertions have
something to bite on. parentUuid links every record to its predecessor.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SESSION_ID = "sess-otbox-context-tree-multi-turn"
MODEL = "claude-sonnet-4-5"
HARNESS_VERSION = "otbox-fake-1.0"

# Per-turn source pairs. Each module starts empty and grows one helper.
_MODULES = (
    ("a", "alpha", "calculate the alpha rollup"),
    ("b", "beta", "calculate the beta rollup"),
    ("c", "gamma", "calculate the gamma rollup"),
)

_BASH_STDOUT = (
    "src/mod_a.py: alpha\n"
    "src/mod_b.py: beta\n"
    "src/mod_c.py: gamma\n"
    "All three helpers present.\n"
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


def _module_source(helper: str, summary: str) -> str:
    return (
        f"def {helper}(values: list[float]) -> float:\n"
        f"    # {summary}\n"
        f"    return sum(values) / len(values) if values else 0.0\n"
    )


def run(project: Path, transcript: Path) -> None:
    src_dir = project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    # Seed all three module files empty so the per-turn Read tool_use has
    # something definite to return.
    for slug, _helper, _summary in _MODULES:
        (src_dir / f"mod_{slug}.py").write_text("", encoding="utf-8")

    prior_cwd = Path.cwd()
    os.chdir(project)
    try:
        _run_session(project, transcript)
    finally:
        os.chdir(prior_cwd)


def _run_session(project: Path, transcript: Path) -> None:
    from opentraces.capture.claude_code.hooks import on_pre_tool_use, on_tool_use

    sys_uuid = "sys-otbox-ctx-multi-init"

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

    prev_uuid = sys_uuid

    for turn_index, (slug, helper, summary) in enumerate(_MODULES, start=1):
        rel_target = f"src/mod_{slug}.py"
        target = project / rel_target
        source_before = ""
        source_after = _module_source(helper, summary)

        suffix = f"multi-turn-{turn_index}"
        tool_read_id = f"toolu_otbox_ctx_{suffix}_read"
        tool_edit_id = f"toolu_otbox_ctx_{suffix}_edit"

        user_uuid = f"user-otbox-ctx-{suffix}-prompt"
        asst_read_uuid = f"asst-otbox-ctx-{suffix}-read"
        tr_read_uuid = f"user-otbox-ctx-{suffix}-read-result"
        asst_edit_uuid = f"asst-otbox-ctx-{suffix}-edit"
        tr_edit_uuid = f"user-otbox-ctx-{suffix}-edit-result"
        asst_close_uuid = f"asst-otbox-ctx-{suffix}-close"

        # 2.X User prompt for this turn.
        _append(transcript, {
            "type": "user",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "uuid": user_uuid,
            "parentUuid": prev_uuid,
            "message": {
                "role": "user",
                "content": (
                    f"Please add a `{helper}()` helper to {rel_target} that "
                    f"computes the mean over a list. Commit once it's in."
                ),
            },
        })

        # 3.X Assistant: Read tool_use.
        _append(transcript, {
            "type": "assistant",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "uuid": asst_read_uuid,
            "parentUuid": user_uuid,
            "requestId": f"req-otbox-ctx-{suffix}-read",
            "message": {
                "role": "assistant",
                "model": MODEL,
                "content": [
                    {"type": "text", "text": f"Reading {rel_target} before adding {helper}()."},
                    {
                        "type": "tool_use",
                        "id": tool_read_id,
                        "name": "Read",
                        "input": {"file_path": rel_target},
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
            "tool_use_id": tool_read_id,
            "tool_input": {"file_path": rel_target},
        })

        read_content = target.read_text(encoding="utf-8")

        _invoke_hook(on_tool_use.main, {
            "session_id": SESSION_ID,
            "transcript_path": str(transcript),
            "cwd": str(project),
            "tool_name": "Read",
            "tool_use_id": tool_read_id,
            "tool_input": {"file_path": rel_target},
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
                    "tool_use_id": tool_read_id,
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

        # 4.X Assistant: Edit tool_use.
        edit_tool_input = {
            "file_path": rel_target,
            "old_string": source_before,
            "new_string": source_after,
        }
        _append(transcript, {
            "type": "assistant",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "uuid": asst_edit_uuid,
            "parentUuid": tr_read_uuid,
            "requestId": f"req-otbox-ctx-{suffix}-edit",
            "message": {
                "role": "assistant",
                "model": MODEL,
                "content": [
                    {"type": "text", "text": f"Adding {helper}() to {rel_target}."},
                    {
                        "type": "tool_use",
                        "id": tool_edit_id,
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
            "tool_use_id": tool_edit_id,
            "tool_input": edit_tool_input,
        })

        target.write_text(source_after, encoding="utf-8")

        _invoke_hook(on_tool_use.main, {
            "session_id": SESSION_ID,
            "transcript_path": str(transcript),
            "cwd": str(project),
            "tool_name": "Edit",
            "tool_use_id": tool_edit_id,
            "tool_input": edit_tool_input,
            "tool_response": {"content": f"The file {rel_target} has been updated."},
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
                    "tool_use_id": tool_edit_id,
                    "content": f"The file {rel_target} has been updated.",
                }],
            },
            "toolUseResult": {
                "oldString": source_before,
                "newString": source_after,
                "originalFile": source_before,
                "userModified": False,
                "replaceAll": False,
                "structuredPatch": [],
            },
        })

        # 5.X Assistant close (per-turn).
        _append(transcript, {
            "type": "assistant",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "uuid": asst_close_uuid,
            "parentUuid": tr_edit_uuid,
            "requestId": f"req-otbox-ctx-{suffix}-close",
            "message": {
                "role": "assistant",
                "model": MODEL,
                "content": f"Added {helper}() to {rel_target}. Ready for the next turn.",
                "usage": {"input_tokens": 60, "output_tokens": 18},
            },
        })

        prev_uuid = asst_close_uuid

    # 6. Final-turn Bash sanity check (extra mixed-tool diversity on turn 3).
    bash_uuid = "asst-otbox-ctx-multi-turn-bash"
    bash_result_uuid = "user-otbox-ctx-multi-turn-bash-result"
    final_uuid = "asst-otbox-ctx-multi-turn-final"
    tool_bash_id = "toolu_otbox_ctx_multi_turn_bash"

    bash_command = "grep -E 'def (alpha|beta|gamma)' src/mod_*.py"
    bash_tool_input = {"command": bash_command, "description": "Sanity check all three helpers."}

    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": bash_uuid,
        "parentUuid": prev_uuid,
        "requestId": "req-otbox-ctx-multi-turn-bash",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "One sanity grep to confirm all three helpers landed."},
                {
                    "type": "tool_use",
                    "id": tool_bash_id,
                    "name": "Bash",
                    "input": bash_tool_input,
                },
            ],
            "usage": {"input_tokens": 60, "output_tokens": 30},
        },
    })

    _invoke_hook(on_pre_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_use_id": tool_bash_id,
        "tool_input": bash_tool_input,
    })

    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_use_id": tool_bash_id,
        "tool_input": bash_tool_input,
        "tool_response": {"stdout": _BASH_STDOUT, "stderr": "", "interrupted": False},
    })

    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": bash_result_uuid,
        "parentUuid": bash_uuid,
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_bash_id,
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

    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": final_uuid,
        "parentUuid": bash_result_uuid,
        "requestId": "req-otbox-ctx-multi-turn-final",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": "alpha, beta, gamma all present across the three modules.",
            "usage": {"input_tokens": 60, "output_tokens": 14},
        },
    })

    # 7. Result message — session-end marker.
    _append(transcript, {
        "type": "result",
        "subtype": "success",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "duration_ms": 22000,
        "num_turns": 3,
        "stop_reason": "stop",
        "total_cost_usd": 0.0,
    })
