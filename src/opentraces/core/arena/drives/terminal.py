"""The terminal drive: public CLI execution with complete per-action exhaust."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..box import Box, CrabboxRuntime
from ..diagnostics import sanitize_diagnostic_value
from ..run_store import RunDraft
from ..recording import RecordingConversionError, convert_script_cast
from .actions import ActionAllocation, RunActionSequence


@dataclass(frozen=True)
class TerminalResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    invocation_ref: str
    result_ref: str

    @property
    def json(self) -> Any:
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            for index, char in enumerate(self.stdout):
                if char not in "[{":
                    continue
                try:
                    value, _ = decoder.raw_decode(self.stdout[index:])
                    return value
                except json.JSONDecodeError:
                    continue
            raise


@dataclass(frozen=True)
class _PendingTerminalState:
    argv: tuple[str, ...]
    environment: dict[str, str]
    cwd: str | None
    timeout: float
    allocation: ActionAllocation
    action: str
    invocation_ref: str
    result_ref: str
    remote_timing: str
    remote_typescript: str
    recorded_argv: list[str]
    timing_path: Path


class TerminalAction:
    """One bounded terminal action that may overlap public browser work."""

    def __init__(
        self,
        *,
        drive: "TerminalDrive",
        state: _PendingTerminalState,
        future: Future[Any],
    ) -> None:
        self._drive = drive
        self._state = state
        self._future = future
        self._result: TerminalResult | None = None

    @property
    def running(self) -> bool:
        return self._result is None and not self._future.done()

    def wait(self, *, timeout: float | None = None) -> TerminalResult:
        """Wait a bounded interval, then freeze the action's complete exhaust."""

        if self._result is not None:
            return self._result
        self._result = self._drive._wait_for_action(self, timeout=timeout)
        return self._result


