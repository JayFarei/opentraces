"""Bucket backend abstraction (plan 080 §6, §7, §8).

A read-only interface over the on-disk bucket layout v2 so that consumer
verbs (``trace get``, ``ctx tree``, ``ctx show``, ``trail blame``, ...) can
swap between a local cache and a remote HuggingFace Hub dataset via a
single ``--remote`` flag.

Two concrete backends ship:

- :class:`LocalBucketBackend` reads from ``paths.bucket_dir()`` using the
  existing path helpers in :mod:`opentraces.core.bucket_store`. Trivial
  filesystem wrapper, no network I/O.
- :class:`RemoteHubBackend` reads from a HuggingFace Hub dataset repo via
  ``huggingface_hub.hf_hub_download``. Uses the same ``cfg.hf_token`` auth
  path as :mod:`opentraces.core.bucket_remote` (no new auth surface, per
  plan 080 §6). Each method downloads at most one file; the backend
  keeps an in-process cache so repeated reads of the same file in one
  command are free.

The factory :func:`get_backend` picks the right backend from a
``--remote`` argument: ``None`` -> local, otherwise -> remote with the
argument interpreted as a HF Hub ``repo_id``.

Cross-track contract:
    Both backends MUST return identical Python objects for the same
    on-bucket files. Track D3 tests assert this symmetry directly by
    monkeypatching :class:`RemoteHubBackend`'s download primitive to
    return paths inside a local bucket fixture.
"""

from __future__ import annotations

import abc
import gzip
import json
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, unquote

try:  # pragma: no cover - exercised by tests through monkeypatching.
    from huggingface_hub import hf_hub_download
except Exception:  # pragma: no cover
    hf_hub_download = None  # type: ignore[assignment]

from . import paths
from .bucket_layout import (
    blobs_v1_context_path,
    bucket_manifest_path,
    events_v1_index_path,
    trace_v1_context_path,
    trace_v1_json_path,
    trace_v1_trail_path,
)
from .bucket_remote import BucketRemoteError
from .bucket_store import (
    BUCKET_MANIFEST_SCHEMA,
    BucketLayoutError,
)
from .config import load_config


__all__ = [
    "BucketBackend",
    "LocalBucketBackend",
    "RemoteHubBackend",
    "get_backend",
]


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BucketBackend(abc.ABC):
    """Read-only view over a bucket-layout-v2 substrate.

    Backends are stateless from the caller's perspective: each method
    is independent and idempotent. Concrete backends MAY cache fetched
    bytes for the lifetime of the instance to avoid repeated network
    round-trips inside one CLI invocation.
    """

    # ------------------------------------------------------------------
    # Top-level reads
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_manifest(self) -> dict[str, Any]:
        """Return the parsed ``bucket/manifest.json``.

        Raises :class:`BucketLayoutError` on a schema mismatch — every
        consumer is on bucket layout v2; there is no v1 reader path on
        this branch (plan 080 §20 Resolution E).
        """

    @abc.abstractmethod
    def get_events_index(self) -> dict[str, Any]:
        """Return the parsed ``bucket/events/v1/index.json``.

        The events mirror index lists every batch file in the canonical
        Git event log mirror and is the entry point for trail-graph
        and event-log replays against a remote bucket.
        """

    # ------------------------------------------------------------------
    # Per-trace reads
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_trace_json(self, trace_id: str) -> dict[str, Any]:
        """Return the parsed ``trace.json`` (TraceRecord spine) for ``trace_id``.

        The project slug is resolved from the manifest's ``traces[]``
        array; raises :class:`BucketRemoteError` (subclass of
        :class:`RuntimeError`) if the trace is not enumerated by the
        manifest.
        """

    @abc.abstractmethod
    def read_trail_jsonl_gz(self, trace_id: str) -> Iterator[dict[str, Any]]:
        """Yield decompressed Trail events for ``trace_id``.

        Empty iterator when the trail companion is absent. Each yielded
        object is the parsed JSON line; consumers MUST tolerate empty
        lines and the file not existing (sources without a Trail).
        """

    @abc.abstractmethod
    def read_context_jsonl_gz(self, trace_id: str) -> Iterator[dict[str, Any]]:
        """Yield decompressed Context Tree events for ``trace_id``.

        Same shape contract as :meth:`read_trail_jsonl_gz`.
        """

    # ------------------------------------------------------------------
    # Lazy layer-blob fetch (powers ``ctx show --remote``)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_layer_blob(self, layer_id: str, project_slug: str) -> dict[str, Any]:
        """Return the parsed Context Layer blob for ``layer_id`` under ``project_slug``.

        The blob path is content-addressed by ``layer_id``:
        ``blobs/v1/<project>/context/<hh>/<hash>.json.gz``. Remote
        backends fetch on demand and cache locally; local backends
        read from disk.

        Raises :class:`BucketRemoteError` if the blob is missing.
        """

    # ------------------------------------------------------------------
    # Helpers shared by both backends
    # ------------------------------------------------------------------

    def _project_slug_for_trace(self, manifest: dict[str, Any], trace_id: str) -> str:
        """Resolve ``project_slug`` for ``trace_id`` from a manifest payload.

        Plan 080 §4: every per-trace envelope lives under
        ``traces/v1/<project_slug>/<trace_id>/`` and the manifest's
        ``traces[]`` entries enumerate the pair as ``{"project_slug":
        ..., "trace_id": ...}``.
        """

        traces = manifest.get("traces") or []
        for entry in traces:
            if not isinstance(entry, dict):
                continue
            if entry.get("trace_id") == trace_id:
                slug = entry.get("project_slug")
                if isinstance(slug, str) and slug:
                    return slug
        raise BucketRemoteError(
            f"trace {trace_id!r} not found in bucket manifest"
        )


