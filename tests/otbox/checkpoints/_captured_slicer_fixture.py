"""``c-captured-slicer-fixture`` — deterministic fixture for the Trace Slicer
Library conformance journey (issue #141).

A slim captured-session: it ingests a hand-crafted Claude Code transcript whose
PARSED shape pins exact S1/S2 boundaries and contains the control messages
(``<task-notification>`` + a bare ``continue``) the S1 continuation-heuristic
must absorb. The journey forked from here partitions ``{trace_id}`` and pins the
trajectory spans, so the three slicer faultpoints (off-by-one, dropped
continuation, self-report-valid-gap) each break a pinned assertion.

Unlike ``c-captured-real-session`` this skips the Trail/anchor/commit chain —
slicing is a derive-on-demand read over ``TraceRecord.steps`` and needs only an
ingested, indexed trace.

PARSED step reality (verified offline against ClaudeCodeParser):
    0 user  "Add a farewell(name) helper..."     (genuine ask #1)
    1 agent Edit
    2 user  "<task-notification> ... </...>"      (CONTROL — absorbed by S1)
    3 user  "Noted the background task."          (opens — not a control tag)
    4 user  "continue"                            (CONTINUATION — absorbed by S1)
    5 agent Edit
    6 user  "Now also add a hello(name) helper."  (genuine ask #2)
    7 agent Edit
    8 user  "Added farewell, goodbye, and hello helpers."
  => s1 spans [(0,2),(3,5),(6,7),(8,8)] ; s2 spans [(0,0),(1,2),(3,5),(6,7),(8,8)]
"""
from __future__ import annotations

import json

from ..env import Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    check as _check_helper,
    encode_claude_path,
    read_state_json,
    synthetic_capture_metadata,
    trace_for_session,
)

_CHECKPOINT = "c-captured-slicer-fixture"


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_CHECKPOINT, label=label)


_SESSION_ID = "sess-otbox-slicer-conformance"
_MODEL = "claude-sonnet-4-5"
_TS = "2026-01-01T00:00:00Z"

_BEFORE = 'def greet(name):\n    return f"hi {name}"\n'
_AFTER1 = _BEFORE + '\n\ndef farewell(name):\n    return f"bye {name}"\n'
_AFTER2 = _AFTER1 + '\n\ndef goodbye(name):\n    return f"goodbye {name}"\n'
_AFTER3 = _AFTER2 + '\n\ndef hello(name):\n    return f"hello {name}"\n'


def _asst_text(uid, text):
    return {"type": "assistant", "sessionId": _SESSION_ID, "timestamp": _TS, "uuid": uid,
            "requestId": uid, "message": {"role": "assistant", "model": _MODEL, "content": text,
                                          "usage": {"input_tokens": 10, "output_tokens": 5}}}


def _asst_edit(uid, tid, old, new):
    return {"type": "assistant", "sessionId": _SESSION_ID, "timestamp": _TS, "uuid": uid,
            "requestId": uid, "message": {"role": "assistant", "model": _MODEL, "content": [
                {"type": "text", "text": "editing"},
                {"type": "tool_use", "id": tid, "name": "Edit",
                 "input": {"file_path": "src/app.py", "old_string": old, "new_string": new}}],
                "usage": {"input_tokens": 10, "output_tokens": 5}}}


def _user(uid, text):
    return {"type": "user", "sessionId": _SESSION_ID, "timestamp": _TS, "uuid": uid,
            "message": {"role": "user", "content": text}}


