"""Deterministic Claude Code session module — ``multi-skill-session``.

Used by plan 068 M68-3 to build the ``c-captured-multi-skill``
checkpoint: three captured sessions across three commits, alternating
two synthetic slash-command skill invocations.

This module is parameterised by an integer ``index`` so the same corpus
can be run multiple times against the same project, producing distinct
sessions (different ``session_id``, different target file, different
skill invoked):

  * Pass the index via the environment variable
    ``OTBOX_FAKE_SESSION_INDEX`` (the fake-claude harness leaves the
    parent's env intact; this avoids touching the harness contract).
  * ``run(project, transcript)`` reads ``OTBOX_FAKE_SESSION_INDEX``
    (default ``0``) and dispatches to ``run_indexed``.

The skill invocation is modelled the way real Claude Code records
slash commands on disk: two consecutive user-role messages where the
first carries ``<command-name>...</command-name>`` + ``<command-args>``
+ ``<command-message>``, and the second carries the injected skill
body headed by ``Base directory for this skill: .../.claude/skills/<n>``.
That is the pairing ``_extract_skill_invocations`` in
``opentraces.capture.claude_code.parse`` requires to populate
``metadata.skill_invocations``.

Each run:

  * Appends every JSONL line (system / user / assistant / user-tool-
    result / result) to ``transcript`` in real Claude Code shape.
  * Invokes the opentraces PreToolUse / PostToolUse hooks with live
    payloads, so ``hook_pre_tool_use`` + ``hook_post_tool_use`` show up
    in the parsed trace metadata (load-bearing for Trace Patch events).
  * Performs the deterministic file mutation between the pre + post
    hooks of the Edit tool call.

Same Stop-hook policy as ``simple-refactor``: the harness deliberately
does NOT fire SessionEnd, because its real ``main()`` fire-and-forgets a
detached ``_ingest-session`` subprocess that would race the deterministic
checkpoint ordering (ingest -> commit -> mature). The checkpoint invokes
``_ingest-session`` explicitly per session instead.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

MODEL = "claude-sonnet-4-5"
HARNESS_VERSION = "otbox-fake-1.0"

# The two skills alternated by index % 2. Names are deliberately bland
# slugs so any future skill-classifier work does not get fooled into
# treating them as real skill packages.
_SKILLS = ("skill-alpha", "skill-beta")


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


def _resolve_index() -> int:
    raw = os.environ.get("OTBOX_FAKE_SESSION_INDEX", "0")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def run(project: Path, transcript: Path) -> None:
    """Entry-point used by the fake-claude harness.

    The harness only knows about ``run(project, transcript)``; the index
    is threaded in via the ``OTBOX_FAKE_SESSION_INDEX`` env var so the
    harness contract does not need to change.
    """
    index = _resolve_index()
    run_indexed(project, transcript, index)


def run_indexed(project: Path, transcript: Path, index: int) -> None:
    """Run the multi-skill session for a specific iteration.

    ``index`` selects:
      * the skill name (``skill-alpha`` vs ``skill-beta``, alternating)
      * the per-iteration target file (``src/feature_<index>.py``)
      * the session_id suffix (``sess-otbox-multi-skill-<index>``)
    """
    src_dir = project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    skill_name = _SKILLS[index % len(_SKILLS)]
    rel_target = f"src/feature_{index}.py"
    target = project / rel_target
    # Iteration creates a NEW file (rather than mutating an existing
    # one) so the per-iteration commits are distinct and the watcher /
    # post-commit chain has one fresh file to attribute per pass.
    source_before = ""
    source_after = (
        f"def feature_{index}() -> str:\n"
        f"    # iteration {index}, invoked via {skill_name}\n"
        f'    return "feature-{index}"\n'
    )
    target.write_text(source_before, encoding="utf-8")

    prior_cwd = Path.cwd()
    os.chdir(project)
    try:
        _run_session(
            project=project,
            target=target,
            rel_target=rel_target,
            transcript=transcript,
            index=index,
            skill_name=skill_name,
            source_before=source_before,
            source_after=source_after,
        )
    finally:
        os.chdir(prior_cwd)


def _run_session(
    *,
    project: Path,
    target: Path,
    rel_target: str,
    transcript: Path,
    index: int,
    skill_name: str,
    source_before: str,
    source_after: str,
) -> None:
    # See module docstring re: the deliberate Stop-hook omission.
    from opentraces.capture.claude_code.hooks import on_pre_tool_use, on_tool_use

    session_id = f"sess-otbox-multi-skill-{index}"
    tool_read_id = f"toolu_otbox_multi_skill_read_{index}"
    tool_edit_id = f"toolu_otbox_multi_skill_edit_{index}"
    suffix = f"multi-skill-{index}"

    # 1. System init line.
    _append(transcript, {
        "type": "system",
        "subtype": "init",
        "sessionId": session_id,
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

    # 2a. User prompt — slash-command wrapper. This is the message the
    #     parser pairs with the body (next message) to mint a
    #     skill_invocations entry. Format matches the real Claude
    #     transcript shape parsed by ``_extract_skill_invocations`` in
    #     ``opentraces.capture.claude_code.parse``.
    _append(transcript, {
        "type": "user",
        "sessionId": session_id,
        "timestamp": _now(),
        "uuid": f"user-otbox-{suffix}-cmd",
        "message": {
            "role": "user",
            "content": (
                f"<command-name>/{skill_name}</command-name>\n"
                f"<command-args>add feature {index}</command-args>\n"
                f"<command-message>{skill_name} add feature {index}</command-message>"
            ),
        },
    })

    # 2b. Skill body — the injected payload that real Claude appends as
    #     the second user-role message after a slash-command wrapper.
    #     The ``Base directory for this skill:`` header is what the
    #     parser uses to confirm the skill body and complete the pair.
    skill_body = (
        f"Base directory for this skill: "
        f"{project}/.claude/skills/{skill_name}\n\n"
        f"Please run {skill_name} to add feature {index}. The skill "
        f"should create {rel_target} with a single feature_{index}() "
        f"function and commit the result."
    )
    _append(transcript, {
        "type": "user",
        "sessionId": session_id,
        "timestamp": _now(),
        "uuid": f"user-otbox-{suffix}-skill-body",
        "message": {
            "role": "user",
            "content": skill_body,
        },
    })

    # 3. Assistant: Read tool_use against the (just-created empty) file
    #    so the trace mirrors a real "look before you write" flow.
    _append(transcript, {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": _now(),
        "uuid": f"asst-otbox-{suffix}-1",
        "requestId": f"req-otbox-{suffix}-1",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Reading {rel_target} before adding feature_{index}()."
                    ),
                },
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

    # 3a. Pre-Read hook.
    _invoke_hook(on_pre_tool_use.main, {
        "session_id": session_id,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": tool_read_id,
        "tool_input": {"file_path": rel_target},
    })

    read_content = target.read_text(encoding="utf-8")

    # 3b. Post-Read hook.
    _invoke_hook(on_tool_use.main, {
        "session_id": session_id,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": tool_read_id,
        "tool_input": {"file_path": rel_target},
        "tool_response": {"content": read_content},
    })

    # 3c. tool_result back to assistant.
    _append(transcript, {
        "type": "user",
        "sessionId": session_id,
        "timestamp": _now(),
        "uuid": f"user-otbox-{suffix}-read-result",
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

    # 4. Assistant: Edit tool_use that writes the feature body.
    edit_tool_input = {
        "file_path": rel_target,
        "old_string": source_before,
        "new_string": source_after,
    }
    _append(transcript, {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": _now(),
        "uuid": f"asst-otbox-{suffix}-2",
        "requestId": f"req-otbox-{suffix}-2",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Adding feature_{index}() via {skill_name}."
                    ),
                },
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

    # 4a. Pre-Edit hook (file still pre-state).
    _invoke_hook(on_pre_tool_use.main, {
        "session_id": session_id,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": tool_edit_id,
        "tool_input": edit_tool_input,
    })

    # 4b. Apply the actual mutation between pre + post hooks.
    target.write_text(source_after, encoding="utf-8")

    # 4c. Post-Edit hook (file now post-state).
    _invoke_hook(on_tool_use.main, {
        "session_id": session_id,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": tool_edit_id,
        "tool_input": edit_tool_input,
        "tool_response": {"content": f"The file {rel_target} has been updated."},
    })

    # 4d. tool_result back to assistant.
    _append(transcript, {
        "type": "user",
        "sessionId": session_id,
        "timestamp": _now(),
        "uuid": f"user-otbox-{suffix}-edit-result",
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

    # 5. Final assistant turn (close the conversation).
    _append(transcript, {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": _now(),
        "uuid": f"asst-otbox-{suffix}-3",
        "requestId": f"req-otbox-{suffix}-3",
        "message": {
            "role": "assistant",
            "model": MODEL,
            "content": f"Added feature_{index}() via {skill_name}.",
            "usage": {"input_tokens": 80, "output_tokens": 12},
        },
    })

    # 6. Result message — session-end marker (timestamp_end / stop_reason).
    _append(transcript, {
        "type": "result",
        "subtype": "success",
        "sessionId": session_id,
        "timestamp": _now(),
        "duration_ms": 7000,
        "num_turns": 3,
        "stop_reason": "stop",
        "total_cost_usd": 0.0,
    })
