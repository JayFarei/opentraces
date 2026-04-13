"""Commit-mode blame helpers.

`opentraces blame <commit>` resolves a git ref to the opentraces traces
attached via `refs/notes/opentraces`. The CLI wrapper joins each hit with
staging records to display intent, session_id, and the resume command;
everything trace-scoped happens here.
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


def blame_commit(ref: str, cwd: Path) -> tuple[str, list[CommitBlameHit]]:
    """Resolve a ref to the opentraces notes attached to its commit.

    Returns (full_sha_or_ref, hits). The sha falls back to the raw ref if
    git rev-parse fails so callers can still render something.
    """
    full_sha = resolve_sha(ref, cwd) or ref
    lines = notes_store.read(full_sha, cwd)
    parsed = [p for p in (notes_store.parse_link(l) for l in lines) if p]
    return full_sha, [CommitBlameHit(trace_id=tid, url=url) for tid, url in parsed]
