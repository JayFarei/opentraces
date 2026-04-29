"""Commit-mode blame helpers.

`opentraces trail blame <commit>` resolves a git ref to the opentraces traces
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


def diff_line_set(cwd: Path, sha: str) -> dict[str, set[int]]:
    """Return ``{post_image_path: {new_file_line_numbers}}`` for the commit.

    The per-path-per-line version of :func:`diff_line_count`. Produces
    the exact set of new-file lines the commit introduced, so callers
    can intersect with a per-file, per-line attribution map and derive
    "of the lines this commit added/modified, how many are attributed
    to trace X." The intersection sums ≤ ``diff_line_count`` by
    construction, which keeps per-trace percentages honest (no more
    whole-file/diff-denominator mismatches).

    Implementation: ``git diff --unified=0 <sha>^..<sha>`` emits hunk
    headers ``@@ -<old> +<new_start>,<new_count> @@`` with zero
    context. We scan for ``+++ b/<path>`` lines to track the file,
    then each ``@@`` adds ``range(new_start, new_start + new_count)``
    to the path's set. No parsing of hunk bodies needed.

    Edge cases handled:
    - Binary files: git emits ``Binary files differ``; no ``@@`` lines,
      so the path is not added to the result. Correct.
    - Deletion-only hunks (``new_count == 0``): empty range; skipped.
    - Merge commits: ``<sha>^..<sha>`` resolves to the first parent,
      matching :func:`diff_line_count`'s implicit first-parent choice.
    - Renames: ``+++ b/<newpath>`` is used, so the destination path
      carries the attribution set. The old path is absent (no new-file
      content survives under its name).

    Returns ``{}`` for: initial commits (no parent), unknown SHAs, any
    subprocess failure. Callers treat empty as "denominator unknown".
    """
    try:
        out = subprocess.run(
            ["git", "diff", "--unified=0", f"{sha}^..{sha}"],
            cwd=cwd, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return {}
    if out.returncode != 0:
        return {}

    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in out.stdout.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            if current == "/dev/null":
                current = None
            else:
                result.setdefault(current, set())
        elif line.startswith("@@ ") and current is not None:
            # ``@@ -<old>[,<count>] +<new_start>[,<new_count>] @@ ...``
            plus_token = None
            for part in line.split(" "):
                if part.startswith("+"):
                    plus_token = part[1:]
                    break
            if not plus_token:
                continue
            try:
                if "," in plus_token:
                    start_str, count_str = plus_token.split(",", 1)
                    start = int(start_str)
                    count = int(count_str)
                else:
                    start = int(plus_token)
                    count = 1
            except ValueError:
                continue
            if count <= 0:
                continue
            for n in range(start, start + count):
                result[current].add(n)
    # Drop empty buckets (pure deletion into a path never touched otherwise).
    return {p: lines for p, lines in result.items() if lines}


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
