"""Enlistment liveness pruning (#23 root cause, finished under #65).

``~/.opentraces/projects/`` accumulates leaked test-fixture enlistments
(``ot-h-*``, ``ot-otbox-*``, tmp-derived worlds run without an isolated HOME).
On the #65 machine 824 of 869 enlistments were leaks, multiplying every sweep's
surface. #23 named this and was closed without the prune ever being
implemented; this module is that prune.

Policy (conservative, two-stage):

* an enlistment whose ``project_dir`` is rooted under a tmp directory and
  whose state hasn't changed for ``TMP_STALE_DAYS`` is a leaked fixture —
  quarantine it;
* an enlistment whose ``project_dir`` no longer exists and whose state hasn't
  changed for ``MISSING_STALE_DAYS`` is dead — quarantine it;
* a missing-but-recently-active project (unmounted volume, mid-move) is left
  alone;
* quarantine = move to ``~/.opentraces/pruned_projects/<slug>`` (reversible);
  quarantined dirs older than ``QUARANTINE_DELETE_DAYS`` are deleted on later
  prune runs.

``OT_WATCHER_PRUNE=0`` disables pruning entirely.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from ..core import paths as _paths

logger = logging.getLogger("opentraces.watcher")

TMP_STALE_DAYS = 7
MISSING_STALE_DAYS = 14
QUARANTINE_DELETE_DAYS = 30

PRUNED_DIRNAME = "pruned_projects"


def _tmp_roots() -> list[Path]:
    roots = [Path("/tmp"), Path("/private/tmp"), Path("/private/var/folders"),
             Path("/var/folders")]
    env_tmp = os.environ.get("TMPDIR")
    if env_tmp:
        roots.append(Path(env_tmp))
    roots.append(Path(tempfile.gettempdir()))
    resolved = []
    for root in roots:
        try:
            resolved.append(root.resolve())
        except OSError:
            continue
    return resolved


def _is_tmp_rooted(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return any(
        resolved == root or root in resolved.parents for root in _tmp_roots()
    )


def _newest_mtime(slug_dir: Path) -> float:
    """Newest mtime across the enlistment dir's immediate entries.

    ``state.json`` is rewritten on every tick/ingest, so this is a faithful
    "last activity" signal without walking trace payloads recursively.
    """
    # NOTE: deliberately NOT seeded from slug_dir's own mtime — directory
    # mtime refreshes on ANY entry create/delete (including the watcher's
    # last_sweep_attempt marker below), which would shield stale leaks.
    # project.json/state.json mtimes are the real activity signal.
    newest = 0.0
    if not slug_dir.is_dir():
        return 0.0
    try:
        for entry in slug_dir.iterdir():
            # Soak finding (#65): last_sweep_attempt is WATCHER bookkeeping,
            # stamped on every sweep attempt regardless of outcome. Counting
            # it as activity resets the staleness clock each sweep, so a
            # leaked tmp enlistment would never reach TMP_STALE_DAYS.
            if entry.name == "last_sweep_attempt":
                continue
            try:
                newest = max(newest, entry.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return newest


def prune_enlistments(*, dry_run: bool = False) -> dict:
    """Quarantine dead/leaked enlistments; delete old quarantined entries.

    Returns a summary dict. Never raises: a prune failure must not take the
    sweep down with it.
    """
    summary = {
        "scanned": 0,
        "pruned_tmp": 0,
        "pruned_missing": 0,
        "kept": 0,
        "quarantine_deleted": 0,
        "errors": 0,
        "dry_run": bool(dry_run),
    }
    if os.environ.get("OT_WATCHER_PRUNE", "") == "0":
        summary["disabled"] = True
        return summary

    projects_dir = _paths.PROJECTS_DIR
    quarantine_dir = _paths.OPENTRACES_DIR / PRUNED_DIRNAME
    now = time.time()

    if projects_dir.is_dir():
        for slug_dir in sorted(projects_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            manifest = slug_dir / "project.json"
            if not manifest.is_file():
                continue
            summary["scanned"] += 1
            try:
                data = json.loads(manifest.read_text()) or {}
            except (OSError, json.JSONDecodeError):
                summary["errors"] += 1
                continue
            src = data.get("project_dir") or data.get("path")
            if not src:
                summary["kept"] += 1
                continue
            target = Path(src)
            age_days = (now - _newest_mtime(slug_dir)) / 86400.0
            verdict: str | None = None
            if _is_tmp_rooted(target) and age_days >= TMP_STALE_DAYS:
                verdict = "pruned_tmp"
            elif not target.is_dir() and age_days >= MISSING_STALE_DAYS:
                verdict = "pruned_missing"
            if verdict is None:
                summary["kept"] += 1
                continue
            summary[verdict] += 1
            if dry_run:
                continue
            try:
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                dest = quarantine_dir / slug_dir.name
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.move(str(slug_dir), str(dest))
                # Start the quarantine clock NOW: the moved dir keeps its old
                # mtime, and a 30-day-stale leak would otherwise be hard-deleted
                # by stage 2 in this same run — defeating the reversible window.
                os.utime(dest, None)
                logger.info("pruned enlistment %s (%s, idle %.0fd)",
                            slug_dir.name, verdict, age_days)
            except OSError:
                summary["errors"] += 1
                logger.warning("failed to prune %s", slug_dir, exc_info=True)

    # Stage 2: delete quarantined entries past the retention window.
    if not dry_run and quarantine_dir.is_dir():
        for entry in quarantine_dir.iterdir():
            try:
                if (now - entry.stat().st_mtime) / 86400.0 >= QUARANTINE_DELETE_DAYS:
                    shutil.rmtree(entry, ignore_errors=True)
                    summary["quarantine_deleted"] += 1
            except OSError:
                continue
    return summary
