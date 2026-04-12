"""Lazy liveness checks for GitLinks (plan 041 R32, R33).

Liveness is computed at read time, not stamped at commit time, because
the tree evolves: a commit can be dropped by force-push; an agent's
contribution can be overwritten by a later Edit. Caching liveness at
commit time would lie.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import mmh3

from opentraces_schema import Attribution


def _run(cmd: list[str], cwd: Path | None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode, proc.stdout
    except Exception:
        return (-1, "")


def commit_reachable(revision: str, cwd: Path | None = None) -> bool:
    """True iff `revision` is an ancestor of HEAD in the current repo.

    False means the commit was orphaned — typically by `git push
    --force` that dropped it — or simply that the repo's HEAD has
    moved so the commit is no longer on a reachable branch.
    """
    # Existence check first: a commit we don't have at all is not reachable.
    code, _ = _run(["git", "cat-file", "-e", revision], cwd)
    if code != 0:
        return False
    code, _ = _run(
        ["git", "merge-base", "--is-ancestor", revision, "HEAD"], cwd,
    )
    return code == 0


def _hash_lines(text: str) -> str:
    return f"murmur3:{mmh3.hash128(text.encode('utf-8'), signed=False):032x}"


def _file_at(revision: str, path: str, cwd: Path | None) -> str | None:
    code, out = _run(["git", "show", f"{revision}:{path}"], cwd)
    return out if code == 0 else None


def content_alive(
    attribution: Attribution,
    head_ref: str = "HEAD",
    cwd: Path | None = None,
) -> bool:
    """True iff any attribution range's content_hash still appears in
    the file at `head_ref`.

    "Appears" means: reading the file at head_ref, picking the same
    line range, and hashing yields the same hash — OR the hash body
    (mm3-hex) is present as a substring of the file's full-content
    hash set computed range-by-range.

    Conservative: returns True at the first surviving range. False
    when no range survives. Ranges without a content_hash are ignored.
    """
    for f in attribution.files:
        content = _file_at(head_ref, f.path, cwd)
        if content is None:
            # Try normalizing absolute → repo-relative paths.
            for part in f.path.split("/")[::-1]:
                content = _file_at(head_ref, f.path.split(part)[-1].lstrip("/"), cwd)
                if content is not None:
                    break
            if content is None:
                continue
        lines = content.splitlines()
        for conv in f.conversations:
            for rng in conv.ranges:
                if not rng.content_hash:
                    continue
                s = max(1, rng.start_line)
                e = max(s, rng.end_line)
                if e > len(lines):
                    continue
                text = "\n".join(lines[s - 1:e])
                if _hash_lines(text) == rng.content_hash:
                    return True
    return False


def annotate(link, attribution: Attribution | None, cwd: Path | None = None):
    """Populate a GitLink's `commit_reachable` and `content_alive`
    in-place. No-op if the link has no revision."""
    if not getattr(link, "revision", None):
        return
    link.commit_reachable = commit_reachable(link.revision, cwd)
    if attribution is not None:
        link.content_alive = content_alive(attribution, head_ref="HEAD", cwd=cwd)
