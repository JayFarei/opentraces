"""Deterministic Claude Code session module — ``context-tree-rewound``.

Plan 077 fixture for ``branch_type: rewind_branch``. The transcript runs
linearly up to step N, then the user hits ``Esc Esc`` and rewinds the
conversation. From the Claude Code on-disk JSONL's perspective, the
rewind manifests as an **orphan branch**: there are records whose
``parentUuid`` points at an earlier record in the active chain, but
the active chain itself continued past that point along a different
``uuid``. Walking parents from the active leaf never visits the orphan.

Layout (linear active path on the left, orphan branch on the right):

    sys.init
        |
    user-1 (prompt)
        |
    asst-1 (Read)
        |
    user-2 (Read tool_result)            <-- rewind anchor
       / \\
      /   \\
  asst-2A  asst-2B (orphan: doomed Edit)
   |        |
  user-3    user-orphan (orphan tool_result)
   |
  asst-3 (Edit on the surviving path)
   |
  user-4 (Edit tool_result)
   |
  asst-4 (close)
   |
  result

The orphan branch (asst-2B / user-orphan) carries the doomed Edit attempt
the user rewound out of. Walking parents from ``asst-4`` never visits
asst-2B; walking parents from user-orphan reaches user-2 but never
re-enters the active continuation past it. This is the structural shape
the Context Tree parser must recognise as ``branch_type: rewind_branch``.

Note: the orphan branch's Edit is NOT applied to disk (the rewind
discarded it). Only the surviving Edit on the active path is applied.
The Pre/PostToolUse hooks fire ONLY for tools on the surviving path,
which matches how a real ``Esc Esc`` flow looks on disk: the rolled-back
turn's hook trail is still in the JSONL up to the point the user hit Esc,
but the orphan continuation has no hook history because the user never
let it execute.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SESSION_ID = "sess-otbox-context-tree-rewound"
MODEL = "claude-sonnet-4-5"
HARNESS_VERSION = "otbox-fake-1.0"
TOOL_READ_ID = "toolu_otbox_ctx_rewound_read"
TOOL_EDIT_SURVIVE_ID = "toolu_otbox_ctx_rewound_edit_survive"
TOOL_EDIT_ORPHAN_ID = "toolu_otbox_ctx_rewound_edit_orphan"

_SOURCE_BEFORE = (
    "def normalize(values: list[float]) -> list[float]:\n"
    "    return [v for v in values]\n"
)

# The surviving Edit (kept on the active path) — divides by max.
_SOURCE_AFTER_SURVIVING = (
    "def normalize(values: list[float]) -> list[float]:\n"
    "    if not values:\n"
    "        return []\n"
    "    cap = max(values)\n"
    "    return [v / cap for v in values]\n"
)

# The orphan Edit (rolled back via Esc Esc; NEVER written to disk) — divides
# by sum, which is the wrong semantics the user caught and reverted out of.
_SOURCE_AFTER_ORPHAN = (
    "def normalize(values: list[float]) -> list[float]:\n"
    "    total = sum(values)\n"
    "    return [v / total for v in values]\n"
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
    target = src_dir / "normalize.py"
    target.write_text(_SOURCE_BEFORE, encoding="utf-8")

    prior_cwd = Path.cwd()
    os.chdir(project)
    try:
        _run_session(project, target, transcript)
    finally:
        os.chdir(prior_cwd)


def _run_session(project: Path, target: Path, transcript: Path) -> None:
    from opentraces.capture.claude_code.hooks import on_pre_tool_use, on_tool_use

    sys_uuid = "sys-otbox-ctx-rewound-init"
    user_uuid = "user-otbox-ctx-rewound-1"
    asst_read_uuid = "asst-otbox-ctx-rewound-1"
    tr_read_uuid = "user-otbox-ctx-rewound-2"             # REWIND ANCHOR
    # Orphan branch (the doomed Edit the user rewound out of):
    asst_orphan_uuid = "asst-otbox-ctx-rewound-2B-orphan"
    tr_orphan_uuid = "user-otbox-ctx-rewound-orphan-result"
    # Surviving active continuation:
    asst_edit_uuid = "asst-otbox-ctx-rewound-2A"
    tr_edit_uuid = "user-otbox-ctx-rewound-3"
    asst_close_uuid = "asst-otbox-ctx-rewound-4"

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
                "Please update src/normalize.py so it scales each value by the "
                "maximum, not the sum. (We tried sum first and the magnitudes "
                "came out wrong.)"
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
        "requestId": "req-otbox-ctx-rewound-1",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Reading src/normalize.py."},
                {
                    "type": "tool_use",
                    "id": TOOL_READ_ID,
                    "name": "Read",
                    "input": {"file_path": "src/normalize.py"},
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
        "tool_input": {"file_path": "src/normalize.py"},
    })

    read_content = target.read_text(encoding="utf-8")

    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": TOOL_READ_ID,
        "tool_input": {"file_path": "src/normalize.py"},
        "tool_response": {"content": read_content},
    })

    # 4. tool_result back from Read — THIS IS THE REWIND ANCHOR.
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
                "filePath": str(target),
                "content": read_content,
                "numLines": read_content.count("\n"),
                "startLine": 1,
                "totalLines": read_content.count("\n"),
            },
        },
    })

    # 5. ORPHAN BRANCH — the doomed Edit the user rewound out of.
    #    Same parentUuid as the surviving asst-2A (tr_read_uuid). Both
    #    children of tr_read_uuid exist on disk; the active path takes
    #    asst-2A; asst-2B and its tool_result are orphans.
    orphan_edit_input = {
        "file_path": "src/normalize.py",
        "old_string": _SOURCE_BEFORE,
        "new_string": _SOURCE_AFTER_ORPHAN,
    }
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_orphan_uuid,
        "parentUuid": tr_read_uuid,
        "requestId": "req-otbox-ctx-rewound-2B-orphan",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Dividing each value by the sum."},
                {
                    "type": "tool_use",
                    "id": TOOL_EDIT_ORPHAN_ID,
                    "name": "Edit",
                    "input": orphan_edit_input,
                },
            ],
            "usage": {"input_tokens": 160, "output_tokens": 80},
        },
    })

    # Orphan tool_result. Note: NO Pre/PostToolUse hooks fire for the
    # orphan branch. The user hit Esc before the Edit tool executed, so
    # the disk never saw a hook trail for this Edit; the orphan tool_result
    # is the model's reply text only.
    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": tr_orphan_uuid,
        "parentUuid": asst_orphan_uuid,
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": TOOL_EDIT_ORPHAN_ID,
                "content": "[interrupted by user]",
                "is_error": True,
            }],
        },
    })

    # 6. SURVIVING active continuation — same parentUuid as the orphan
    #    (tr_read_uuid). Walking parents from asst_close_uuid reaches
    #    tr_read_uuid via asst-2A, never via asst-2B.
    edit_tool_input = {
        "file_path": "src/normalize.py",
        "old_string": _SOURCE_BEFORE,
        "new_string": _SOURCE_AFTER_SURVIVING,
    }
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_edit_uuid,
        "parentUuid": tr_read_uuid,
        "requestId": "req-otbox-ctx-rewound-2A",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": "Right, dividing by max instead."},
                {
                    "type": "tool_use",
                    "id": TOOL_EDIT_SURVIVE_ID,
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
        "tool_use_id": TOOL_EDIT_SURVIVE_ID,
        "tool_input": edit_tool_input,
    })

    # Apply the surviving Edit only. The orphan Edit was never executed.
    target.write_text(_SOURCE_AFTER_SURVIVING, encoding="utf-8")

    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": TOOL_EDIT_SURVIVE_ID,
        "tool_input": edit_tool_input,
        "tool_response": {"content": "The file src/normalize.py has been updated."},
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
                "tool_use_id": TOOL_EDIT_SURVIVE_ID,
                "content": "The file src/normalize.py has been updated.",
            }],
        },
        "toolUseResult": {
            "oldString": _SOURCE_BEFORE,
            "newString": _SOURCE_AFTER_SURVIVING,
            "originalFile": _SOURCE_BEFORE,
            "userModified": False,
            "replaceAll": False,
            "structuredPatch": [],
        },
    })

    # 7. Final assistant close on the surviving active path.
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_close_uuid,
        "parentUuid": tr_edit_uuid,
        "requestId": "req-otbox-ctx-rewound-4",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": "Updated normalize() to divide by max instead of sum.",
            "usage": {"input_tokens": 80, "output_tokens": 12},
        },
    })

    # 8. Result message — session-end marker.
    _append(transcript, {
        "type": "result",
        "subtype": "success",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "duration_ms": 11000,
        "num_turns": 3,
        "stop_reason": "stop",
        "total_cost_usd": 0.0,
    })
