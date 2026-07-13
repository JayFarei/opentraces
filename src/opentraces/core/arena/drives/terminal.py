"""The terminal drive: public CLI execution with complete per-action exhaust."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..box import Box, CrabboxRuntime
from ..run_store import RunDraft


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
        return json.loads(self.stdout)


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
        observed = self.runtime.exec(
            self.box,
            list(argv),
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
        return TerminalResult(
            argv=list(argv),
            returncode=observed.returncode,
            stdout=observed.stdout,
            stderr=observed.stderr,
            duration_ms=duration_ms,
            invocation_ref=invocation_ref,
            result_ref=result_ref,
        )
