"""Snapshot / restore — portable workspace archives (spec R2).

A snapshot is a ``.tar.gz`` of an entire box: the isolated HOME (incl.
``~/.opentraces``), the seeded project (incl. its ``.git``), the
fake-remote root, and logs. Restore extracts into a *fresh* box and
rewrites the handful of absolute paths opentraces bakes into
``~/.opentraces`` JSON so the restored world is internally consistent at
its new location.

Mirrors crabbox's ``workspace-archive`` checkpoint tier: portable,
offline, deterministic, substrate-agnostic.
"""

from __future__ import annotations

import json
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from .env import (
    SNAPSHOTS_DIR,
    Box,
    ensure_state_root,
    new_box_id,
    utc_now,
)


class SnapshotError(Exception):
    pass


@dataclass
class SnapshotInfo:
    name: str
    archive: Path
    origin_box_id: str
    origin_root: str
    seed: str | None
    driver: str
    created: str
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "archive": str(self.archive),
            "origin_box_id": self.origin_box_id,
            "origin_root": self.origin_root,
            "seed": self.seed,
            "driver": self.driver,
            "created": self.created,
            "size_bytes": self.size_bytes,
        }


def _archive_path(name: str) -> Path:
    return SNAPSHOTS_DIR / f"{name}.tar.gz"


def _meta_path(name: str) -> Path:
    return SNAPSHOTS_DIR / f"{name}.json"


def snapshot_exists(name: str) -> bool:
    return _archive_path(name).exists() and _meta_path(name).exists()


def load_snapshot(name: str) -> SnapshotInfo:
    if not snapshot_exists(name):
        raise SnapshotError(f"no otbox snapshot named {name!r}")
    data = json.loads(_meta_path(name).read_text())
    return SnapshotInfo(
        name=data["name"],
        archive=Path(data["archive"]),
        origin_box_id=data["origin_box_id"],
        origin_root=data["origin_root"],
        seed=data.get("seed"),
        driver=data.get("driver", "local"),
        created=data["created"],
        size_bytes=data.get("size_bytes", 0),
    )


def create_snapshot(box: Box, name: str, *, overwrite: bool = False) -> SnapshotInfo:
    """Archive an entire box to ``.otbox/snapshots/<name>.tar.gz``."""
    ensure_state_root()
    if not box.root.exists():
        raise SnapshotError(f"box {box.box_id} has no on-disk state to snapshot")
    archive = _archive_path(name)
    if archive.exists() and not overwrite:
        raise SnapshotError(
            f"snapshot {name!r} already exists; pass overwrite=True to replace it"
        )

    tmp = archive.with_suffix(".tar.gz.partial")
    with tarfile.open(tmp, "w:gz") as tar:
        # arcname="." keeps the archive relocatable: it extracts straight
        # into whatever box root we later restore into.
        tar.add(box.root, arcname=".")
    tmp.replace(archive)

    info = SnapshotInfo(
        name=name,
        archive=archive,
        origin_box_id=box.box_id,
        origin_root=str(box.root),
        seed=box.seed,
        driver=box.driver,
        created=utc_now(),
        size_bytes=archive.stat().st_size,
    )
    _meta_path(name).write_text(json.dumps(info.to_dict(), indent=2, sort_keys=True) + "\n")
    return info


def _rewrite_absolute_paths(box: Box, old_root: str) -> int:
    """Repoint opentraces' baked-in absolute paths at the new box root.

    opentraces records the project's absolute repo path in
    ``~/.opentraces/config.json`` (and occasionally in per-project
    ``state.json``). After restoring into a new box id, those still point
    at the origin box. We rewrite every UTF-8 text file under the
    isolated ``~/.opentraces`` plus the project marker. The origin/target
    roots differ only in the ``otb_xxxx`` segment, so substitution is
    unambiguous.
    """
    new_root = str(box.root)
    if old_root == new_root:
        return 0
    targets: list[Path] = []
    ot_dir = box.opentraces_dir
    if ot_dir.exists():
        targets.extend(p for p in ot_dir.rglob("*") if p.is_file())
    marker = box.project / ".opentraces.json"
    if marker.exists():
        targets.append(marker)

    # Per-box venv scripts have absolute-path shebangs that need rewriting
    # whenever c-installed-source snapshots are restored into a fresh box.
    testvenv_bin = box.project / ".testvenv" / "bin"
    if testvenv_bin.exists():
        for entry in testvenv_bin.iterdir():
            if entry.is_file() and not entry.is_symlink():
                targets.append(entry)

    rewritten = 0
    for path in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if old_root not in text:
            continue
        path.write_text(text.replace(old_root, new_root), encoding="utf-8")
        rewritten += 1
    return rewritten


def restore_snapshot(name: str, *, box_id: str | None = None) -> tuple[Box, dict]:
    """Extract a snapshot into a fresh box and return it ready to use."""
    info = load_snapshot(name)
    box = Box(
        box_id=box_id or new_box_id(),
        driver=info.driver,
        seed=info.seed,
        status="restored",
        restored_from=name,
    )
    if box.root.exists():
        raise SnapshotError(f"box {box.box_id} already exists; cannot restore onto it")
    box.root.mkdir(parents=True)

    start = time.monotonic()
    with tarfile.open(info.archive, "r:gz") as tar:
        # ``filter="fully_trusted"`` lets venvs (with their absolute
        # symlinks to /opt/homebrew/bin/python3.14 etc.) extract. We
        # control the archives — they came from our own snapshot() call.
        tar.extractall(box.root, filter="fully_trusted")
    # Carry forward the archived box's notes (checkpoint audit trail,
    # seed report, etc.) before we clobber meta.json with the new identity.
    if box.meta_path.exists():
        try:
            old_meta = json.loads(box.meta_path.read_text())
            box.notes = old_meta.get("notes", {}) or {}
        except (OSError, json.JSONDecodeError):
            pass
    rewritten = _rewrite_absolute_paths(box, info.origin_root)
    duration = time.monotonic() - start

    box.save()  # overwrite the extracted meta.json with the new identity
    return box, {
        "snapshot": name,
        "origin_box_id": info.origin_box_id,
        "paths_rewritten": rewritten,
        "restore_duration_s": round(duration, 4),
    }


def delete_snapshot(name: str) -> None:
    for path in (_archive_path(name), _meta_path(name)):
        if path.exists():
            path.unlink()
