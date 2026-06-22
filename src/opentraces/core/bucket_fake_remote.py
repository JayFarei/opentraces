"""File-backed fake bucket remote — a test/dev double for remote sync.

Extracted from :mod:`opentraces.core.bucket_store` (plan: god-module
decomposition). The ``fake`` remote provider copies the local bucket tree
to/from a ``file://`` root so ``bucket remote`` round-trips can be exercised
without a real HuggingFace remote. It is a *leaf* of the bucket layer — only
:mod:`opentraces.core.bucket_remote` and the tests drive it — so it lives in
its own module instead of bloating the multi-thousand-line ``bucket_store``
core. The dependency is one-directional (``bucket_fake_remote`` →
``bucket_store``); ``bucket_store`` never imports this module, so there is no
cycle.
"""

from __future__ import annotations

import json
import shutil
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import paths
from ._bucket_io import _atomic_write_json
from .bucket_store import (
    BUCKET_REMOTE_SCHEMA,
    _copy_bucket_tree,
    bucket_manifest,
    classify_bucket_remote_state,
    sync_trace_records_from_local_stores,
    write_bucket_sync_state,
)


def fake_remote_root() -> Path | None:
    raw = os.environ.get("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    try:
        from .config import load_config

        remote = load_config().bucket.remote
    except Exception:
        return None
    if not remote.enabled or remote.provider != "fake" or not remote.url:
        return None
    parsed = urlparse(remote.url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).expanduser().resolve()
    return Path(remote.url).expanduser().resolve()


def fake_remote_status(remote_root: Path | None = None) -> dict[str, Any]:
    root = remote_root or fake_remote_root()
    if root is None:
        return {
            "schema_version": BUCKET_REMOTE_SCHEMA,
            "state": "unconfigured",
            "advice": "set OPENTRACES_FAKE_BUCKET_REMOTE_ROOT",
        }
    manifest_path = root / "manifest.json"
    local = bucket_manifest(write=True, include_objects=False)
    if not manifest_path.exists():
        return {
            "schema_version": BUCKET_REMOTE_SCHEMA,
            "state": "missing",
            "remote_root": str(root),
            "local_digest": local.get("digest"),
            "remote_digest": None,
        }
    try:
        remote = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {
            "schema_version": BUCKET_REMOTE_SCHEMA,
            "state": "error",
            "remote_root": str(root),
            "error": str(exc),
            "local_digest": local.get("digest"),
            "remote_digest": None,
        }
    remote_digest = remote.get("digest")
    relation = classify_bucket_remote_state(
        provider="fake",
        target=str(root),
        local_digest=local.get("digest"),
        remote_digest=remote_digest,
    )
    return {
        "schema_version": BUCKET_REMOTE_SCHEMA,
        "state": relation["state"],
        "remote_root": str(root),
        "local_digest": local.get("digest"),
        "remote_digest": remote_digest,
        "last_sync_digest": relation.get("last_sync_digest"),
        "remote_updated_at": remote.get("updated_at"),
    }


def fake_remote_diff(remote_root: Path | None = None) -> dict[str, Any]:
    status = fake_remote_status(remote_root)
    return {
        **status,
        "different": status.get("state")
        in {"missing", "different", "local_ahead", "remote_ahead", "diverged", "error"},
    }


def fake_remote_push(remote_root: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    root = remote_root or fake_remote_root()
    if root is None:
        raise ValueError("set OPENTRACES_FAKE_BUCKET_REMOTE_ROOT")
    local_bucket = paths.bucket_dir()
    root.mkdir(parents=True, exist_ok=True)
    # Same refresh the HF push path runs: re-scan records whose security
    # envelope predates the currently-enabled tools, so push eligibility is
    # judged against current-config security state (parity with the daemon
    # path's implicit index-sync refresh).
    try:
        sync_trace_records_from_local_stores(prune=False)
    except Exception:  # noqa: BLE001 - refresh is best-effort; the gate below stays authoritative
        pass
    manifest = bucket_manifest(write=True, include_objects=False)
    sync = manifest.get("sync") or {}
    if sync.get("eligible") is not True:
        reasons = ", ".join(str(reason) for reason in sync.get("blocked_reasons") or [])
        raise ValueError(
            "bucket is not eligible for remote sync"
            + (f": {reasons}" if reasons else "")
            + (
                "; run 'opentraces setup bucket' to enable the recommended "
                "security tools — unscanned records are re-scanned on the "
                "next push"
                if "unfiltered_records" in (sync.get("blocked_reasons") or [])
                else ""
            )
        )
    status = fake_remote_status(root)
    if status.get("state") in {"remote_ahead", "diverged"} and not force:
        raise ValueError(
            "remote bucket has changes that are not in the local bucket; "
            "pull first or pass --force to overwrite"
        )
    copied = _copy_bucket_tree(local_bucket, root)
    _atomic_write_json(root / "manifest.json", manifest)
    write_bucket_sync_state(
        provider="fake",
        target=str(root),
        digest=manifest.get("digest"),
        remote_digest=manifest.get("digest"),
        direction="push",
    )
    return {
        "schema_version": BUCKET_REMOTE_SCHEMA,
        "state": "pushed",
        "remote_root": str(root),
        "digest": manifest.get("digest"),
        "files_copied": copied,
    }


def fake_remote_pull(remote_root: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    root = remote_root or fake_remote_root()
    if root is None:
        raise ValueError("set OPENTRACES_FAKE_BUCKET_REMOTE_ROOT")
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"fake bucket remote is missing: {root}")
    local_bucket = paths.bucket_dir()
    if root.resolve() == local_bucket.resolve():
        raise ValueError("fake remote root must be separate from the local bucket")
    status = fake_remote_status(root)
    if status.get("state") in {"local_ahead", "diverged"} and not force:
        raise ValueError(
            "local bucket has changes that are not in the remote bucket; "
            "push first or pass --force to overwrite"
        )
    if local_bucket.exists():
        shutil.rmtree(local_bucket)
    local_bucket.mkdir(parents=True, exist_ok=True)
    copied = _copy_bucket_tree(root, local_bucket, skip_names={"manifest.json"})
    manifest = bucket_manifest(write=True, include_objects=False)
    write_bucket_sync_state(
        provider="fake",
        target=str(root),
        digest=manifest.get("digest"),
        remote_digest=manifest.get("digest"),
        direction="pull",
    )
    return {
        "schema_version": BUCKET_REMOTE_SCHEMA,
        "state": "pulled",
        "remote_root": str(root),
        "digest": manifest.get("digest"),
        "files_copied": copied,
    }
