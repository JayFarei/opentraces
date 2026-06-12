"""otbox gc — sweep killed-run residue under ``.otbox/`` (issue #53).

In-run teardown (checkpoint failure, matrix ``finally``) covers paths a
process can clean itself; this sweep covers what a *killed* run leaves
behind: meta-less box stubs (invisible to ``otbox down --all``, which
filters on ``meta.json``), dead-owner boxes, and aged
``_capture-refresh-*`` snapshot archives.

Keep rules (asymmetric by design — guard failures keep garbage, they
never delete a live box):

* the current-pointer box is never touched;
* a box whose meta ``owner_pid`` is alive is kept;
* anything younger than the age grace is kept (closes the race with a
  concurrent run that created the dir but hasn't written meta yet, and
  the mid-restore window where the extracted archive transiently
  carries the origin box's dead-pid meta);
* ``_checkpoint-*`` snapshots (the content-addressed cache) and
  user-named snapshots are never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path


def pid_alive(pid: int) -> bool:
    """Best-effort liveness probe — errs toward 'alive' (keep)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except (OverflowError, ValueError, OSError):
        return True  # unprobeable → keep
    return True


def entry_age_hours(path: Path, meta_created: str | None = None) -> float:
    """Age in hours from the *newest* of dir mtime and meta ``created``."""
    try:
        newest = path.stat().st_mtime
    except OSError:
        return 0.0  # vanished mid-sweep → treat as fresh (keep)
    if meta_created:
        try:
            newest = max(newest, datetime.fromisoformat(meta_created).timestamp())
        except ValueError:
            pass
    return max(0.0, (time.time() - newest) / 3600.0)


def sweep(
    *,
    boxes_dir: Path,
    snapshots_dir: Path,
    current_box_id: str | None,
    min_age_hours: float = 1.0,
    dry_run: bool = False,
) -> dict:
    """Sweep dead-run boxes + aged ``_capture-refresh-*`` snapshots.

    Iterates *raw* directory entries (not ``list_box_ids()``), so
    meta-less crash stubs are visible. Returns a report dict:
    ``{removed_boxes, removed_snapshots, kept, dry_run}``.
    """
    removed_boxes: list[str] = []
    removed_snapshots: list[str] = []
    kept: list[dict] = []

    if boxes_dir.exists():
        for entry in sorted(boxes_dir.iterdir()):
            if not entry.is_dir():
                continue
            box_id = entry.name
            if current_box_id is not None and box_id == current_box_id:
                kept.append({"box_id": box_id, "reason": "current box"})
                continue
            owner_pid: int | None = None
            created: str | None = None
            meta = entry / "meta.json"
            if meta.exists():
                try:
                    data = json.loads(meta.read_text())
                    raw_pid = data.get("owner_pid")
                    owner_pid = int(raw_pid) if raw_pid is not None else None
                    created = data.get("created")
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            if owner_pid is not None and pid_alive(owner_pid):
                kept.append(
                    {"box_id": box_id, "reason": f"owner pid {owner_pid} alive"}
                )
                continue
            age = entry_age_hours(entry, created)
            if age < min_age_hours:
                kept.append(
                    {
                        "box_id": box_id,
                        "reason": (
                            f"younger than grace "
                            f"({age:.2f}h < {min_age_hours:g}h)"
                        ),
                    }
                )
                continue
            removed_boxes.append(box_id)
            if not dry_run:
                shutil.rmtree(entry, ignore_errors=True)

    if snapshots_dir.exists():
        for snap in sorted(snapshots_dir.iterdir()):
            if not snap.name.startswith("_capture-refresh-"):
                continue  # never touch _checkpoint-* or user-named snapshots
            if entry_age_hours(snap) < min_age_hours:
                continue
            removed_snapshots.append(snap.name)
            if not dry_run:
                try:
                    snap.unlink()
                except OSError:
                    pass

    return {
        "removed_boxes": removed_boxes,
        "removed_snapshots": removed_snapshots,
        "kept": kept,
        "dry_run": dry_run,
    }
