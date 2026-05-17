"""Deterministic Claude Code session module — ``context-tree-compacted``.

Plan 077 fixture for ``context_compaction_observed`` event capture and
the lossy diff between pre-compaction message history and post-compaction
summary. The transcript runs a long enough opening (15+ messages across
five user turns) that a ``/compact`` boundary fires; the JSONL records
both the original pre-compaction history (the Context Tree parser
reads this to build the ``pre`` ``messages`` layer with ``is_summary:
false``) AND the synthetic post-compaction summary line (which becomes
the ``post`` ``messages`` layer with ``is_summary: true``). Two more
user turns continue past the compaction.

Layout:

    sys.init
        |
    five user turns ((prompt -> Read -> Edit -> close) each)
        |
    system.compact_boundary  (trigger='manual', pre_tokens=...)
        |
    user.isCompactSummary=true  (synthetic summary line Claude Code writes
                                  immediately after the boundary; the
                                  Context Tree parser folds this into the
                                  post-compaction messages layer)
        |
    two more user turns (prompt -> Read -> Edit -> close)
        |
    result

The Context Tree parser must:
  * Recognise the compact_boundary record and emit one
    context_compaction_observed event.
  * Hash the pre-compaction `messages` layer over the full message span
    BEFORE the boundary (is_summary: false).
  * Hash the post-compaction `messages` layer over just the summary
    record + post-boundary turns (is_summary: true, summary_of_span set).
  * Walk the lossy diff: which message uuids existed pre-compaction
    that do NOT survive into the post-compaction summary.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SESSION_ID = "sess-otbox-context-tree-compacted"
MODEL = "claude-sonnet-4-5"
HARNESS_VERSION = "otbox-fake-1.0"

_PRE_COMPACT_TURNS = (
    ("a", "alpha", "average over a window"),
    ("b", "beta",  "rolling minimum over a window"),
    ("c", "gamma", "rolling maximum over a window"),
    ("d", "delta", "exponentially-weighted average"),
    ("e", "epsilon", "running standard deviation"),
)

_POST_COMPACT_TURNS = (
    ("f", "zeta", "rolling sum over a window"),
    ("g", "eta",  "rolling product over a window"),
)

_COMPACT_SUMMARY_TEXT = (
    "Conversation so far: the user asked for five rolling-statistic helpers "
    "in src/stats.py (alpha=mean, beta=min, gamma=max, delta=ewma, "
    "epsilon=stdev). All five have been added and tests pass. The next "
    "request is likely another rolling statistic."
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


def _helper_source(slug: str, helper: str, summary: str) -> str:
    return (
        f"def {helper}(values: list[float], window: int) -> list[float]:\n"
        f"    # {summary}\n"
        f"    return [0.0 for _ in values]  # placeholder for slug={slug}\n"
    )


def run(project: Path, transcript: Path) -> None:
    src_dir = project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    stats_path = src_dir / "stats.py"
    stats_path.write_text("", encoding="utf-8")

    prior_cwd = Path.cwd()
    os.chdir(project)
    try:
        _run_session(project, stats_path, transcript)
    finally:
        os.chdir(prior_cwd)


def _emit_turn(
    *,
    on_pre_tool_use,
    on_tool_use,
    project: Path,
    target: Path,
    transcript: Path,
    prev_uuid: str,
    turn_index: int,
    slug: str,
    helper: str,
    summary: str,
    section: str,
) -> str:
    """Emit one (prompt -> Read -> Edit -> close) turn and return the new tail uuid."""
    rel_target = "src/stats.py"
    suffix = f"{section}-{turn_index}-{slug}"
    tool_read_id = f"toolu_otbox_ctx_compacted_read_{suffix}"
    tool_edit_id = f"toolu_otbox_ctx_compacted_edit_{suffix}"

    user_uuid = f"user-otbox-ctx-compacted-{suffix}-prompt"
    asst_read_uuid = f"asst-otbox-ctx-compacted-{suffix}-read"
    tr_read_uuid = f"user-otbox-ctx-compacted-{suffix}-read-result"
    asst_edit_uuid = f"asst-otbox-ctx-compacted-{suffix}-edit"
    tr_edit_uuid = f"user-otbox-ctx-compacted-{suffix}-edit-result"
    asst_close_uuid = f"asst-otbox-ctx-compacted-{suffix}-close"

    # Per-turn source delta: append the new helper to the file.
    pre_content = target.read_text(encoding="utf-8")
    new_helper = _helper_source(slug, helper, summary)
    post_content = pre_content + ("\n" if pre_content and not pre_content.endswith("\n") else "") + new_helper

    # User prompt.
    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": user_uuid,
        "parentUuid": prev_uuid,
        "message": {
            "role": "user",
            "content": (
                f"Please add a `{helper}({summary})` helper to {rel_target}."
            ),
        },
    })

    # Assistant Read.
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_read_uuid,
        "parentUuid": user_uuid,
        "requestId": f"req-otbox-ctx-compacted-{suffix}-read",
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
            "usage": {"input_tokens": 80, "output_tokens": 30},
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

    _invoke_hook(on_tool_use.main, {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": tool_read_id,
        "tool_input": {"file_path": rel_target},
        "tool_response": {"content": pre_content},
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
                "content": pre_content,
            }],
        },
        "toolUseResult": {
            "file": {
                "filePath": str(target),
                "content": pre_content,
                "numLines": pre_content.count("\n"),
                "startLine": 1,
                "totalLines": pre_content.count("\n"),
            },
        },
    })

    # Assistant Edit.
    edit_tool_input = {
        "file_path": rel_target,
        "old_string": pre_content,
        "new_string": post_content,
    }
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_edit_uuid,
        "parentUuid": tr_read_uuid,
        "requestId": f"req-otbox-ctx-compacted-{suffix}-edit",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {"type": "text", "text": f"Adding {helper}()."},
                {
                    "type": "tool_use",
                    "id": tool_edit_id,
                    "name": "Edit",
                    "input": edit_tool_input,
                },
            ],
            "usage": {"input_tokens": 120, "output_tokens": 80},
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

    target.write_text(post_content, encoding="utf-8")

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
            "oldString": pre_content,
            "newString": post_content,
            "originalFile": pre_content,
            "userModified": False,
            "replaceAll": False,
            "structuredPatch": [],
        },
    })

    # Per-turn close.
    _append(transcript, {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": asst_close_uuid,
        "parentUuid": tr_edit_uuid,
        "requestId": f"req-otbox-ctx-compacted-{suffix}-close",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": f"Added {helper}() to {rel_target}.",
            "usage": {"input_tokens": 40, "output_tokens": 12},
        },
    })

    return asst_close_uuid


def _run_session(project: Path, target: Path, transcript: Path) -> None:
    from opentraces.capture.claude_code.hooks import on_pre_tool_use, on_tool_use

    sys_uuid = "sys-otbox-ctx-compacted-init"

    # 1. System init.
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

    prev_uuid = sys_uuid

    # 2. Pre-compaction turns.
    for turn_index, (slug, helper, summary) in enumerate(_PRE_COMPACT_TURNS, start=1):
        prev_uuid = _emit_turn(
            on_pre_tool_use=on_pre_tool_use,
            on_tool_use=on_tool_use,
            project=project,
            target=target,
            transcript=transcript,
            prev_uuid=prev_uuid,
            turn_index=turn_index,
            slug=slug,
            helper=helper,
            summary=summary,
            section="pre",
        )

    # 3. The /compact boundary itself. The Claude Code parser keys off
    #    subtype=='compact_boundary'; compact_metadata.trigger names the
    #    invocation source (manual = user typed /compact, auto = context
    #    window pressure). pre_tokens is the approximate pre-boundary
    #    token total; opaque to opentraces today, structural for Context
    #    Tree's lossy_diff layer.
    compact_uuid = "sys-otbox-ctx-compacted-boundary"
    _append(transcript, {
        "type": "system",
        "subtype": "compact_boundary",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": compact_uuid,
        "parentUuid": prev_uuid,
        "compact_metadata": {
            "trigger": "manual",
            "pre_message_count": 5 + 5 * 5,  # init + 5 turns * 5 records/turn
        },
        "pre_tokens": 12000,
    })

    # 4. Synthetic isCompactSummary line — Claude Code writes one of
    #    these immediately after every compact_boundary so the resumed
    #    agent sees a concise recap in place of the dropped history.
    #    The mainline parser skips this record (it would pollute steps),
    #    but the Context Tree parser folds it into the post-compaction
    #    `messages` layer as the summary substitute (is_summary: true).
    summary_uuid = "user-otbox-ctx-compacted-summary"
    _append(transcript, {
        "type": "user",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "uuid": summary_uuid,
        "parentUuid": compact_uuid,
        "isCompactSummary": True,
        "message": {
            "role": "user",
            "content": _COMPACT_SUMMARY_TEXT,
        },
    })

    prev_uuid = summary_uuid

    # 5. Post-compaction turns.
    for turn_index, (slug, helper, summary) in enumerate(_POST_COMPACT_TURNS, start=1):
        prev_uuid = _emit_turn(
            on_pre_tool_use=on_pre_tool_use,
            on_tool_use=on_tool_use,
            project=project,
            target=target,
            transcript=transcript,
            prev_uuid=prev_uuid,
            turn_index=turn_index,
            slug=slug,
            helper=helper,
            summary=summary,
            section="post",
        )

    # 6. Result message.
    _append(transcript, {
        "type": "result",
        "subtype": "success",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "duration_ms": 45000,
        "num_turns": 7,
        "stop_reason": "stop",
        "total_cost_usd": 0.0,
    })
