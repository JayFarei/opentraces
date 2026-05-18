"""Deterministic Claude Code session module — ``context-tree-via-proxy``.

Plan 078 prototype fixture. Mirrors ``context-tree-multi-turn`` (three
user turns, three Edits across three files) but adds a real HTTP POST
through the caller-supplied proxy per turn. The proxy capture log is
the second source of truth for the Context Tree substrate; the prototype's
``compare.py`` joins the two on ``step_index`` and reports the field
delta.

Call signature:

    run(project: Path, transcript: Path, *, proxy_url: str | None = None) -> None

When ``proxy_url`` is None the fixture writes the JSONL only (matches
the existing context-tree-multi-turn). When set, each user turn issues
one POST to ``{proxy_url}/v1/messages`` carrying:

- ``messages``: the active-path message list at that turn
- ``tools``: the tool registry WITH input_schema for every built-in
  (Read/Edit/Bash) — this is the key field JSONL parsing loses.
- ``system``: a multi-block system prompt list mirroring what Claude
  Code's static-core + CLAUDE.md merge looks like.
- ``OT-Property-*`` headers carrying step_index, session_id, cwd,
  permission-mode, transcript-uuid — the Helicone convention that
  plugs the harness-side provenance gap.

The HTTP responses are not consumed by the JSONL writer; the fixture
keeps the JSONL deterministic exactly as the JSONL-only fixture does.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

SESSION_ID = "sess-otbox-context-tree-via-proxy"
MODEL = "claude-sonnet-4-5"
HARNESS_VERSION = "otbox-fake-1.0"

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

# Built-in tool schemas as Anthropic ships them. These are the bytes the
# JSONL pipeline cannot reconstruct (the JSONL `system.init` record only
# carries name + description, no input_schema). Captured here verbatim
# from the public Claude Code tool catalogue.
_TOOL_INPUT_SCHEMAS = {
    "Read": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from.",
            },
            "limit": {
                "type": "integer",
                "description": "The number of lines to read.",
            },
        },
        "required": ["file_path"],
    },
    "Edit": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    "Bash": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "description": {"type": "string"},
            "timeout": {"type": "integer"},
            "run_in_background": {"type": "boolean"},
        },
        "required": ["command"],
    },
}

# Multi-block system prompt — mirrors what Claude Code's static core +
# CLAUDE.md merge looks like on the wire. The JSONL pipeline cannot
# see this; the static core is never written to disk and the CLAUDE.md
# set is reconstructed from on-disk reads (paths only, not block-by-block).
_SYSTEM_BLOCKS = [
    {
        "type": "text",
        "text": (
            "You are Claude, an AI assistant integrated into the Claude Code "
            "CLI. You help users with software-engineering tasks against a "
            "local project tree. Always prefer tool use over commentary, and "
            "always verify your work before claiming completion."
        ),
        "cache_control": {"type": "ephemeral"},
    },
    {
        "type": "text",
        "text": (
            "# Project CLAUDE.md\n\n"
            "This is a synthetic test project. There are three modules under "
            "src/ (mod_a.py, mod_b.py, mod_c.py) and each one starts empty. "
            "When asked to add a helper to a module, add it and ensure the "
            "function signature accepts a list[float] and returns float."
        ),
    },
    {
        "type": "text",
        "text": (
            "# Environment\n"
            "- cwd: {cwd}\n"
            "- model: claude-sonnet-4-5\n"
            "- permission_mode: default\n"
        ),
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append(transcript: Path, payload: dict) -> None:
    transcript.parent.mkdir(parents=True, exist_ok=True)
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _invoke_hook(main_func: Callable[[], None], payload: dict) -> None:
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


def _system_blocks_for(cwd: Path) -> list[dict[str, Any]]:
    """Return a fresh copy of the system blocks with cwd substituted."""
    blocks = []
    for block in _SYSTEM_BLOCKS:
        cloned = dict(block)
        if "{cwd}" in cloned.get("text", ""):
            cloned["text"] = cloned["text"].replace("{cwd}", str(cwd))
        blocks.append(cloned)
    return blocks


def _build_tools_payload() -> list[dict[str, Any]]:
    """Build the tools field as Claude Code would ship it on the wire."""
    return [
        {
            "name": name,
            "description": f"Built-in Claude Code tool: {name}.",
            "input_schema": _TOOL_INPUT_SCHEMAS[name],
        }
        for name in ("Read", "Edit", "Bash")
    ]


def _post_to_proxy(
    proxy_url: str,
    *,
    project: Path,
    messages: list[dict[str, Any]],
    turn_index: int,
    step_index: int,
    transcript_uuid: str,
    transcript_parent_uuid: str | None,
) -> None:
    """Issue one /v1/messages POST through the proxy for a given turn."""
    body = {
        "model": MODEL,
        "max_tokens": 4096,
        "system": _system_blocks_for(project),
        "messages": messages,
        "tools": _build_tools_payload(),
        "tool_choice": {"type": "auto"},
        "metadata": {"user_id": "otbox-fixture-user"},
    }
    headers = {
        "Content-Type": "application/json",
        "Anthropic-Version": "2023-06-01",
        "User-Agent": "claude-cli/proto-0.0.1 (otbox-fixture)",
        # Helicone-style custom-properties so the proxy can capture
        # harness-side provenance that the wire alone would not show.
        "OT-Property-session-id": SESSION_ID,
        "OT-Property-cwd": str(project),
        "OT-Property-permission-mode": "default",
        "OT-Property-step-index": str(step_index),
        "OT-Property-turn-index": str(turn_index),
        "OT-Property-transcript-uuid": transcript_uuid,
        # Redacted in the capture log; we still send a value to confirm
        # the redaction path is exercised.
        "Authorization": "Bearer sk-fake-proto-key-9999",
    }
    if transcript_parent_uuid:
        headers["OT-Property-transcript-parent-uuid"] = transcript_parent_uuid
    # Best-effort: short timeout so a misconfigured proxy doesn't hang the
    # fixture. The fixture is OK with the POST failing — the assertion in
    # the comparison report is "we tried", not "the upstream returned X".
    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(f"{proxy_url}/v1/messages", json=body, headers=headers)
    except Exception:  # noqa: BLE001
        # Swallow; the fixture should not crash the JSONL pipeline run
        # just because the proxy is down. Calls that fail still get
        # logged on the proxy side if the proxy logged the request before
        # the upstream call errored.
        pass


def run(
    project: Path,
    transcript: Path,
    *,
    proxy_url: str | None = None,
) -> None:
    """Drive the deterministic three-turn session, optionally hitting a proxy.

    ``project`` is the project root (gets the synthetic ``src/mod_*.py``
    files created). ``transcript`` is the JSONL output path the JSONL
    pipeline parses. ``proxy_url`` (optional) is the proxy's HTTP URL;
    when set, one HTTP POST per turn is issued through it.
    """
    src_dir = project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    for slug, _helper, _summary in _MODULES:
        (src_dir / f"mod_{slug}.py").write_text("", encoding="utf-8")

    prior_cwd = Path.cwd()
    os.chdir(project)
    try:
        _run_session(project, transcript, proxy_url)
    finally:
        os.chdir(prior_cwd)


def _run_session(project: Path, transcript: Path, proxy_url: str | None) -> None:
    from opentraces.capture.claude_code.hooks import on_pre_tool_use, on_tool_use

    sys_uuid = "sys-otbox-ctx-proxy-init"

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
    accumulated_messages: list[dict[str, Any]] = []
    step_counter = 0

    for turn_index, (slug, helper, summary) in enumerate(_MODULES, start=1):
        rel_target = f"src/mod_{slug}.py"
        target = project / rel_target
        source_before = ""
        source_after = _module_source(helper, summary)

        suffix = f"via-proxy-{turn_index}"
        tool_read_id = f"toolu_otbox_ctx_{suffix}_read"
        tool_edit_id = f"toolu_otbox_ctx_{suffix}_edit"

        user_uuid = f"user-otbox-ctx-{suffix}-prompt"
        asst_read_uuid = f"asst-otbox-ctx-{suffix}-read"
        tr_read_uuid = f"user-otbox-ctx-{suffix}-read-result"
        asst_edit_uuid = f"asst-otbox-ctx-{suffix}-edit"
        tr_edit_uuid = f"user-otbox-ctx-{suffix}-edit-result"
        asst_close_uuid = f"asst-otbox-ctx-{suffix}-close"

        user_prompt = (
            f"Please add a `{helper}()` helper to {rel_target} that "
            f"computes the mean over a list. Commit once it's in."
        )
        accumulated_messages.append({"role": "user", "content": user_prompt})

        # JSONL-side user record
        _append(transcript, {
            "type": "user",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "uuid": user_uuid,
            "parentUuid": prev_uuid,
            "message": {"role": "user", "content": user_prompt},
        })

        # Proxy-side POST for this turn. Issued BEFORE any assistant
        # records so the message-list snapshot matches what the real
        # Claude Code client would have sent at this decision point.
        if proxy_url is not None:
            _post_to_proxy(
                proxy_url,
                project=project,
                messages=list(accumulated_messages),
                turn_index=turn_index,
                step_index=step_counter,
                transcript_uuid=user_uuid,
                transcript_parent_uuid=prev_uuid,
            )

        # Assistant: Read tool_use record (JSONL only — no second proxy
        # call per inner tool, mirroring how a real session uses one
        # /messages POST per turn that emits a multi-block response).
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
        accumulated_messages.append({
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"Reading {rel_target} before adding {helper}()."},
                {
                    "type": "tool_use",
                    "id": tool_read_id,
                    "name": "Read",
                    "input": {"file_path": rel_target},
                },
            ],
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
        accumulated_messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_read_id,
                "content": read_content,
            }],
        })

        # Assistant: Edit tool_use
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
        accumulated_messages.append({
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"Adding {helper}() to {rel_target}."},
                {
                    "type": "tool_use",
                    "id": tool_edit_id,
                    "name": "Edit",
                    "input": edit_tool_input,
                },
            ],
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
        accumulated_messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_edit_id,
                "content": f"The file {rel_target} has been updated.",
            }],
        })

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
        accumulated_messages.append({
            "role": "assistant",
            "content": f"Added {helper}() to {rel_target}. Ready for the next turn.",
        })

        prev_uuid = asst_close_uuid
        step_counter += 1

    # End-of-session marker.
    _append(transcript, {
        "type": "result",
        "subtype": "success",
        "sessionId": SESSION_ID,
        "timestamp": _now(),
        "duration_ms": 24000,
        "num_turns": 3,
        "stop_reason": "stop",
        "total_cost_usd": 0.0,
    })