class TerminalDrive:
    """Execute commands through one box and freeze every observation."""

    def __init__(
        self,
        *,
        runtime: CrabboxRuntime,
        box: Box,
        draft: RunDraft,
        repository: Path,
        actions: RunActionSequence,
    ) -> None:
        self.runtime = runtime
        self.box = box
        self.draft = draft
        self.repository = repository
        self.actions = actions
        self._recording_channels: list[dict[str, Any]] = []
        self._markers: list[dict[str, Any]] = []
        self._pending: list[TerminalAction] = []

    @staticmethod
    def _env_pins(env: Mapping[str, str]) -> dict[str, str]:
        # Values can be credentials.  Record stable pins without freezing the
        # secret itself into the run.
        return {
            name: f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
            for name, value in sorted(env.items())
        }

    def exec(
        self,
        *argv: str,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        timeout: float = 60,
    ) -> TerminalResult:
        """Execute one terminal action synchronously through the shared primitive."""

        return self.start(*argv, env=env, cwd=cwd, timeout=timeout).wait(
            timeout=timeout + 5
        )

    def start(
        self,
        *argv: str,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        timeout: float = 60,
    ) -> TerminalAction:
        """Start a bounded command while preserving its run action allocation."""

        if not argv:
            raise ValueError("terminal.start requires at least one argv element")
        if timeout <= 0:
            raise ValueError("terminal timeout must be positive")
        allocation = self.actions.allocate("terminal")
        ordinal = allocation.ordinal
        action = f"actions/{ordinal:04d}"
        invocation_ref = f"{action}/invocation.json"
        result_ref = f"{action}/result.json"
        environment = dict(env or {})
        self.draft.write_json(
            invocation_ref,
            {
                "ordinal": ordinal,
                "argv": list(argv),
                "env_pins": self._env_pins(environment),
                "cwd": cwd or ".",
                "started_at": allocation.started_at,
            },
        )
        remote_base = f"bench-recordings/terminal-{ordinal:04d}"
        remote_timing = f"{remote_base}.timing"
        remote_typescript = f"{remote_base}.typescript"
        # Crabbox artifact globs are relative to its synced workdir.  Capture
        # that directory before honoring the requested command cwd, otherwise
        # `cwd=/tmp` silently strands the typescript under /tmp while collect()
        # looks in the repository workdir.
        recording_command = (
            'recording_root="$PWD/bench-recordings" && '
            'mkdir -p "$recording_root" && '
            f"cd {shlex.quote(cwd or '.')} && "
            "exec script -q --return "
            f'--log-timing "$recording_root/{Path(remote_timing).name}" '
            f'--log-out "$recording_root/{Path(remote_typescript).name}" '
            f"--command {shlex.quote(shlex.join(argv))}"
        )
        recorded_argv = [
            "sh",
            "-c",
            recording_command,
        ]
        timing_path = self.draft.path / action / "timing.json"
        state = _PendingTerminalState(
            argv=tuple(argv),
            environment=environment,
            cwd=cwd,
            timeout=timeout,
            allocation=allocation,
            action=action,
            invocation_ref=invocation_ref,
            result_ref=result_ref,
            remote_timing=remote_timing,
            remote_typescript=remote_typescript,
            recorded_argv=recorded_argv,
            timing_path=timing_path,
        )
        future: Future[Any] = Future()

        def execute() -> None:
            try:
                observed = self.runtime.exec_product(
                    self.box,
                    state.recorded_argv,
                    cwd=self.repository,
                    env=state.environment,
                    timeout=state.timeout,
                    timing_path=state.timing_path,
                )
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(observed)

        pending = TerminalAction(drive=self, state=state, future=future)
        self._pending.append(pending)
        threading.Thread(
            target=execute,
            name=f"opentraces-terminal-{ordinal:04d}",
            daemon=True,
        ).start()
        return pending

    def _wait_for_action(
        self,
        pending: TerminalAction,
        *,
        timeout: float | None,
    ) -> TerminalResult:
        state = pending._state
        wait_timeout = state.timeout + 5 if timeout is None else timeout
        if wait_timeout <= 0:
            raise ValueError("terminal wait timeout must be positive")
        try:
            observed = pending._future.result(timeout=wait_timeout)
        except FutureTimeoutError as exc:
            raise TimeoutError(
                f"terminal action did not complete within {wait_timeout:g} seconds"
            ) from exc
        except Exception as exc:
            duration_ms = self.actions.duration_ms(state.allocation)
            self.actions.complete(state.allocation)
            timeout_error = self._timeout_error(exc)
            stdout = self._exception_stream(exc, "stdout", "output")
            stderr = self._exception_stream(exc, "stderr")
            timing = self._read_partial_timing(state.timing_path)
            self.draft.write_text(f"{state.action}/stdout", stdout)
            self.draft.write_text(f"{state.action}/stderr", stderr)
            self.draft.write_json(f"{state.action}/timing.json", timing)
            if timeout_error is not None:
                reason = {
                    "code": "terminal_timeout",
                    "message": (
                        f"terminal command exceeded its {state.timeout:g} second timeout"
                    ),
                }
            else:
                reason = {
                    "code": "terminal_exec_error",
                    "message": sanitize_diagnostic_value(f"{type(exc).__name__}: {exc}"),
                }
            self.draft.write_json(
                state.result_ref,
                {
                    "execution_status": "error",
                    "returncode": None,
                    "duration_ms": duration_ms,
                    "stdout_ref": f"{state.action}/stdout",
                    "stderr_ref": f"{state.action}/stderr",
                    "timing_ref": f"{state.action}/timing.json",
                    "reason": reason,
                },
            )
            self._recording_channels.append(
                {
                    "kind": "terminal",
                    "complete": False,
                    "path": None,
                    "reason": reason["message"],
                }
            )
            self.draft.write_json("recordings/playlist.json", {"markers": self._markers})
            self._pending.remove(pending)
            raise
        duration_ms = self.actions.duration_ms(state.allocation)
        self.actions.complete(state.allocation)
        self.draft.write_text(f"{state.action}/stdout", observed.stdout)
        self.draft.write_text(f"{state.action}/stderr", observed.stderr)
        self.draft.write_json(f"{state.action}/timing.json", observed.timing)
        self.draft.write_json(
            state.result_ref,
            {
                "returncode": observed.returncode,
                "duration_ms": duration_ms,
                "stdout_ref": f"{state.action}/stdout",
                "stderr_ref": f"{state.action}/stderr",
                "timing_ref": f"{state.action}/timing.json",
            },
        )
        channel: dict[str, Any]
        collect = getattr(self.runtime, "collect", None)
        if collect is None:
            channel = {
                "kind": "terminal",
                "complete": False,
                "path": None,
                "reason": "cast collection unavailable",
            }
        else:
            ordinal = state.allocation.ordinal
            raw_destination = self.draft.path / "recordings" / "raw" / f"{ordinal:04d}"
            files = collect(
                self.box,
                [state.remote_timing, state.remote_typescript],
                destination=raw_destination,
                repository=self.repository,
            )
            timing_raw = files.get(Path(state.remote_timing).name)
            typescript_raw = files.get(Path(state.remote_typescript).name)
            if timing_raw is None or typescript_raw is None:
                channel = {
                    "kind": "terminal",
                    "complete": False,
                    "path": None,
                    "reason": "cast artifacts were not collected",
                }
            else:
                try:
                    cast = convert_script_cast(typescript_raw, timing_raw)
                    cast_ref = f"recordings/terminal-{ordinal:04d}.cast"
                    self.draft.write_bytes(cast_ref, cast)
                    channel = {
                        "kind": "terminal",
                        "complete": True,
                        "path": cast_ref,
                        "reason": None,
                    }
                    self._markers.append(
                        {
                            "ordinal": state.allocation.ordinal,
                            "label": " ".join(state.argv),
                            "cast_ref": cast_ref,
                            "duration_ms": duration_ms,
                        }
                    )
                except (OSError, RecordingConversionError) as exc:
                    channel = {
                        "kind": "terminal",
                        "complete": False,
                        "path": None,
                        "reason": f"cast conversion failed: {exc}",
                    }
        self._recording_channels.append(channel)
        self.draft.write_json("recordings/playlist.json", {"markers": self._markers})
        self._pending.remove(pending)
        return TerminalResult(
            argv=list(state.argv),
            returncode=observed.returncode,
            stdout=observed.stdout,
            stderr=observed.stderr,
            duration_ms=duration_ms,
            invocation_ref=state.invocation_ref,
            result_ref=state.result_ref,
        )

    @staticmethod
    def _exception_chain(exc: BaseException) -> list[BaseException]:
        chain: list[BaseException] = []
        current: BaseException | None = exc
        while current is not None and current not in chain:
            chain.append(current)
            current = current.__cause__ or current.__context__
        return chain

    @classmethod
    def _timeout_error(cls, exc: BaseException) -> subprocess.TimeoutExpired | None:
        for candidate in cls._exception_chain(exc):
            if isinstance(candidate, subprocess.TimeoutExpired):
                return candidate
        return None

    @classmethod
    def _exception_stream(cls, exc: BaseException, *names: str) -> str:
        for candidate in cls._exception_chain(exc):
            for name in names:
                value = getattr(candidate, name, None)
                if value is None:
                    continue
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return str(value)
        return ""

    @staticmethod
    def _read_partial_timing(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"invalid": True}
        sanitized = sanitize_diagnostic_value(value)
        return dict(sanitized) if isinstance(sanitized, Mapping) else {"invalid": True}

    def recording_summary(self) -> dict[str, Any]:
        if not self._recording_channels:
            return {
                "rewatchable": False,
                "channels": [
                    {
                        "kind": "terminal",
                        "complete": False,
                        "path": None,
                        "reason": "no terminal action was recorded",
                    }
                ],
            }
        complete = all(channel["complete"] for channel in self._recording_channels)
        if complete:
            return {
                "rewatchable": True,
                "channels": [
                    {
                        "kind": "terminal",
                        "complete": True,
                        "path": "recordings/playlist.json",
                        "reason": None,
                        "casts": self._markers,
                    }
                ],
            }
        reasons = [channel["reason"] for channel in self._recording_channels if channel["reason"]]
        return {
            "rewatchable": False,
            "channels": [
                {
                    "kind": "terminal",
                    "complete": False,
                    "path": None,
                    "reason": "; ".join(reasons),
                }
            ],
        }

    @property
    def has_actions(self) -> bool:
        return bool(self._recording_channels)
