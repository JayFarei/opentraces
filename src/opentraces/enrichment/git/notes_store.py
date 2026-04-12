"""Wrapper around `git notes --ref=opentraces` with append-only semantics.

Notes format (one per line):
    opentraces:<external_id> [<shared_url>]

Append-only means: never clobber an existing note. Collaborators can
append their own links to the same commit and all survive.
"""

from __future__ import annotations

from pathlib import Path

from .._shared import run_git

NOTES_REF_NAME = "opentraces"
NOTES_REF = f"refs/notes/{NOTES_REF_NAME}"


def _git(args: list[str], cwd: Path | None) -> tuple[int, str]:
    try:
        code, out, _ = run_git(args, cwd)
        return code, out
    except Exception:
        return (-1, "")


def read(revision: str, cwd: Path | None = None) -> list[str]:
    """Return the existing note lines on `revision`, or [] if none."""
    code, out = _git(["notes", f"--ref={NOTES_REF_NAME}", "show", revision], cwd)
    if code != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


def append(revision: str, new_lines: list[str], cwd: Path | None = None) -> int:
    """Append `new_lines` to `revision`'s note, skipping duplicates.

    Dedups against the existing note then writes all surviving lines
    in a single `git notes append` invocation (one subprocess per
    call instead of one per line). Returns the count written.
    """
    existing = set(read(revision, cwd))
    to_write = []
    for line in new_lines:
        line = line.strip()
        if line and line not in existing and line not in to_write:
            to_write.append(line)
    if not to_write:
        return 0
    code, _ = _git(
        ["notes", f"--ref={NOTES_REF_NAME}", "append",
         "-m", "\n".join(to_write), revision],
        cwd,
    )
    return len(to_write) if code == 0 else 0


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