def _tool_result(uid, tid, content):
    return {"type": "user", "sessionId": _SESSION_ID, "timestamp": _TS, "uuid": uid,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": content}]}}


def _transcript_lines(project: str) -> list[dict]:
    return [
        {"type": "system", "subtype": "init", "sessionId": _SESSION_ID, "timestamp": _TS,
         "cwd": project, "model": _MODEL, "claude_code_version": "otbox-fake-1.0",
         "tools": [{"name": "Edit", "description": "edit"}], "permissionMode": "default",
         "gitBranch": "main"},
        _user("u1", "Add a farewell(name) helper to src/app.py."),
        _asst_edit("a1", "t1", _BEFORE, _AFTER1),
        _tool_result("u1r", "t1", "The file src/app.py has been updated."),
        _user("u2", "<task-notification> background task wu7 finished </task-notification>"),
        _asst_text("a2", "Noted the background task."),
        _user("u3", "continue"),
        _asst_edit("a3", "t2", _AFTER1, _AFTER2),
        _tool_result("u3r", "t2", "The file src/app.py has been updated."),
        _user("u4", "Now also add a hello(name) helper."),
        _asst_edit("a4", "t3", _AFTER2, _AFTER3),
        _tool_result("u4r", "t3", "The file src/app.py has been updated."),
        _asst_text("a5", "Added farewell, goodbye, and hello helpers."),
        {"type": "result", "subtype": "success", "sessionId": _SESSION_ID, "timestamp": _TS,
         "duration_ms": 1, "num_turns": 5, "stop_reason": "stop", "total_cost_usd": 0.0},
    ]


def _slicer_fixture_delta(driver, box: Box) -> None:
    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]
    cli = resolve_cli_argv()

    # 1. A real git project (init expects an opt-in-able repo).
    driver.mkdir(box, project)
    driver.mkdir(box, f"{project}/src")
    driver.put_text(box, f"{project}/src/app.py", _BEFORE)
    driver.exec(box, ["git", "-C", project, "init", "-q"])
    driver.exec(box, ["git", "-C", project, "add", "-A"])
    driver.exec(box, ["git", "-C", project, "commit", "-q", "-m", "seed"])

    _check(
        driver.exec(box, [*cli, "init", "--start-fresh", "--agent", "claude-code"]),
        "opentraces init",
    )

    # 2. Write the deterministic transcript to the canonical Claude path.
    encoded = encode_claude_path(project)
    transcript_path = f"{home}/.claude/projects/{encoded}/{_SESSION_ID}.jsonl"
    driver.mkdir(box, f"{home}/.claude/projects/{encoded}")
    body = "\n".join(json.dumps(line, sort_keys=True) for line in _transcript_lines(project)) + "\n"
    driver.put_text(box, transcript_path, body)

    # 3. Ingest (creates the bucket TraceRecord + project state entry).
    _check(
        driver.exec(box, [*cli, "_ingest-session", transcript_path, "--project", project]),
        "_ingest-session",
    )

    # 4. Populate the read-only search snapshot so resolution works.
    _check(driver.exec(box, [*cli, "trace", "index"]), "trace index")

    # 5. Resolve the minted trace_id from project state.
    state_dir, state = read_state_json(driver, box)
    trace_id, step_count = trace_for_session(state, _SESSION_ID)
    if not trace_id:
        raise CheckpointError(
            f"c-captured-slicer-fixture ingested no trace for session {_SESSION_ID}; "
            f"state at {state_dir} had {len(state.get('traces') or {})} entries"
        )

    audit = {
        "session_id": _SESSION_ID,
        "trace_id": trace_id,
        "step_count": step_count,
        "transcript_path": transcript_path,
        "state_dir": state_dir,
        # Pinned PARSED boundaries (also pinned in the journey TOML).
        "s1_spans": [[0, 2], [3, 5], [6, 7], [8, 8]],
        "s2_spans": [[0, 0], [1, 2], [3, 5], [6, 7], [8, 8]],
        "capture_metadata": synthetic_capture_metadata(),
    }
    box.notes["c_captured_slicer_fixture_audit"] = audit
    # Expose {trace_id} to the journey templater, which resolves it from the
    # canonical ``c_captured_session_audit`` key.
    box.notes["c_captured_session_audit"] = {"trace_id": trace_id, "session_id": _SESSION_ID}


register(
    Checkpoint(
        name="c-captured-slicer-fixture",
        composed_from="c-installed-source",
        delta=_slicer_fixture_delta,
        cache=False,
        description=(
            "c-installed-source + a deterministic ingested Claude Code session "
            "whose parsed shape pins exact S1/S2 trajectory boundaries and "
            "carries the control messages the S1 continuation-heuristic absorbs "
            "— the fixture the Trace Slicer Library conformance journey + its "
            "three mutation kills (issue #141) fork from."
        ),
        provides={
            "captured_traces": 1,
        },
    )
)
