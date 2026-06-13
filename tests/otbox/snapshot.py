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
import os
import re
import secrets
import sqlite3
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


def _unique_partial_path(target: Path) -> Path:
    """Per-writer temp path in target's directory: ``<name>.<pid>-<rand>.partial``.

    Concurrent writers (e.g. two pytest runs cold-building the same
    content-addressed checkpoint) must never share a temp path: a shared
    path means interleaved writes plus a ``FileNotFoundError`` for the
    writer that renames second (issue #50). ``<pid>`` makes losing-writer
    diagnosis greppable; the random token covers pid reuse and two
    snapshots inside one process. The ``.partial`` suffix keeps leftovers
    self-describing and outside the ``*.tar.gz`` listing glob.
    """
    return target.parent / f"{target.name}.{os.getpid()}-{secrets.token_hex(4)}.partial"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a unique temp + atomic rename.

    A concurrent reader never observes torn/empty content; same-directory
    rename keeps the swap atomic on POSIX.
    """
    tmp = _unique_partial_path(path)
    try:
        tmp.write_text(text)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)


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

    tmp = _unique_partial_path(archive)
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            # arcname="." keeps the archive relocatable: it extracts straight
            # into whatever box root we later restore into.
            tar.add(box.root, arcname=".")
    except BaseException:
        # With per-writer unique temps, failure cleanup is mandatory or
        # orphaned partials accumulate.
        tmp.unlink(missing_ok=True)
        raise
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
    _atomic_write_text(
        _meta_path(name), json.dumps(info.to_dict(), indent=2, sort_keys=True) + "\n"
    )
    return info


def _quote_sqlite_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# Any absolute path ending in /.otbox/boxes/otb_<id>, regardless of the
# machine prefix that produced it (the non-greedy prefix stops at common
# JSON/text delimiters). Used for cross-machine restore.
_ANY_BOX_ROOT_RE = re.compile(r"/[^\s\"',;:()\[\]]*?/\.otbox/boxes/otb_[0-9a-fA-F]+")


def _replace_box_roots(
    text: str,
    *,
    old_root: str,
    new_root: str,
    boxes_root: Path,
) -> str:
    text = text.replace(old_root, new_root)
    boxes_prefix = re.escape(str(boxes_root))
    box_root_re = re.compile(rf"{boxes_prefix}/otb_[0-9a-fA-F]+")
    text = box_root_re.sub(new_root, text)
    # Cross-machine restore: a captured artifact may carry a DIFFERENT
    # machine's path prefix (captured under /Users/... on a mac, restored on
    # /home/runner/... on CI), which neither of the above — both keyed on the
    # CURRENT machine's boxes_root — can match. Repoint ANY absolute path
    # ending in /.otbox/boxes/otb_<id> at the new box root regardless of the
    # capture-machine prefix. (The first on-main nightly's whole
    # restored-world cluster traced to this: trace records kept /Users/...
    # paths that don't exist on CI, so trace query found nothing.)
    return _ANY_BOX_ROOT_RE.sub(new_root, text)


def _rewrite_sqlite_absolute_paths(
    path: Path,
    old_root: str,
    new_root: str,
    boxes_root: Path,
) -> int:
    """Rewrite old box roots inside known path-bearing SQLite projections.

    The Trace Index stores ``repo_path`` / ``trace_path`` columns in SQLite, so
    a text-file-only restore can leave a path-valid archive with a stale index.
    Keep this generic over TEXT columns so future projection tables inherit the
    portability fix without coupling this helper to opentraces internals.
    """
    if not path.exists():
        return 0

    try:
        with sqlite3.connect(path) as conn:
            rows = list(
                conn.execute(
                    """
                    select name from sqlite_master
                    where type = 'table' and name not like 'sqlite_%'
                    order by name
                    """
                )
            )
            rewritten = 0
            for (table_name,) in rows:
                quoted_table = _quote_sqlite_identifier(str(table_name))
                columns = list(conn.execute(f"pragma table_info({quoted_table})"))
                for column in columns:
                    col_name = str(column[1])
                    col_type = str(column[2] or "").upper()
                    if "TEXT" not in col_type and "CHAR" not in col_type and "CLOB" not in col_type:
                        continue
                    quoted_col = _quote_sqlite_identifier(col_name)
                    values = [
                        str(row[0])
                        for row in conn.execute(
                            f"select {quoted_col} from {quoted_table} where {quoted_col} is not null"
                        )
                    ]
                    # Discover box roots regardless of the capture-machine
                    # prefix (cross-machine restore): the index SQLite, built
                    # on the capture machine, holds /Users/... paths that the
                    # current-boxes_root pattern would miss on CI.
                    discovered_roots = {
                        match
                        for value in values
                        for match in _ANY_BOX_ROOT_RE.findall(value)
                    }
                    roots = [old_root, *sorted(discovered_roots)]
                    for root in roots:
                        if root == new_root:
                            continue
                        before = conn.total_changes
                        conn.execute(
                            f"""
                            update {quoted_table}
                            set {quoted_col} = replace({quoted_col}, ?, ?)
                            where instr({quoted_col}, ?) > 0
                            """,
                            (root, new_root, root),
                        )
                        rewritten += conn.total_changes - before
            conn.commit()
            return rewritten
    except sqlite3.DatabaseError:
        return 0


def rewrite_absolute_paths(box: Box, old_root: str) -> int:
    """Repoint opentraces' baked-in absolute paths at the new box root.

    opentraces records the project's absolute repo path in
    ``~/.opentraces/config.json`` (and occasionally in per-project
    ``state.json``). The Trace Index also stores absolute repo paths in
    SQLite. After restoring into a new box id, those still point at the
    origin box. We rewrite every UTF-8 text file under the isolated
    ``~/.opentraces``, the project marker, venv entrypoint shebangs, and
    known SQLite projections. The origin/target roots differ only in the
    ``otb_xxxx`` segment, so substitution is unambiguous.
    """
    new_root = str(box.root)
    boxes_root = box.root.parent
    if old_root == new_root:
        return 0
    targets: list[Path] = []
    ot_dir = box.opentraces_dir
    if ot_dir.exists():
        targets.extend(p for p in ot_dir.rglob("*") if p.is_file())
    if box.meta_path.exists():
        targets.append(box.meta_path)
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
        new_text = _replace_box_roots(
            text,
            old_root=old_root,
            new_root=new_root,
            boxes_root=boxes_root,
        )
        if new_text == text:
            continue
        path.write_text(new_text, encoding="utf-8")
        rewritten += 1

    sqlite_targets = [box.opentraces_dir / "index" / "index.db"]
    search_projection_root = (
        box.opentraces_dir / "bucket" / "projections" / "search" / "v1"
    )
    if search_projection_root.exists():
        sqlite_targets.extend(search_projection_root.rglob("*.sqlite"))
    for path in sqlite_targets:
        rewritten += _rewrite_sqlite_absolute_paths(
            path,
            old_root,
            new_root,
            boxes_root,
        )
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
    rewritten = rewrite_absolute_paths(box, info.origin_root)
    if box.meta_path.exists():
        try:
            rewritten_meta = json.loads(box.meta_path.read_text())
            box.notes = rewritten_meta.get("notes", {}) or box.notes
        except (OSError, json.JSONDecodeError):
            pass
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
