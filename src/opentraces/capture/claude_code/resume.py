"""Resume a Claude Code session from a trace id.

Adapter-specific: produces the `claude --resume <session_id>` invocation.
The CLI is responsible for printing or exec'ing; this module just resolves
trace_id prefix → session_id → argv.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ResumeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ResumeTarget:
    trace_id: str
    session_id: str
    argv: list[str]

    @property
    def command(self) -> str:
        return " ".join(self.argv)


def resolve(trace_id_prefix: str, staging: Path) -> ResumeTarget:
    """Look up the Claude session_id behind a trace id (or short prefix).

    Raises ResumeError with codes:
      - NO_MATCH        : no trace matched the prefix
      - AMBIGUOUS       : multiple traces matched; caller should render options
      - NO_SESSION      : trace has no session_id (not a Claude Code session)
    """
    from ...core.inbox import load_trace_records

    traces = list(load_trace_records(staging))
    matches = [t for t in traces if t.trace_id.startswith(trace_id_prefix)]
    if not matches:
        raise ResumeError("NO_MATCH", f"no trace matches '{trace_id_prefix}'")
    if len(matches) > 1:
        ids = ", ".join(t.trace_id for t in matches[:5])
        raise ResumeError(
            "AMBIGUOUS",
            f"'{trace_id_prefix}' is ambiguous ({len(matches)} matches): {ids}",
        )

    rec = matches[0]
    session_id = getattr(rec, "session_id", None)
    if not session_id:
        raise ResumeError(
            "NO_SESSION",
            f"trace {rec.trace_id} has no session_id recorded.",
        )

    return ResumeTarget(
        trace_id=rec.trace_id,
        session_id=session_id,
        argv=["claude", "--resume", session_id],
    )
