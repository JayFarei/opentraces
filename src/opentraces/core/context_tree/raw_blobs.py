"""Per-message raw content blobs — the materialized text behind a ``content_hash``.

A Context Tree ``messages`` layer is a *manifest*: an ordered list of
``{role, uuid, parent_uuid, content_hash}`` entries (see
``capture/claude_code/context_tree_capture.py::build_messages_layer`` and the
OTel emitter). Historically the ``content_hash`` was a dangling pointer — the
actual message text was never persisted, so ``ctx`` could report *which*
messages a step saw but never *what* they said, and re-storing every step's
full message list inline blew storage up to O(N²) on long traces.

This module closes that gap with one content-addressed write, shared by both
capture paths (JSONL parser and OTel receiver):

* ``message_content_hash(payload)`` — the canonical hash both paths use. It is
  byte-for-byte the function the JSONL path already used
  (``_sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))``), so
  existing manifests keep resolving and no ``content_hash`` / ``layer_id``
  changes for already-captured traces.
* ``write_message_content_blob`` / ``materialize_message_blobs`` — write the
  *exact canonical-JSON bytes that were hashed*, gzipped deterministically, to
  ``blobs/v1/<slug>/raw/<hh>/<hash>.json.gz``. Content-addressed: a message
  shared across N steps (or across traces) is stored once. No
  ``objects/raw/v1/sources/`` link file is created, so these blobs are
  invisible to ``bucket_digest`` (only link files feed ``raw_sources.digest``,
  and ``context_trees`` is excluded from the digest entirely).
* ``deref_message_content`` / ``deref_message_payload`` — resolve a
  ``content_hash`` back to the canonical bytes / parsed message. Round-trip
  holds by construction: ``"sha256:" + sha256(deref(h)).hexdigest() == h``.

The ``blobs/v1/<slug>/raw/**`` family is already wired into remote sync
(``_PUSH_PASS_BLOBS``, lazy on pull) — see ``core/bucket_remote.py`` — so
materialized message content rides the existing bucket transport.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Iterable

from .._bucket_io import _atomic_write_gzip, _read_gzip_bytes
from ..bucket_layout import blobs_v1_raw_path

logger = logging.getLogger("opentraces.context_tree.raw_blobs")

# Message-content blobs live under the raw/ blob family with a gzip suffix.
RAW_BLOB_SUFFIX = ".json.gz"


def _canonical_message_text(payload: Any) -> str:
    """Canonical JSON string of one message payload — the bytes that get hashed.

    Matches the JSONL path's historical content_hash input exactly:
    ``json.dumps(message_payload, sort_keys=True, ensure_ascii=False)`` with
    Python-default separators (NOT the compact separators used by
    ``models.canonical_json``). This is a deliberate contract: the messages
    manifest hash and the raw blob both derive from this string, so re-hashing
    a stored blob reproduces its ``content_hash``.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def message_content_hash(payload: Any) -> str:
    """Return ``sha256:<hex>`` for one message payload.

    Shared by the JSONL parser and the OTel emitter so a message identical
    within a path collapses to one blob. (Cross-path equality is not a goal:
    the JSONL ``message`` sub-object and the OTel wire message differ
    structurally, and normalizing them would change every existing JSONL
    ``content_hash``. Within-path dedup — the source of the O(N) win — holds.)
    """
    return "sha256:" + hashlib.sha256(
        _canonical_message_text(payload).encode("utf-8")
    ).hexdigest()


def write_message_content_blob(project_slug: str, payload: Any) -> str:
    """Materialize one message payload as a content-addressed raw blob.

    Returns the ``content_hash``. Idempotent and content-addressed: the blob
    name is the hash, and ``_atomic_write_gzip`` is a no-op when the bytes
    already match. The stored bytes are ``gzip(canonical_text)`` with
    ``mtime=0`` so two machines produce byte-identical blobs.
    """
    text = _canonical_message_text(payload)
    content_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    path = blobs_v1_raw_path(project_slug, content_hash, suffix=RAW_BLOB_SUFFIX)
    _atomic_write_gzip(path, text.encode("utf-8"))
    return content_hash


def materialize_message_blobs_incremental(
    project_slug: str, payloads: Iterable[Any], *, seen: dict[str, None]
) -> int:
    """Write raw blobs for ``payloads``, deduping against a caller-owned ``seen`` set.

    The streaming counterpart of :func:`materialize_message_blobs` (issue #252):
    the caller threads one ``seen`` dict across many small ``payloads`` batches
    (e.g. per-step, one step at a time) so peak memory never holds the whole
    session's messages at once, while the dedup semantics are identical to a
    single one-shot call over the concatenation — a hash already in ``seen`` (from
    an earlier batch *or* an earlier payload in this batch) is skipped; the first
    occurrence is written. ``seen`` is mutated in place; returns the number of
    blobs written by THIS call.
    """
    written = 0
    for payload in payloads:
        try:
            text = _canonical_message_text(payload)
            content_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            logger.debug("skipping unhashable message payload")
            continue
        if content_hash in seen:
            continue
        seen[content_hash] = None
        try:
            path = blobs_v1_raw_path(project_slug, content_hash, suffix=RAW_BLOB_SUFFIX)
            _atomic_write_gzip(path, text.encode("utf-8"))
            written += 1
        except OSError as exc:  # pragma: no cover - disk hiccup
            logger.warning("could not write message blob %s: %s", content_hash, exc)
    return written


def materialize_message_blobs(
    project_slug: str, payloads: Iterable[Any]
) -> dict[str, Any]:
    """Write raw blobs for every message payload; dedup by content hash.

    Returns ``{written, deduped, content_hashes}`` where ``content_hashes`` is
    the de-duplicated set actually persisted. Errors on individual writes are
    logged and swallowed so capture never fails on a blob-store hiccup.
    """
    seen: dict[str, None] = {}
    written = materialize_message_blobs_incremental(project_slug, payloads, seen=seen)
    return {
        "written": written,
        "deduped": len(seen),
        "content_hashes": list(seen.keys()),
    }


def message_content_blob_path(project_slug: str, content_hash: str) -> Path:
    """Resolve the local path for a message-content blob (may not exist)."""
    return blobs_v1_raw_path(project_slug, content_hash, suffix=RAW_BLOB_SUFFIX)


def deref_message_content(project_slug: str, content_hash: str) -> bytes | None:
    """Return the canonical-JSON bytes behind a ``content_hash``, or ``None``.

    Round-trip invariant: ``"sha256:" + sha256(returned_bytes).hexdigest()``
    equals ``content_hash``.
    """
    path = blobs_v1_raw_path(project_slug, content_hash, suffix=RAW_BLOB_SUFFIX)
    if not path.exists():
        return None
    try:
        return _read_gzip_bytes(path)
    except OSError:  # pragma: no cover
        return None


def deref_message_payload(project_slug: str, content_hash: str) -> Any | None:
    """Return the parsed message payload behind a ``content_hash``, or ``None``."""
    raw = deref_message_content(project_slug, content_hash)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:  # pragma: no cover
        return None
