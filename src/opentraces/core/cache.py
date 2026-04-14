"""Attribution cache — JSON files + content-addressed blob store.

Layout (per project):
    ~/.opentraces/projects/<slug>/
        attributions/<sha>.json    — per-commit attribution
        entities/<sha>.json        — reserved for phase 4 (entity map)
        blobs/<ab>/<cd>/<sha256>   — CA store for oversized line payloads

Attribution JSON shape (version 1):
    {
      "commit_sha": "abc…",
      "project_slug": "…",
      "version": 1,
      "generated_at": "2026-…",
      "traces": [{"trace_id": "…", "line_count": 12, "files": ["path/a.py"]}],
      "files": {
        "path/a.py": {
          "lines": [{"n": 1, "trace_id": "…" | null,
                     "consistency": "attributed|pre-audit|missing_from_audit"}],
          "total": 42
        }
      },
      "coverage": {"attributed": 38, "total": 42, "ratio": 0.904}
    }

When a file's ``lines`` payload would exceed ~256KB, it's spilled into the
blob store and replaced with ``{"blob_sha256": "…", "total": N}``.

All writes go through an atomic tmpfile + ``os.replace`` rename. Blob writes
are no-ops when the blob already exists (content-addressed).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .config import get_project_dir

CACHE_VERSION = 1
BLOB_SPILL_THRESHOLD = 256 * 1024  # bytes

# Subdir names — intentionally generic. No user-visible "sem"/"tree-sitter"/etc.
_ATTRIBUTIONS_DIR = "attributions"
_ENTITIES_DIR = "entities"
_BLOBS_DIR = "blobs"


class AttributionCache:
    """File-backed cache for per-commit attribution results."""

    def __init__(self, project_cwd: Path) -> None:
        self._project_cwd = Path(project_cwd).resolve()
        self._root = get_project_dir(self._project_cwd)

    # --- path helpers ---

    @property
    def root(self) -> Path:
        return self._root

    def attribution_path(self, sha: str) -> Path:
        return self._root / _ATTRIBUTIONS_DIR / f"{sha}.json"

    def entity_path(self, sha: str) -> Path:
        return self._root / _ENTITIES_DIR / f"{sha}.json"

    def blob_path(self, sha256: str) -> Path:
        if len(sha256) < 4:
            raise ValueError(f"sha256 too short: {sha256!r}")
        return self._root / _BLOBS_DIR / sha256[:2] / sha256[2:4] / sha256

    # --- attribution I/O ---

    def has_attribution(self, sha: str) -> bool:
        return self.attribution_path(sha).is_file()

    def read_attribution(self, sha: str) -> dict | None:
        p = self.attribution_path(sha)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def write_attribution(self, sha: str, data: dict) -> None:
        """Write attribution JSON atomically. Spills oversized file payloads
        into the blob store before writing."""
        data = dict(data)  # shallow copy; we may mutate files.*.lines
        data.setdefault("version", CACHE_VERSION)
        data.setdefault("commit_sha", sha)
        files = data.get("files") or {}
        for rel, info in list(files.items()):
            if not isinstance(info, dict):
                continue
            lines = info.get("lines")
            if not isinstance(lines, list):
                continue
            payload = json.dumps(lines, separators=(",", ":"))
            if len(payload.encode("utf-8")) > BLOB_SPILL_THRESHOLD:
                sha256 = self.write_blob(payload.encode("utf-8"))
                info.pop("lines", None)
                info["blob_sha256"] = sha256
                files[rel] = info
        self._atomic_write_json(self.attribution_path(sha), data)

    def list_attributed_shas(self) -> list[str]:
        d = self._root / _ATTRIBUTIONS_DIR
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob("*.json"))

    # --- blob store ---

    def write_blob(self, content: bytes) -> str:
        """Store ``content`` by its sha256 hash. Returns the hex digest.

        Idempotent — if the blob already exists the bytes are left untouched.
        """
        sha256 = hashlib.sha256(content).hexdigest()
        p = self.blob_path(sha256)
        if p.is_file():
            return sha256
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, p)
        return sha256

    def read_blob(self, sha256: str) -> bytes | None:
        p = self.blob_path(sha256)
        if not p.is_file():
            return None
        try:
            return p.read_bytes()
        except OSError:
            return None

    # --- lifecycle ---

    def clear(self) -> None:
        """Delete attributions/, entities/, and blobs/ under this project."""
        for sub in (_ATTRIBUTIONS_DIR, _ENTITIES_DIR, _BLOBS_DIR):
            d = self._root / sub
            if d.is_dir():
                shutil.rmtree(d)

    # --- internals ---

    @staticmethod
    def _atomic_write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, path)
