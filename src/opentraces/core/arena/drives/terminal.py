"""The terminal drive: public CLI execution with complete per-action exhaust."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..box import Box, CrabboxRuntime
from ..diagnostics import sanitize_diagnostic_value
from ..run_store import RunDraft
from ..recording import RecordingConversionError, convert_script_cast
from .actions import RunActionSequence


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
        if not argv:
            raise ValueError("terminal.exec requires at least one argv element")
        allocation = self.actions.allocate()
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
        try:
            observed = self.runtime.exec_product(
                self.box,
                recorded_argv,
                cwd=self.repository,
                env=environment,
                timeout=timeout,
                timing_path=timing_path,
            )
        except Exception as exc:
            duration_ms = self.actions.duration_ms(allocation)
            timeout_error = self._timeout_error(exc)
            stdout = self._exception_stream(exc, "stdout", "output")
            stderr = self._exception_stream(exc, "stderr")
            timing = self._read_partial_timing(timing_path)
            self.draft.write_text(f"{action}/stdout", stdout)
            self.draft.write_text(f"{action}/stderr", stderr)
            self.draft.write_json(f"{action}/timing.json", timing)
            if timeout_error is not None:
                reason = {
                    "code": "terminal_timeout",
                    "message": f"terminal command exceeded its {timeout:g} second timeout",
                }
            else:
                reason = {
                    "code": "terminal_exec_error",
                    "message": sanitize_diagnostic_value(f"{type(exc).__name__}: {exc}"),
                }
            self.draft.write_json(
                result_ref,
                {
                    "execution_status": "error",
                    "returncode": None,
                    "duration_ms": duration_ms,
                    "stdout_ref": f"{action}/stdout",
                    "stderr_ref": f"{action}/stderr",
                    "timing_ref": f"{action}/timing.json",
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
            raise
        duration_ms = self.actions.duration_ms(allocation)
        self.draft.write_text(f"{action}/stdout", observed.stdout)
        self.draft.write_text(f"{action}/stderr", observed.stderr)
        self.draft.write_json(f"{action}/timing.json", observed.timing)
        self.draft.write_json(
            result_ref,
            {
                "returncode": observed.returncode,
                "duration_ms": duration_ms,
                "stdout_ref": f"{action}/stdout",
                "stderr_ref": f"{action}/stderr",
                "timing_ref": f"{action}/timing.json",
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
            raw_destination = self.draft.path / "recordings" / "raw" / f"{ordinal:04d}"
            files = collect(
                self.box,
                [remote_timing, remote_typescript],
                destination=raw_destination,
                repository=self.repository,
            )
            timing_raw = files.get(Path(remote_timing).name)
            typescript_raw = files.get(Path(remote_typescript).name)
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
                            "ordinal": ordinal,
                            "label": " ".join(argv),
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
        return TerminalResult(
            argv=list(argv),
            returncode=observed.returncode,
            stdout=observed.stdout,
            stderr=observed.stderr,
            duration_ms=duration_ms,
            invocation_ref=invocation_ref,
            result_ref=result_ref,
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
                "timeline_ref": "recordings/playlist.json",
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
            "timeline_ref": "recordings/playlist.json",
        }

    @property
    def has_actions(self) -> bool:
        return bool(self._recording_channels)
