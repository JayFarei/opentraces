"""The terminal drive: public CLI execution with complete per-action exhaust."""

from __future__ import annotations

import hashlib
import json
import shlex
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..box import Box, CrabboxRuntime
from ..run_store import RunDraft
from ..recording import RecordingConversionError, convert_script_cast


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    ) -> None:
        self.runtime = runtime
        self.box = box
        self.draft = draft
        self.repository = repository
        self._ordinal = 0
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
        self._ordinal += 1
        action = f"actions/{self._ordinal:04d}"
        invocation_ref = f"{action}/invocation.json"
        result_ref = f"{action}/result.json"
        environment = dict(env or {})
        started_at = _utc_now()
        self.draft.write_json(
            invocation_ref,
            {
                "ordinal": self._ordinal,
                "argv": list(argv),
                "env_pins": self._env_pins(environment),
                "cwd": cwd or ".",
                "started_at": started_at,
            },
        )
        started = time.monotonic()
        remote_base = f"bench-recordings/terminal-{self._ordinal:04d}"
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
            f"--log-timing \"$recording_root/{Path(remote_timing).name}\" "
            f"--log-out \"$recording_root/{Path(remote_typescript).name}\" "
            f"--command {shlex.quote(shlex.join(argv))}"
        )
        recorded_argv = [
            "sh",
            "-c",
            recording_command,
        ]
        observed = self.runtime.exec(
            self.box,
            recorded_argv,
            cwd=self.repository,
            env=environment,
            timeout=timeout,
            timing_path=self.draft.path / action / "timing.json",
        )
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
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
            raw_destination = self.draft.path / "recordings" / "raw" / f"{self._ordinal:04d}"
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
                    cast_ref = f"recordings/terminal-{self._ordinal:04d}.cast"
                    self.draft.write_bytes(cast_ref, cast)
                    channel = {
                        "kind": "terminal",
                        "complete": True,
                        "path": cast_ref,
                        "reason": None,
                    }
                    self._markers.append(
                        {
                            "ordinal": self._ordinal,
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
