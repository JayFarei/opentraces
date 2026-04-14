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
# Independent of CACHE_VERSION: tracks the *content* schema of the
# attribution payload's identifier fields. Bumped to 2 when we fixed the
# session_id-leaking-as-trace_id bug — v1 caches may carry session_ids in
# the traces[].trace_id field and need read-time normalisation.
ATTRIBUTION_SCHEMA_VERSION = 2
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
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if isinstance(data, dict):
            data = self._normalise_legacy_trace_ids(data)
        return data

    def _normalise_legacy_trace_ids(self, data: dict) -> dict:
        """In-memory fixup for v1 caches that wrote session_ids under the
        ``trace_id`` name. Maps each session_id to the real trace_id by
        looking up the matching JSONL's first line. Never mutates the
        on-disk cache — users wanting permanent fix run ``ot backfill
        --rebuild``.
        """
        try:
            schema_v = int(data.get("attribution_schema_version") or 1)
        except (TypeError, ValueError):
            schema_v = 1
        if schema_v >= ATTRIBUTION_SCHEMA_VERSION:
            return data
        traces = data.get("traces")
        if not isinstance(traces, list) or not traces:
            return data
        # Build session_id -> trace_id map from the Claude Code corpus,
        # lazily and only when needed.
        try:
            from ..enrichment.git.attribution import (
                _encode_cwd, _read_trace_id_from_jsonl,
            )
        except Exception:
            return data
        proj_dir = (
            Path.home() / ".claude" / "projects"
            / _encode_cwd(self._project_cwd)
        )
        if not proj_dir.is_dir():
            return data
        sid_to_tid: dict[str, str] = {}
        for jsonl_path in proj_dir.glob("*.jsonl"):
            tid = _read_trace_id_from_jsonl(jsonl_path)
            if tid:
                sid_to_tid[jsonl_path.stem] = tid
        if not sid_to_tid:
            return data
        for entry in traces:
            if not isinstance(entry, dict):
                continue
            old = entry.get("trace_id")
            if isinstance(old, str) and old in sid_to_tid:
                entry["trace_id"] = sid_to_tid[old]
        # Same fixup for files.<rel>.lines[].trace_id when present inline.
        files = data.get("files") or {}
        if isinstance(files, dict):
            for info in files.values():
                if not isinstance(info, dict):
                    continue
                lines = info.get("lines")
                if not isinstance(lines, list):
                    continue
                for ln in lines:
                    if not isinstance(ln, dict):
                        continue
                    old = ln.get("trace_id")
                    if isinstance(old, str) and old in sid_to_tid:
                        ln["trace_id"] = sid_to_tid[old]
        return data

    def write_attribution(self, sha: str, data: dict) -> None:
        """Write attribution JSON atomically. Spills oversized file payloads
        into the blob store before writing."""
        data = dict(data)  # shallow copy; we may mutate files.*.lines
        data.setdefault("version", CACHE_VERSION)
        # Stamp every fresh write with the current attribution schema
        # version so future read-time normalisers can skip v2+ payloads.
        data["attribution_schema_version"] = ATTRIBUTION_SCHEMA_VERSION
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
