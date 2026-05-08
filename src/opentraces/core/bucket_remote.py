"""Private bucket remote adapters.

The bucket remote is private workspace infrastructure. It is intentionally
separate from dataset remotes, which are publication destinations.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:  # pragma: no cover - exercised by tests through monkeypatching.
    from huggingface_hub import HfApi
except Exception:  # pragma: no cover
    HfApi = None  # type: ignore[assignment]

from . import paths
from .bucket_store import (
    bucket_manifest,
    fake_remote_diff,
    fake_remote_pull,
    fake_remote_push,
    fake_remote_root,
    fake_remote_status,
)
from .config import load_config


REMOTE_SCHEMA = "opentraces.bucket.remote.v1"


class BucketRemoteError(RuntimeError):
    """Raised when a configured bucket remote cannot be used."""


def remote_status(*, fake_root: Path | None = None) -> dict[str, Any]:
    """Return provider-aware private bucket remote status."""

    if fake_root is not None:
        return _fake_payload(fake_remote_status(fake_root))
    cfg = load_config()
    remote = cfg.bucket.remote
    if cfg.bucket.storage != "remote" or not remote.enabled:
        fake = _ambient_fake_status()
        if fake is not None:
            return fake
        return {
            "schema_version": REMOTE_SCHEMA,
            "state": "unconfigured",
            "provider": remote.provider,
            "advice": "run opentraces setup bucket",
        }
    if remote.provider == "fake":
        return _fake_payload(fake_remote_status())
    return _hf_status(remote.url, cfg.hf_token)


def remote_diff(*, fake_root: Path | None = None) -> dict[str, Any]:
    if fake_root is not None:
        return _fake_payload(fake_remote_diff(fake_root))
    status = remote_status()
    return {
        **status,
        "different": status.get("state") in {"missing", "different", "error"},
    }


def remote_push(*, fake_root: Path | None = None) -> dict[str, Any]:
    if fake_root is not None:
        return _fake_payload(fake_remote_push(fake_root))
    cfg = load_config()
    remote = cfg.bucket.remote
    if cfg.bucket.storage != "remote" or not remote.enabled:
        if fake_remote_root() is not None:
            return _fake_payload(fake_remote_push())
        raise BucketRemoteError("private bucket remote is not configured")
    if remote.provider == "fake":
        return _fake_payload(fake_remote_push())
    return _hf_push(remote.url, cfg.hf_token)


def remote_pull(*, fake_root: Path | None = None) -> dict[str, Any]:
    if fake_root is not None:
        return _fake_payload(fake_remote_pull(fake_root))
    cfg = load_config()
    remote = cfg.bucket.remote
    if cfg.bucket.storage != "remote" or not remote.enabled:
        if fake_remote_root() is not None:
            return _fake_payload(fake_remote_pull())
        raise BucketRemoteError("private bucket remote is not configured")
    if remote.provider == "fake":
        return _fake_payload(fake_remote_pull())
    return _hf_pull(remote.url, cfg.hf_token)


def reconcile_once(*, reason: str = "manual") -> dict[str, Any]:
    """Best-effort daemon reconciliation for the configured bucket remote."""

    cfg = load_config()
    remote = cfg.bucket.remote
    if cfg.bucket.storage != "remote" or not remote.enabled:
        return {"schema_version": REMOTE_SCHEMA, "state": "disabled", "reason": reason}
    if remote.sync_policy != "daemon":
        return {"schema_version": REMOTE_SCHEMA, "state": "manual", "reason": reason}
    status = remote_status()
    if status.get("state") == "current":
        return {**status, "reason": reason}
    if status.get("state") not in {"missing", "different"}:
        return {**status, "reason": reason}
    pushed = remote_push()
    return {**pushed, "reason": reason}


def _hf_status(url: str | None, token: str | None) -> dict[str, Any]:
    repo_id = _hf_repo_id(url)
    local = bucket_manifest(write=True, include_objects=False)
    try:
        api = _hf_api(token)
    except BucketRemoteError as exc:
        return _hf_status_payload(
            repo_id,
            state="error",
            local_digest=local.get("digest"),
            remote_digest=None,
            error=str(exc),
        )
    try:
        files = set(api.list_repo_files(repo_id=repo_id, repo_type="dataset"))
    except Exception as exc:  # noqa: BLE001
        return _hf_error_or_missing(exc, repo_id, local.get("digest"))
    if "manifest.json" not in files:
        return _hf_status_payload(
            repo_id,
            state="missing",
            local_digest=local.get("digest"),
            remote_digest=None,
        )
    try:
        downloaded = api.hf_hub_download(
            repo_id=repo_id,
            filename="manifest.json",
            repo_type="dataset",
        )
        remote_manifest = json.loads(Path(downloaded).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _hf_status_payload(
            repo_id,
            state="error",
            local_digest=local.get("digest"),
            remote_digest=None,
            error=str(exc),
        )
    remote_digest = remote_manifest.get("digest")
    return _hf_status_payload(
        repo_id,
        state="current" if remote_digest == local.get("digest") else "different",
        local_digest=local.get("digest"),
        remote_digest=remote_digest,
        remote_updated_at=remote_manifest.get("updated_at"),
    )


def _hf_push(url: str | None, token: str | None) -> dict[str, Any]:
    repo_id = _hf_repo_id(url)
    api = _hf_api(token)
    manifest = bucket_manifest(write=True, include_objects=False)
    sync = manifest.get("sync") or {}
    if sync.get("eligible") is not True:
        reasons = ", ".join(str(reason) for reason in sync.get("blocked_reasons") or [])
        raise BucketRemoteError(
            "bucket is not eligible for remote sync"
            + (f": {reasons}" if reasons else "")
        )
    api.create_repo(
        repo_id=repo_id,
        repo_type="dataset",
        exist_ok=True,
        private=True,
    )
    files_uploaded = 0
    for path in sorted(paths.bucket_dir().rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(paths.bucket_dir()).as_posix()
        api.upload_file(
            path_or_fileobj=str(path),
            path_in_repo=rel,
            repo_id=repo_id,
            repo_type="dataset",
        )
        files_uploaded += 1
    return {
        "schema_version": REMOTE_SCHEMA,
        "provider": "huggingface",
        "state": "pushed",
        "repo_id": repo_id,
        "digest": manifest.get("digest"),
        "files_uploaded": files_uploaded,
    }


def _hf_pull(url: str | None, token: str | None) -> dict[str, Any]:
    repo_id = _hf_repo_id(url)
    api = _hf_api(token)
    try:
        files = sorted(api.list_repo_files(repo_id=repo_id, repo_type="dataset"))
    except Exception as exc:  # noqa: BLE001
        raise BucketRemoteError(f"remote bucket is not readable: {repo_id}: {exc}") from exc
    if "manifest.json" not in files:
        raise BucketRemoteError(f"remote bucket is missing manifest.json: {repo_id}")
    with tempfile.TemporaryDirectory(prefix="opentraces-bucket-pull-") as tmp_name:
        tmp_root = Path(tmp_name) / "bucket"
        tmp_root.mkdir(parents=True)
        copied = 0
        for name in files:
            if name == ".gitattributes" or name.endswith("/"):
                continue
            downloaded = Path(
                api.hf_hub_download(
                    repo_id=repo_id,
                    filename=name,
                    repo_type="dataset",
                )
            )
            target = tmp_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(downloaded, target)
            copied += 1
        local = paths.bucket_dir()
        if local.exists():
            shutil.rmtree(local)
        local.mkdir(parents=True, exist_ok=True)
        _copy_tree(tmp_root, local)
    manifest = bucket_manifest(write=True, include_objects=False)
    return {
        "schema_version": REMOTE_SCHEMA,
        "provider": "huggingface",
        "state": "pulled",
        "repo_id": repo_id,
        "digest": manifest.get("digest"),
        "files_downloaded": copied,
    }


def _hf_api(token: str | None):
    if not token:
        raise BucketRemoteError("not authenticated; run opentraces auth login")
    if HfApi is None:
        raise BucketRemoteError("huggingface_hub is not installed")
    return HfApi(token=token)


def _ambient_fake_status() -> dict[str, Any] | None:
    payload = fake_remote_status()
    if payload.get("state") == "unconfigured":
        return None
    return _fake_payload(payload)


def _fake_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "schema_version": REMOTE_SCHEMA,
        "provider": "fake",
    }


def _hf_repo_id(url: str | None) -> str:
    if not url:
        raise BucketRemoteError("bucket remote URL is missing")
    raw = str(url)
    parsed = urlparse(raw)
    if parsed.scheme == "hf":
        return parsed.netloc + parsed.path
    marker = "huggingface.co/datasets/"
    if marker in raw:
        return raw.split(marker, 1)[1].strip("/")
    if "://" in raw:
        raise BucketRemoteError(f"unsupported bucket remote URL: {url}")
    return raw


def _hf_error_or_missing(exc: Exception, repo_id: str, local_digest: str | None) -> dict[str, Any]:
    text = str(exc).lower()
    state = "missing" if any(bit in text for bit in ("404", "not found", "repo not found")) else "error"
    return _hf_status_payload(
        repo_id,
        state=state,
        local_digest=local_digest,
        remote_digest=None,
        error=None if state == "missing" else str(exc),
    )


def _hf_status_payload(
    repo_id: str,
    *,
    state: str,
    local_digest: str | None,
    remote_digest: str | None,
    remote_updated_at: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": REMOTE_SCHEMA,
        "provider": "huggingface",
        "state": state,
        "repo_id": repo_id,
        "local_digest": local_digest,
        "remote_digest": remote_digest,
    }
    if remote_updated_at:
        payload["remote_updated_at"] = remote_updated_at
    if error:
        payload["error"] = error
    return payload


def _copy_tree(source: Path, destination: Path) -> int:
    copied = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied
