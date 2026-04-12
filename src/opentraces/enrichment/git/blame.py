"""`opentraces blame` implementation (plan 041 R30).

Walks `git blame` → revision → `refs/notes/opentraces` for that
revision → load each linked trace → find the attribution range
that covers the requested line → return (trace_id, step_N, alive).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from opentraces_schema import TraceRecord

from . import liveness, notes_store


@dataclass
class BlameHit:
    trace_id: str
    step: int | None
    revision: str
    file_path: str
    line: int
    content_alive: bool


def _blame_revision(file_path: str, line: int, cwd: Path) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "blame", "-L", f"{line},{line}", "--porcelain", "--", file_path],
            cwd=str(cwd), text=True,
            stderr=subprocess.DEVNULL, timeout=10,
        )
    except Exception:
        return None
    # First token on the first line is the commit sha.
    first = out.splitlines()
    if not first:
        return None
    parts = first[0].split()
    if not parts:
        return None
    return parts[0]


def _find_step(trace: TraceRecord, file_path: str, line: int) -> int | None:
    """Locate the step whose attribution range covers `line` in `file_path`."""
    if trace.attribution is None:
        return None
    for f in trace.attribution.files:
        # Path suffix match (Edit paths may be absolute, hunks are repo-relative).
        if not (f.path == file_path or f.path.endswith("/" + file_path)
                or file_path.endswith("/" + f.path)):
            continue
        for conv in f.conversations:
            for rng in conv.ranges:
                if rng.start_line <= line <= rng.end_line:
                    # AttributionConversation.url is
                    # `opentraces://<trace_id>/step_<N>`; parse the step.
                    url = conv.url or ""
                    if "/step_" in url:
                        try:
                            return int(url.rsplit("/step_", 1)[1])
                        except ValueError:
                            return None
    return None


def blame(
    file_path: str,
    line: int,
    traces: dict[str, TraceRecord] | None,
    cwd: Path,
) -> list[BlameHit]:
    """Resolve which opentraces trace authored `file_path:line` at HEAD.

    `traces` is a mapping from trace_id to loaded TraceRecord, used to
    look up attribution + step index for each trace id attached to the
    blame's commit via refs/notes/opentraces.
    """
    revision = _blame_revision(file_path, line, cwd)
    if revision is None:
        return []
    note_lines = notes_store.read(revision, cwd)
    hits: list[BlameHit] = []
    for note_line in note_lines:
        parsed = notes_store.parse_link(note_line)
        if not parsed:
            continue
        trace_id = parsed[0]
        trace = (traces or {}).get(trace_id)
        step = _find_step(trace, file_path, line) if trace else None
        alive = False
        if trace and trace.attribution:
            alive = liveness.content_alive(trace.attribution, "HEAD", cwd)
        hits.append(BlameHit(
            trace_id=trace_id,
            step=step,
            revision=revision,
            file_path=file_path,
            line=line,
            content_alive=alive,
        ))
    return hits
