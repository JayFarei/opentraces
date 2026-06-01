"""Capture a REAL client session into the bucket (plan 089, R1).

Synthesizes a believable Claude Code session in which a client agent investigates
``delay_seconds("1h30m")`` returning 3600 instead of 5400, runs the failing test,
and traces it to the consumed ``humanduration`` dependency. Ingesting that session
through the real ``ingest_one_session`` pipeline produces a genuine bucket
TraceRecord that ``capsule export`` can package — not a hand-built fixture.

Used by the live U5 proof and the U7 regression/journey.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

# The github URL the client pins (the consumed dependency at the buggy version).
HUMANDURATION_PIN = "git+https://github.com/JayFarei/humanduration@v0.1.0"

_APP = '''\
"""Client app: schedule a task after a human-readable delay."""
from humanduration import parse


def delay_seconds(spec: str) -> int:
    return parse(spec)
'''

_TEST = '''\
from app import delay_seconds


def test_delay_is_5400():
    assert delay_seconds("1h30m") == 5400
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def build_client_project(dest: Path) -> Path:
    """A real git repo + opted-in opentraces project that consumes humanduration@v0.1.0."""

    client = Path(dest)
    client.mkdir(parents=True, exist_ok=True)
    (client / "app.py").write_text(_APP, encoding="utf-8")
    (client / "test_delay.py").write_text(_TEST, encoding="utf-8")
    (client / "requirements.txt").write_text(f"humanduration @ {HUMANDURATION_PIN}\n", encoding="utf-8")
    _git(client, "init", "-q")
    _git(client, "config", "user.email", "client@opentraces.test")
    _git(client, "config", "user.name", "client agent")
    _git(client, "config", "commit.gpgsign", "false")
    _git(client, "add", "-A")
    _git(client, "commit", "-q", "-m", "client: delay scheduler consuming humanduration")
    (client / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": "humanduration-client",
        "review_policy": "auto",
        "push_policy": "manual",
        "remotes": {"origin": {"url": "JayFarei/humanduration-client", "visibility": "public"}},
        "active_remote": "origin",
        "agents": ["claude-code"],
    }), encoding="utf-8")
    return client


def _msg(session_id, ts, role, content, **extra):
    return {"type": role, "sessionId": session_id, "timestamp": ts,
            "message": {"role": "assistant" if role == "assistant" else "user", "content": content},
            **extra}


def _client_session_lines(session_id: str, cwd: str) -> list[dict]:
    """A realistic debugging session ending at a failing pytest run."""

    t = lambda i: f"2026-06-01T09:00:{i:02d}Z"
    pytest_fail = (
        "============================= test session starts =============================\n"
        "collected 1 item\n\ntest_delay.py F                                          [100%]\n\n"
        "=================================== FAILURES ===================================\n"
        "_________________________________ test_delay __________________________________\n\n"
        "    def test_delay_is_5400():\n"
        ">       assert delay_seconds(\"1h30m\") == 5400\n"
        "E       assert 3600 == 5400\nE        +  where 3600 = delay_seconds('1h30m')\n\n"
        "test_delay.py:5: AssertionError\n=========================== 1 failed in 0.02s ============================"
    )
    lines: list[dict] = [
        _msg(session_id, t(1), "user", "delay_seconds('1h30m') returns 3600 but it should be 5400. "
             "Figure out why and confirm with the test.", cwd=cwd),
        _msg(session_id, t(2), "assistant",
             [{"type": "text", "text": "Let me run the test to see the failure."},
              {"type": "tool_use", "id": "tu_pytest", "name": "Bash",
               "input": {"command": "python -m pytest -q test_delay.py",
                         "description": "Run the failing delay test"}}], cwd=cwd),
        _msg(session_id, t(3), "user",
             [{"type": "tool_result", "tool_use_id": "tu_pytest", "content": pytest_fail,
               "is_error": True}],
             toolUseResult={"stdout": pytest_fail, "stderr": "", "exit_code": 1}, cwd=cwd),
        _msg(session_id, t(4), "assistant",
             [{"type": "text", "text": "delay_seconds just calls humanduration.parse. Let me read app.py."},
              {"type": "tool_use", "id": "tu_read", "name": "Read", "input": {"file_path": "app.py"}}],
             cwd=cwd),
        _msg(session_id, t(5), "user",
             [{"type": "tool_result", "tool_use_id": "tu_read", "content": _APP}], cwd=cwd),
        _msg(session_id, t(6), "assistant",
             [{"type": "text", "text": "app.py is correct — it delegates to humanduration.parse. The "
               "bug is in the consumed dependency humanduration@v0.1.0, which drops the minutes "
               "component (1h30m -> 3600). The client code is not at fault; the library needs a fix."}],
             cwd=cwd),
    ]
    return lines


def capture_client_trace(root: Path, *, cfg=None):
    """Build the client project, synthesize + ingest a real session; return (project, trace_id)."""

    from opentraces.core.ingest import ingest_one_session

    client = build_client_project(root / "client")
    session_id = str(uuid.uuid4())
    corpus = root / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    jsonl = corpus / f"{session_id}.jsonl"
    with jsonl.open("w", encoding="utf-8") as f:
        for line in _client_session_lines(session_id, str(client)):
            f.write(json.dumps(line) + "\n")

    result = ingest_one_session(jsonl, client, cfg=cfg)
    return client, result