# ---------------------------------------------------------------------------
# Local backend
# ---------------------------------------------------------------------------


class LocalBucketBackend(BucketBackend):
    """Read from ``paths.bucket_dir()``.

    Thin wrapper over the existing :mod:`opentraces.core.bucket_store`
    path helpers. No network I/O, no caching needed beyond the OS page
    cache.
    """

    def get_manifest(self) -> dict[str, Any]:
        manifest_path = bucket_manifest_path()
        return _load_json_manifest(manifest_path)

    def get_events_index(self) -> dict[str, Any]:
        index_path = events_v1_index_path()
        if not index_path.exists():
            raise BucketRemoteError(
                f"bucket events index missing: {index_path}"
            )
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise BucketRemoteError(
                f"bucket events index is unreadable: {index_path}: {exc}"
            ) from exc

    def get_trace_json(self, trace_id: str) -> dict[str, Any]:
        manifest = self.get_manifest()
        project_slug = self._project_slug_for_trace(manifest, trace_id)
        path = trace_v1_json_path(project_slug, trace_id)
        if not path.exists():
            raise BucketRemoteError(
                f"trace.json missing for {trace_id!r} under {project_slug!r}: {path}"
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise BucketRemoteError(
                f"trace.json is unreadable: {path}: {exc}"
            ) from exc

    def read_trail_jsonl_gz(self, trace_id: str) -> Iterator[dict[str, Any]]:
        manifest = self.get_manifest()
        project_slug = self._project_slug_for_trace(manifest, trace_id)
        path = trace_v1_trail_path(project_slug, trace_id)
        yield from _iter_jsonl_gz_path(path)

    def read_context_jsonl_gz(self, trace_id: str) -> Iterator[dict[str, Any]]:
        manifest = self.get_manifest()
        project_slug = self._project_slug_for_trace(manifest, trace_id)
        path = trace_v1_context_path(project_slug, trace_id)
        yield from _iter_jsonl_gz_path(path)

    def get_layer_blob(self, layer_id: str, project_slug: str) -> dict[str, Any]:
        path = blobs_v1_context_path(project_slug, layer_id)
        if not path.exists():
            raise BucketRemoteError(
                f"context layer blob missing: layer_id={layer_id!r} project={project_slug!r}: {path}"
            )
        try:
            raw = gzip.decompress(path.read_bytes()).decode("utf-8")
        except (OSError, gzip.BadGzipFile) as exc:
            raise BucketRemoteError(
                f"context layer blob is unreadable: {path}: {exc}"
            ) from exc
        try:
            return json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise BucketRemoteError(
                f"context layer blob is not JSON: {path}: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Remote HuggingFace Hub backend
# ---------------------------------------------------------------------------


class RemoteHubBackend(BucketBackend):
    """Read from a HuggingFace Hub dataset repo.

    Constructor takes a ``repo_id`` (e.g. ``"user/my-bucket"``) and
    resolves the HF token via :func:`opentraces.core.config.load_config`
    on first use (same auth path as
    :func:`opentraces.core.bucket_remote._hf_api`). Each method fetches
    at most one file through ``huggingface_hub.hf_hub_download``; the
    downloaded path is cached for the lifetime of the backend instance
    so repeated reads of the same file in one CLI invocation are free.
    """

    def __init__(self, repo_id: str, *, token: str | None = None) -> None:
        if not repo_id:
            raise BucketRemoteError("remote bucket repo_id is empty")
        self._repo_id = repo_id
        self._token = token
        self._token_resolved = token is not None
        # filename -> parsed payload (json) OR list of jsonl lines (str)
        self._cache: dict[str, Any] = {}
        # filename -> on-disk path returned by hf_hub_download (so tests
        # that exercise the download primitive directly can observe the
        # cache hits).
        self._path_cache: dict[str, Path] = {}

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def repo_id(self) -> str:
        return self._repo_id

    def get_manifest(self) -> dict[str, Any]:
        payload = self._fetch_json("manifest.json")
        # Plan 080 §20 Resolution E: schema check on every read path.
        found = payload.get("schema_version") if isinstance(payload, dict) else None
        if found != BUCKET_MANIFEST_SCHEMA:
            raise BucketLayoutError(
                f"remote bucket manifest schema {found!r} incompatible with local "
                f"{BUCKET_MANIFEST_SCHEMA!r}; run 'opentraces setup bucket --migrate' "
                "on the writer machine"
            )
        return payload

    def get_events_index(self) -> dict[str, Any]:
        return self._fetch_json("events/v1/index.json")

    def get_trace_json(self, trace_id: str) -> dict[str, Any]:
        manifest = self.get_manifest()
        project_slug = self._project_slug_for_trace(manifest, trace_id)
        filename = _hf_traces_path(project_slug, trace_id, "trace.json")
        return self._fetch_json(filename)

    def read_trail_jsonl_gz(self, trace_id: str) -> Iterator[dict[str, Any]]:
        manifest = self.get_manifest()
        project_slug = self._project_slug_for_trace(manifest, trace_id)
        filename = _hf_traces_path(project_slug, trace_id, "trail.jsonl.gz")
        yield from self._iter_jsonl_gz(filename)

    def read_context_jsonl_gz(self, trace_id: str) -> Iterator[dict[str, Any]]:
        manifest = self.get_manifest()
        project_slug = self._project_slug_for_trace(manifest, trace_id)
        filename = _hf_traces_path(project_slug, trace_id, "context.jsonl.gz")
        yield from self._iter_jsonl_gz(filename)

    def get_layer_blob(self, layer_id: str, project_slug: str) -> dict[str, Any]:
        filename = _hf_layer_blob_path(project_slug, layer_id)
        return self._fetch_gzipped_json(filename)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_token(self) -> str | None:
        if self._token_resolved:
            return self._token
        cfg = load_config()
        self._token = cfg.hf_token
        self._token_resolved = True
        if not self._token:
            raise BucketRemoteError(
                "not authenticated; run opentraces auth login"
            )
        return self._token

    def _download(self, filename: str) -> Path:
        """Download ``filename`` from the remote repo, returning a local path.

        Cached per backend instance. Tests monkeypatch this method to
        return a fixture path without hitting the network.
        """

        if filename in self._path_cache:
            return self._path_cache[filename]
        token = self._ensure_token()
        if hf_hub_download is None:  # pragma: no cover
            raise BucketRemoteError("huggingface_hub is not installed")
        try:
            local = hf_hub_download(
                repo_id=self._repo_id,
                filename=filename,
                repo_type="dataset",
                token=token,
            )
        except Exception as exc:  # noqa: BLE001 - HF Hub raises many types
            raise BucketRemoteError(
                f"remote bucket fetch failed: {self._repo_id}:{filename}: {exc}"
            ) from exc
        path = Path(local)
        self._path_cache[filename] = path
        return path

    def _fetch_json(self, filename: str) -> dict[str, Any]:
        cached = self._cache.get(filename)
        if cached is not None:
            return cached
        path = self._download(filename)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise BucketRemoteError(
                f"remote bucket file is not JSON: {self._repo_id}:{filename}: {exc}"
            ) from exc
        self._cache[filename] = payload
        return payload

    def _fetch_gzipped_json(self, filename: str) -> dict[str, Any]:
        cached = self._cache.get(filename)
        if cached is not None:
            return cached
        path = self._download(filename)
        try:
            raw = gzip.decompress(path.read_bytes()).decode("utf-8")
        except (OSError, gzip.BadGzipFile) as exc:
            raise BucketRemoteError(
                f"remote bucket blob is unreadable: {self._repo_id}:{filename}: {exc}"
            ) from exc
        try:
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise BucketRemoteError(
                f"remote bucket blob is not JSON: {self._repo_id}:{filename}: {exc}"
            ) from exc
        self._cache[filename] = payload
        return payload

    def _iter_jsonl_gz(self, filename: str) -> Iterator[dict[str, Any]]:
        try:
            path = self._download(filename)
        except BucketRemoteError:
            # An absent companion file is normal (a trace without a
            # trail, etc.). Mirror the local backend's behavior: yield
            # nothing rather than raise.
            return
        yield from _iter_jsonl_gz_path(path)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_backend(remote: str | None) -> BucketBackend:
    """Return the appropriate backend for a ``--remote`` argument.

    ``None`` or empty string -> :class:`LocalBucketBackend`. Any other
    value is interpreted as a HuggingFace Hub dataset ``repo_id``
    (e.g. ``"user/my-bucket"``) and yields a :class:`RemoteHubBackend`.
    """

    if not remote:
        return LocalBucketBackend()
    return RemoteHubBackend(repo_id=remote)


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _load_json_manifest(path: Path) -> dict[str, Any]:
    """Parse a local manifest.json and enforce the v2 schema (plan 080 §20 E)."""

    if not path.exists():
        raise BucketRemoteError(f"bucket manifest missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BucketRemoteError(
            f"bucket manifest is unreadable: {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise BucketLayoutError(
            f"bucket manifest is not an object: {path}"
        )
    found = payload.get("schema_version")
    if found != BUCKET_MANIFEST_SCHEMA:
        raise BucketLayoutError(
            f"bucket manifest schema {found!r} incompatible with local "
            f"{BUCKET_MANIFEST_SCHEMA!r}; run 'opentraces setup bucket --migrate'"
        )
    return payload


def _iter_jsonl_gz_path(path: Path) -> Iterator[dict[str, Any]]:
    """Yield decoded JSONL records from a ``.jsonl.gz`` file (absent -> empty)."""

    if not path.exists() or path.stat().st_size == 0:
        return
    try:
        raw = gzip.decompress(path.read_bytes()).decode("utf-8")
    except (OSError, gzip.BadGzipFile):
        return
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue


def _hf_path_part(value: str) -> str:
    """URL-quote a path component the same way :mod:`bucket_store` does.

    The local writer uses :func:`urllib.parse.quote` with the
    ``"-._~"`` safe set so trace ids and project slugs round-trip
    cleanly across the local and remote namespaces.
    """

    return quote(str(value), safe="-._~")


def _hf_traces_path(project_slug: str, trace_id: str, filename: str) -> str:
    """Build the in-repo path for a per-trace envelope file (POSIX)."""

    return (
        f"traces/v1/{_hf_path_part(project_slug)}/"
        f"{_hf_path_part(trace_id)}/{filename}"
    )


def _hf_layer_blob_path(project_slug: str, layer_id: str) -> str:
    """Build the in-repo path for a content-addressed Context Layer blob.

    Mirrors :func:`bucket_store.blobs_v1_context_path` shape:
    ``blobs/v1/<project>/context/<hh>/<hash>.json.gz``.
    """

    digest_hex = layer_id.split(":", 1)[1] if ":" in layer_id else layer_id
    return (
        f"blobs/v1/{_hf_path_part(project_slug)}/context/"
        f"{digest_hex[:2]}/{digest_hex}.json.gz"
    )


# ``unquote`` re-export retained for symmetry with the writer side; not
# used here but kept available for any consumer that needs to reverse
# the path-part quoting (e.g. tests).
_unquote = unquote
