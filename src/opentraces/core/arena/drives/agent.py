"""Host-side terminal control for one real harness action in a leased box."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shlex
import subprocess
import tempfile
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
        env: Mapping[str, str] | None = None,
    ) -> None: ...

    def send(self, text: str) -> None: ...

    def observe(self) -> AgentTerminalObservation: ...

    def stop(self) -> None: ...

    def recording_complete(self, recording_path: Path) -> bool: ...


@dataclass(frozen=True)
class AgentTerminalResult:
    status: str
    reason: dict[str, str] | None
    duration_ms: int
    invocation_ref: str
    result_ref: str
    transcript_ref: str
    recording_ref: str
    recording_complete: bool


TermctrlRunner = Callable[..., subprocess.CompletedProcess[str]]


def _run_termctrl(
    argv: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: float = 5,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env={**os.environ, **dict(env or {})},
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
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner(
            ["termctrl", *args],
            input_text=input_text,
            timeout=timeout,
            env=env,
        )

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
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
            env=env,
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
        stopped = self._call("stop", self.name)
        if stopped.returncode != 0:
            raise RuntimeError("terminal-control could not stop the agent session")

    def recording_complete(self, recording_path: Path) -> bool:
        if not recording_path.is_file() or recording_path.stat().st_size == 0:
            return False
        with tempfile.TemporaryDirectory(prefix="opentraces-termctrl-") as directory:
            output = Path(directory) / "recording.txt"
            saved = self._call(
                "save",
                "--recording",
                str(recording_path),
                "--format",
                "txt",
                "--out",
                str(output),
                timeout=10,
            )
            return saved.returncode == 0 and output.is_file()


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
        reverse_forwards: Sequence[tuple[int, int]],
    ) -> list[str]:
        argv = [
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
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-i",
            self.box.ssh_key,
            "-p",
            self.box.ssh_port,
        ]
        for name in sorted(env):
            argv.extend(["-o", f"SendEnv={name}"])
        for remote_port, local_port in reverse_forwards:
            argv.extend(
                [
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-R",
                    f"127.0.0.1:{remote_port}:127.0.0.1:{local_port}",
                ]
            )
        if self.box.workspace is None:
            raise ValueError("agent box has no validated materialized workspace")
        self.box.bind_workspace(self.box.workspace)
        remote_argv = [
            "/bin/sh",
            "-c",
            'cd -- "$1" && shift && exec "$@"',
            "opentraces-agent-workspace",
            self.box.workspace,
            *harness_argv,
        ]
        return [
            *argv,
            f"{self.box.ssh_user}@{self.box.ssh_host}",
            "--",
            shlex.join(remote_argv),
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
        reverse_forwards: Sequence[tuple[int, int]] = (),
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
        started = False
        cleanup_error: Exception | None = None
        try:
            session.start(
                # Box.ssh_user is the transport identity, not automatically the
                # product actor. The high-level harness adapter must supply any
                # required actor switch in harness_argv.
                self._ssh_argv(harness_argv, environment, reverse_forwards),
                recording_path=recording_path,
                cols=cols,
                rows=rows,
                env=environment,
            )
            started = True
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
                if observation.state == "disconnected":
                    reason = {
                        "code": "agent_drive_disconnected",
                        "message": f"agent terminal disconnected during {action}",
                    }
                    break
                if observation.state == "exited":
                    reason = {
                        "code": "agent_drive_exited_before_expectation",
                        "message": f"agent terminal exited before expectation during {action}",
                    }
                    break
                if expected.search(latest):
                    status = "pass"
                    break
                if time.monotonic() >= deadline:
                    reason = {
                        "code": "agent_drive_timeout",
                        "message": f"agent terminal timed out during {action}",
                    }
                    break
                if self.poll_interval:
                    time.sleep(self.poll_interval)
        except Exception as exc:
            reason = {
                "code": "agent_drive_control_failed",
                "message": f"agent terminal control failed during {action}: {type(exc).__name__}",
            }
        finally:
            if started:
                try:
                    session.stop()
                except Exception as exc:
                    cleanup_error = exc

        if cleanup_error is not None and status == "pass":
            status = "fail"
            reason = {
                "code": "agent_drive_cleanup_failed",
                "message": f"agent terminal cleanup failed during {action}",
            }
        try:
            recording_complete = started and session.recording_complete(recording_path)
        except Exception:
            recording_complete = False

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
                "recording_complete": recording_complete,
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
            recording_complete=recording_complete,
        )
