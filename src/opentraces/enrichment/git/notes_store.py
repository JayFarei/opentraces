"""Wrapper around `git notes --ref=opentraces` with append-only semantics.

Notes format (one per line):
    opentraces:<external_id> [<shared_url>]

Append-only means: never clobber an existing note. Collaborators can
append their own links to the same commit and all survive.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

NOTES_REF = "refs/notes/opentraces"
_NOTES_REF_SHORT = "opentraces"


def _run(cmd: list[str], cwd: Path | None) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def read(revision: str, cwd: Path | None = None) -> list[str]:
    """Return the existing note lines on `revision`, or [] if none."""
    code, out, _ = _run(
        ["git", "notes", "--ref=" + _NOTES_REF_SHORT, "show", revision],
        cwd=cwd,
    )
    if code != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


def append(revision: str, new_lines: list[str], cwd: Path | None = None) -> int:
    """Append `new_lines` to `revision`'s note, skipping duplicates.

    Returns the count of lines actually written.
    """
    existing = set(read(revision, cwd))
    added = 0
    for line in new_lines:
        line = line.strip()
        if not line or line in existing:
            continue
        code, _, err = _run(
            ["git", "notes", "--ref=" + _NOTES_REF_SHORT, "append", "-m", line, revision],
            cwd=cwd,
        )
        if code == 0:
            existing.add(line)
            added += 1
        else:
            # Don't raise: note addition is best-effort and never
            # blocks the post-commit hook.
            pass
    return added


def format_link(trace_id: str, shared_url: str | None = None) -> str:
    """Format a single notes line: `opentraces:<id>[ <url>]`."""
    if shared_url:
        return f"opentraces:{trace_id} {shared_url}"
    return f"opentraces:{trace_id}"


def parse_link(line: str) -> tuple[str, str | None] | None:
    """Parse a single notes line into (trace_id, url | None) or None."""
    line = line.strip()
    if not line.startswith("opentraces:"):
        return None
    rest = line[len("opentraces:"):].strip()
    if not rest:
        return None
    parts = rest.split(None, 1)
    trace_id = parts[0]
    url = parts[1] if len(parts) > 1 else None
    return (trace_id, url)
