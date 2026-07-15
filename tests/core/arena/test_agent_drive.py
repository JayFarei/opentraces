from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Mapping

from opentraces.core.arena.box import Box
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.drives.actions import RunActionSequence
from opentraces.core.arena.drives.agent import (
    AgentTerminalDrive,
    AgentTerminalObservation,
)
from opentraces.core.arena.run_store import RunStore


class ScriptedTerminalControlSession:
    """Local terminal-control boundary double; it never probes the box."""

    def __init__(self, observations: list[AgentTerminalObservation]) -> None:
        self.observations = iter(observations)
        self.started_argv: list[str] | None = None
        self.started_env: dict[str, str] = {}
        self.sent: list[str] = []
        self.start_count = 0
        self.stop_count = 0
        self.observe_count = 0

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.start_count += 1
        self.started_argv = list(argv)
        self.started_env = dict(env or {})
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_bytes(b"partial terminal-control recording")

    def send(self, text: str) -> None:
        self.sent.append(text)

    def observe(self) -> AgentTerminalObservation:
        self.observe_count += 1
        return next(self.observations)

    def stop(self) -> None:
        self.stop_count += 1

    def recording_complete(self, recording_path: Path) -> bool:
        return recording_path.is_file() and recording_path.stat().st_size > 0


def _box() -> Box:
    return Box(
        id="cbx_a6",
        slug="a6",
        provider="local-container",
        sandbox_tier="container",
        ssh_host="127.0.0.1",
        ssh_user="bench",
        ssh_port="2222",
        ssh_key="/keys/bench_ed25519",
        image="ubuntu:24.04",
    )


def _draft(tmp_path: Path):
    store = RunStore(tmp_path / "runs" / "v1")
    draft = store.begin()
    actions = RunActionSequence(draft=draft, run_started_monotonic=time.monotonic())
    return store, draft, actions


def test_forced_agent_disconnect_is_one_bounded_named_failure_in_a_verifiable_run(
    tmp_path: Path,
) -> None:
    store, draft, actions = _draft(tmp_path)
    session = ScriptedTerminalControlSession(
        [
            AgentTerminalObservation(
                state="disconnected",
                screen="working on it",
                logs="connected\nworking on it\nssh connection closed",
            )
        ]
    )
    drive = AgentTerminalDrive(
        box=_box(),
        draft=draft,
        actions=actions,
        session_factory=lambda _name: session,
        poll_interval=0,
    )

    started = time.monotonic()
    result = drive.run(
        harness_argv=["claude"],
        prompt="Make the small change and commit it.",
        expect_regex=r"commit [0-9a-f]{7,}",
        timeout=30,
    )

    assert time.monotonic() - started < 1
    assert result.status == "fail"
    assert result.reason == {
        "code": "agent_drive_disconnected",
        "message": "agent terminal disconnected during actions/0001",
    }
    assert session.start_count == 1
    assert session.observe_count == 1
    assert session.stop_count == 1
    assert session.sent == ["Make the small change and commit it."]
    assert (draft.path / result.recording_ref).read_bytes().startswith(b"partial")
    assert (
        json.loads((draft.path / result.result_ref).read_text(encoding="utf-8"))["reason"]
        == result.reason
    )
    assert actions.timeline_status() == {
        "complete": True,
        "path": "recordings/timeline.jsonl",
        "reason": None,
    }

    envelope = build_result(
        run_id=draft.run_id,
        claim="A real harness remains honestly observable when its PTY disconnects.",
        nodeid="tests/core/arena/test_agent_drive.py::disconnect",
        source_ref="source/scenario.py",
        execution_mode="agent_live",
        started_at="2026-07-15T00:00:00Z",
        duration_ms=result.duration_ms,
        execution_status="complete",
        verdict="fail",
        reason=result.reason,
        verifiers=[],
        evidence={
            "complete": False,
            "requirements": [
                {
                    "name": "agent.terminal_observation",
                    "complete": False,
                    "evidence_refs": [result.result_ref, result.recording_ref],
                }
            ],
        },
        recordings={
            "rewatchable": False,
            "channels": [
                {
                    "kind": "agent_terminal",
                    "complete": False,
                    "path": result.recording_ref,
                    "reason": result.reason["message"],
                }
            ],
        },
        artifacts=[],
        capture=None,
        pins={},
    )
    finalized = draft.finalize(envelope)
    assert store.verify(finalized) is True


def test_agent_drive_uses_box_ssh_facts_and_only_local_polls(tmp_path: Path) -> None:
    _store, draft, actions = _draft(tmp_path)
    session = ScriptedTerminalControlSession(
        [
            AgentTerminalObservation(state="running", screen="", logs="ready"),
            AgentTerminalObservation(
                state="running",
                screen="commit abcdef1",
                logs="ready\ncommit abcdef1",
            ),
        ]
    )
    drive = AgentTerminalDrive(
        box=_box(),
        draft=draft,
        actions=actions,
        session_factory=lambda _name: session,
        poll_interval=0,
    )

    result = drive.run(
        harness_argv=["claude", "--permission-mode", "acceptEdits"],
        prompt="Commit the change.",
        expect_regex=r"commit [0-9a-f]{7,}",
        timeout=5,
        env={"ANTHROPIC_API_KEY": "live-secret"},
    )

    assert result.status == "pass"
    assert result.reason is None
    assert session.started_argv is not None
    assert not any("live-secret" in argument for argument in session.started_argv)
    assert session.started_env == {"ANTHROPIC_API_KEY": "live-secret"}
    assert "SendEnv=ANTHROPIC_API_KEY" in session.started_argv
    remote = session.started_argv[session.started_argv.index("--") + 1 :]
    assert remote == ["claude", "--permission-mode", "acceptEdits"]
    assert session.observe_count == 2
    assert session.start_count == 1
    invocation = (draft.path / result.invocation_ref).read_text(encoding="utf-8")
    assert "live-secret" not in invocation
    assert "ANTHROPIC_API_KEY" in invocation


def test_agent_harness_exit_before_match_is_not_reported_as_ssh_disconnect(
    tmp_path: Path,
) -> None:
    _store, draft, actions = _draft(tmp_path)
    session = ScriptedTerminalControlSession(
        [AgentTerminalObservation(state="exited", screen="goodbye", logs="goodbye")]
    )
    drive = AgentTerminalDrive(
        box=_box(),
        draft=draft,
        actions=actions,
        session_factory=lambda _name: session,
        poll_interval=0,
    )

    result = drive.run(
        harness_argv=["claude"],
        prompt="Commit the change.",
        expect_regex=r"commit [0-9a-f]{7,}",
        timeout=5,
    )

    assert result.status == "fail"
    assert result.reason == {
        "code": "agent_drive_exited_before_expectation",
        "message": "agent terminal exited before expectation during actions/0001",
    }
