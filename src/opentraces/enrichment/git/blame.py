"""Commit-mode blame helpers.

`opentraces blame <commit>` resolves a git ref to the opentraces traces
attached via `refs/notes/opentraces`. The CLI wrapper joins each hit with
staging records to display the task label, session_id, and the resume
command; everything trace-scoped happens here.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import notes_store


_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def looks_like_sha(s: str) -> bool:
    return bool(_SHA_RE.match(s.strip()))


def resolve_sha(ref: str, cwd: Path) -> str | None:
    """Resolve any git ref (HEAD, branch, short sha) to a full commit SHA."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=cwd, capture_output=True, text=True, check=False,
        )
        return res.stdout.strip() if res.returncode == 0 else None
    except FileNotFoundError:
        return None


@dataclass
class CommitBlameHit:
    trace_id: str
    url: str | None


def diff_line_count(cwd: Path, sha: str) -> int:
    """Count of added (insertion) lines in the commit's diff.

    Uses ``git show --numstat``. Binary files (shown as ``-\t-``) are
    excluded. Merge commits with no numstat, unreachable shas, or any
    failure return 0 — callers treat 0 as "denominator unknown" and
    fall back to whole-file blame.

    Insertion-only denominator is deliberate: modifications produce an
    add + a delete in numstat, and blaming counts the inserted line
    once, so dividing by (adds+deletes) would artificially halve
    coverage for every modified line.
    """
    try:
        out = subprocess.run(
            ["git", "show", "--numstat", "--format=", sha],
            cwd=cwd, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return 0
    if out.returncode != 0:
        return 0
    total = 0
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        added = parts[0].strip()
        if not added or added == "-":  # blank line or binary
            continue
        try:
            total += int(added)
        except ValueError:
            continue
    return total


def blame_commit(ref: str, cwd: Path) -> tuple[str, list[CommitBlameHit]]:
    """Resolve a ref to the opentraces notes attached to its commit.

    Returns (full_sha_or_ref, hits). The sha falls back to the raw ref if
    git rev-parse fails so callers can still render something.
    """
    full_sha = resolve_sha(ref, cwd) or ref
    lines = notes_store.read(full_sha, cwd)
    parsed = [p for p in (notes_store.parse_link(l) for l in lines) if p]
    return full_sha, [CommitBlameHit(trace_id=tid, url=url) for tid, url in parsed]
