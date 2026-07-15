"""Host-side terminal control for one real harness action in a leased box."""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from ..box import Box
from ..run_store import RunDraft
from .actions import RunActionSequence


@dataclass(frozen=True)
class AgentTerminalObservation:
    """One local read of terminal-control's retained PTY state."""

    state: str
    screen: str
    logs: str


class AgentTerminalSession(Protocol):
    """The local terminal-control boundary used by the drive loop."""

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
    ) -> None: ...

    def send(self, text: str) -> None: ...

    def observe(self) -> AgentTerminalObservation: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class AgentTerminalResult:
    status: str
    reason: dict[str, str] | None
    duration_ms: int
    invocation_ref: str
    result_ref: str
    transcript_ref: str
    recording_ref: str


TermctrlRunner = Callable[..., subprocess.CompletedProcess[str]]


def _run_termctrl(
    argv: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: float = 5,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


class TermctrlAgentSession:
    """Thin adapter over one host-local ``termctrl`` session."""

    def __init__(self, name: str, *, runner: TermctrlRunner = _run_termctrl) -> None:
        self.name = name
        self.runner = runner

    def _call(
        self,
        *args: str,
        input_text: str | None = None,
        timeout: float = 5,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner(
            ["termctrl", *args],
            input_text=input_text,
            timeout=timeout,
        )

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
    ) -> None:
        started = self._call(
            "start",
            self.name,
            "--record",
            str(recording_path),
            "--cols",
            str(cols),
            "--rows",
            str(rows),
            "--",
            *argv,
            timeout=10,
        )
        if started.returncode != 0:
            raise RuntimeError("terminal-control could not start the agent session")

    def send(self, text: str) -> None:
        pasted = self._call("send", self.name, "--stdin", input_text=text)
        entered = self._call("send", self.name, "enter")
        if pasted.returncode != 0 or entered.returncode != 0:
            raise RuntimeError("terminal-control could not send the agent prompt")

    def observe(self) -> AgentTerminalObservation:
        # All three reads address the host-local control socket. The SSH PTY is
        # never probed by the poll loop.
        status = self._call("status", self.name, "--json")
        screen = self._call("show", self.name)
        logs = self._call("logs", self.name)
        if status.returncode != 0:
            state = "disconnected"
        else:
            try:
                payload = json.loads(status.stdout)
                state = str(payload.get("state") or "disconnected")
            except (AttributeError, json.JSONDecodeError):
                state = "disconnected"
        return AgentTerminalObservation(
            state=state,
            screen=screen.stdout if screen.returncode == 0 else "",
            logs=logs.stdout if logs.returncode == 0 else "",
        )

    def stop(self) -> None:
        self._call("stop", self.name)


AgentTerminalSessionFactory = Callable[[str], AgentTerminalSession]


class AgentTerminalDrive:
    """Drive one bounded prompt through a real harness over a Box SSH PTY."""

    def __init__(
        self,
        *,
        box: Box,
        draft: RunDraft,
        actions: RunActionSequence,
        session_factory: AgentTerminalSessionFactory = TermctrlAgentSession,
        poll_interval: float = 0.3,
    ) -> None:
        if poll_interval < 0:
            raise ValueError("agent poll interval must be non-negative")
        self.box = box
        self.draft = draft
        self.actions = actions
        self.session_factory = session_factory
        self.poll_interval = poll_interval

    @staticmethod
    def _env_pins(env: Mapping[str, str]) -> dict[str, str]:
        return {
            name: f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
            for name, value in sorted(env.items())
        }

    def _ssh_argv(
        self,
        harness_argv: Sequence[str],
        env: Mapping[str, str],
    ) -> list[str]:
        remote_argv = [
            "env",
            *(f"{name}={value}" for name, value in sorted(env.items())),
            *harness_argv,
        ]
        return [
            "ssh",
            "-tt",
            "-F",
            "/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=4",
            "-i",
            self.box.ssh_key,
            "-p",
            self.box.ssh_port,
            f"{self.box.ssh_user}@{self.box.ssh_host}",
            "--",
            *remote_argv,
        ]

    @staticmethod
    def _combined_observation(observation: AgentTerminalObservation) -> str:
        if observation.screen and observation.screen not in observation.logs:
            return f"{observation.logs}\n{observation.screen}".lstrip("\n")
        return observation.logs or observation.screen

    def run(
        self,
        *,
        harness_argv: Sequence[str],
        prompt: str,
        expect_regex: str,
        timeout: float,
        env: Mapping[str, str] | None = None,
        cols: int = 110,
        rows: int = 32,
    ) -> AgentTerminalResult:
        if not harness_argv:
            raise ValueError("agent harness argv must not be empty")
        if not prompt:
            raise ValueError("agent prompt must not be empty")
        if timeout <= 0:
            raise ValueError("agent timeout must be positive")
        environment = dict(env or {})
        expected = re.compile(expect_regex, re.IGNORECASE)
        allocation = self.actions.allocate("agent")
        action = allocation.action_ref
        invocation_ref = f"{action}/invocation.json"
        result_ref = f"{action}/result.json"
        transcript_ref = f"{action}/transcript.txt"
        recording_ref = f"recordings/agent-{allocation.ordinal:04d}.termctrl"
        self.draft.write_json(
            invocation_ref,
            {
                "ordinal": allocation.ordinal,
                "surface": "agent",
                "kind": "interactive_prompt",
                "harness_argv": list(harness_argv),
                "env_pins": self._env_pins(environment),
                "expect_regex": expect_regex,
                "timeout_ms": int(timeout * 1_000),
                "started_at": allocation.started_at,
                "box": {
                    "provider": self.box.provider,
                    "sandbox_tier": self.box.sandbox_tier,
                },
            },
        )
        session = self.session_factory(f"ot-{self.draft.run_id}-{allocation.ordinal:04d}")
        recording_path = self.draft.path / recording_ref
        latest = ""
        reason: dict[str, str] | None = None
        status = "fail"
        connected = False
        try:
            session.start(
                # Box.ssh_user is the transport identity, not automatically the
                # product actor. The high-level harness adapter must supply any
                # required actor switch in harness_argv.
                self._ssh_argv(harness_argv, environment),
                recording_path=recording_path,
                cols=cols,
                rows=rows,
            )
            connected = True
            session.send(prompt)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    observation = session.observe()
                except Exception:
                    observation = AgentTerminalObservation(
                        state="disconnected", screen="", logs=latest
                    )
                latest = self._combined_observation(observation)
                if expected.search(latest):
                    status = "pass"
                    break
                if observation.state == "disconnected":
                    reason = {
                        "code": "agent_drive_disconnected",
                        "message": f"agent terminal disconnected during {action}",
                    }
                    connected = False
                    break
                if observation.state == "exited":
                    reason = {
                        "code": "agent_drive_exited_before_expectation",
                        "message": f"agent terminal exited before expectation during {action}",
                    }
                    connected = False
                    break
                if time.monotonic() >= deadline:
                    reason = {
                        "code": "agent_drive_timeout",
                        "message": f"agent terminal timed out during {action}",
                    }
                    break
                if self.poll_interval:
                    time.sleep(self.poll_interval)
        except Exception:
            reason = {
                "code": "agent_drive_disconnected",
                "message": f"agent terminal disconnected during {action}",
            }
            connected = False
        finally:
            if connected:
                session.stop()

        duration_ms = self.actions.complete(allocation)
        self.draft.write_text(transcript_ref, latest)
        self.draft.write_json(
            result_ref,
            {
                "execution_status": "complete",
                "status": status,
                "duration_ms": duration_ms,
                "reason": reason,
                "transcript_ref": transcript_ref,
                "recording_ref": recording_ref,
            },
        )
        return AgentTerminalResult(
            status=status,
            reason=reason,
            duration_ms=duration_ms,
            invocation_ref=invocation_ref,
            result_ref=result_ref,
            transcript_ref=transcript_ref,
            recording_ref=recording_ref,
        )
