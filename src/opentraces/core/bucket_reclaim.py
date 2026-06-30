"""Dry-run-default janitor for leaked Trace Trails cruft under ``.git/**/opentraces/``.

The Trace Trails accelerators (the event-log snapshot pickle and the event
index pickle) live under the COMMON git dir at
``<git-common-dir>/opentraces/``. Two kinds of cruft accumulate there:

* **Leaked ``*.tmp`` files** — the atomic-write temp companions
  (``event_log_snapshot.pkl.tmp``, ``event_index/base.pkl.tmp``, and the
  random ``tmp*.tmp`` the verify-watermark / survival-cache writers create).
  A hard kill between the temp write and the ``os.replace`` strands one.
* **Orphan / duplicate accelerator pickles** — before #169 C the snapshot and
  index were written per-LINKED-WORKTREE (``.git/worktrees/<wt>/opentraces/``,
  the "22-copy duplication bug"). The fix moved them to the common git dir, so
  the per-worktree copies are now dead weight.

This janitor lists those candidates with per-file byte counts. It is
**dry-run by default**: a plain run deletes nothing and leaves the file set
byte-for-byte unchanged. Destructive removal requires ``apply=True`` and is
hard-fenced to NEVER touch:

* the LIVE ``<git-common-dir>/opentraces/event_log_snapshot.pkl``,
* the LIVE ``<git-common-dir>/opentraces/event_index/base.pkl``,
* the canonical event ref (refs are never filesystem-walked here), or
* anything under the bucket (only paths *inside the git dir* are scanned).

Non-cruft accelerator state (``event_log_verified.json``,
``survival_cache.json``) is out of scope and never listed or removed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SNAPSHOT_NAME = "event_log_snapshot.pkl"
INDEX_DIR_NAME = "event_index"
INDEX_BASE_NAME = "base.pkl"

# Candidate categories.
CATEGORY_LEAKED_TMP = "leaked_tmp"
CATEGORY_ORPHAN_SNAPSHOT = "orphan_snapshot"
CATEGORY_ORPHAN_INDEX_PICKLE = "orphan_index_pickle"


@dataclass
class Candidate:
    path: Path
    size_bytes: int
    category: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "bytes": self.size_bytes,
            "category": self.category,
        }


@dataclass
class ReclaimReport:
    repo: Path
    git_common_dir: Path | None
    live_keepers: list[Path] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    applied: bool = False
    removed: list[Candidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(c.size_bytes for c in self.candidates)

    @property
    def removed_bytes(self) -> int:
        return sum(c.size_bytes for c in self.removed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": str(self.repo),
            "git_common_dir": str(self.git_common_dir) if self.git_common_dir else None,
            "live_keepers": [str(p) for p in self.live_keepers],
            "candidates": [c.as_dict() for c in self.candidates],
            "candidate_count": len(self.candidates),
            "total_bytes": self.total_bytes,
            "applied": self.applied,
            "removed": [c.as_dict() for c in self.removed],
            "removed_count": len(self.removed),
            "removed_bytes": self.removed_bytes,
            "errors": list(self.errors),
        }


def _git_common_dir(repo: Path) -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    git_dir = Path(raw)
    if not git_dir.is_absolute():
        git_dir = (Path(repo) / git_dir).resolve()
    return git_dir.resolve()


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _classify(path: Path, *, live_snapshot: Path, live_base: Path) -> str | None:
    """Return the cruft category for ``path``, or ``None`` if it must be kept.

    LIVE keepers are excluded FIRST so the live snapshot / index pickle are
    never classified as cruft even though they match the name patterns.
    """
    resolved = path.resolve()
    if resolved == live_snapshot or resolved == live_base:
        return None
    name = path.name
    if name.endswith(".tmp"):
        return CATEGORY_LEAKED_TMP
    if name == SNAPSHOT_NAME:
        # The live snapshot was already excluded above; any other copy is orphan.
        return CATEGORY_ORPHAN_SNAPSHOT
    if name.endswith(".pkl") and path.parent.name == INDEX_DIR_NAME:
        # The live base.pkl was already excluded above; any other index pickle
        # (per-worktree base.pkl, a stale rename) is orphan.
        return CATEGORY_ORPHAN_INDEX_PICKLE
    return None


def scan_repo(repo: Path) -> ReclaimReport:
    """Scan one repo's git dir for reclaimable cruft. Never mutates anything."""
    repo = Path(repo).resolve()
    common = _git_common_dir(repo)
    report = ReclaimReport(repo=repo, git_common_dir=common)
    if common is None:
        report.errors.append(f"not a git repository (no --git-common-dir): {repo}")
        return report

    live_snapshot = (common / "opentraces" / SNAPSHOT_NAME).resolve()
    live_base = (common / "opentraces" / INDEX_DIR_NAME / INDEX_BASE_NAME).resolve()
    report.live_keepers = [live_snapshot, live_base]

    # Every ``opentraces`` directory anywhere inside the git dir — this catches
    # the live ``<common>/opentraces`` AND every per-worktree
    # ``<common>/worktrees/<wt>/opentraces``. We never leave the git dir, so the
    # bucket (under ~/.opentraces) is structurally out of reach.
    seen: set[Path] = set()
    candidates: list[Candidate] = []
    for ot_dir in sorted(common.rglob("opentraces")):
        if not ot_dir.is_dir():
            continue
        for entry in sorted(ot_dir.rglob("*")):
            if not entry.is_file():
                continue
            resolved = entry.resolve()
            if resolved in seen:
                continue
            category = _classify(entry, live_snapshot=live_snapshot, live_base=live_base)
            if category is None:
                continue
            seen.add(resolved)
            candidates.append(Candidate(path=entry, size_bytes=_safe_size(entry), category=category))

    report.candidates = sorted(candidates, key=lambda c: str(c.path))
    return report


def reclaim_repo(repo: Path, *, apply: bool = False) -> ReclaimReport:
    """Scan ``repo`` and, when ``apply`` is set, remove the candidate cruft.

    Default (``apply=False``) is print-only: the on-disk file set is unchanged.
    The live snapshot + index pickle are protected by :func:`_classify`, so even
    an ``apply`` run can never delete them.
    """
    report = scan_repo(repo)
    if not apply:
        return report

    report.applied = True
    # Re-derive the protected set so a delete can never hit a live keeper even if
    # the candidate list were tampered with between scan and apply.
    live = {p.resolve() for p in report.live_keepers}
    for candidate in report.candidates:
        resolved = candidate.path.resolve()
        if resolved in live:  # belt-and-suspenders: never delete a live keeper
            continue
        try:
            candidate.path.unlink()
            report.removed.append(candidate)
        except OSError as exc:
            report.errors.append(f"failed to remove {candidate.path}: {exc}")
    return report
