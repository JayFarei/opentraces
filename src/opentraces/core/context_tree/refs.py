"""Lazy ContextLayer facade for bucket-backed projections.

Plan 080 Resolution D — Track C1. The sealed Context Tree substrate
(``models.py``, ``contract.py``) is untouched; this module is purely
additive. The query projection (``query.py``) gains a ``lazy=True``
mode that returns ``ContextLayerRef`` instances in ``layers_by_id``
instead of fully materialized ``ContextLayer`` objects.

Motivation: ``ctx list`` and ``bucket status`` only need the layer
catalogue (ids + metadata), never the content. Materializing every
layer's content (gunzip + json.loads) for a 10k-trace bucket is a
catastrophic eager-load. ``ContextLayerRef`` defers the gunzip until
the consumer actually reads ``.content``.

Plan 080 §17 R-080-2 is the hard gate: ``bench-ctx-list-10k-traces``
must trigger zero blob file opens. This module is what makes that
enforceable.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from .models import ContextLayer

__all__ = ["ContextLayerRef"]


@dataclass
class ContextLayerRef:
    """Thin handle over a layer's identity + on-disk blob location.

    Construction is cheap (no I/O). The blob is gunzipped + parsed only
    when ``content`` is accessed; subsequent reads hit the cached value
    on the instance.

    Constructed via either the explicit constructor (when the caller
    knows the bucket blob path) or ``from_event_payload`` (when the
    caller has a ``context_layer_captured`` event payload + the bucket
    root + project slug).

    Carries the metadata fields the projection already knows from the
    event payload (``layer_type``, ``capture_method``, ``completeness``,
    ``byte_count``) so consumers don't need to materialize content
    to render a catalogue row.
    """

    layer_id: str
    bucket_path: Path
    layer_type: str | None = None
    capture_method: str | None = None
    completeness: str | None = None
    byte_count: int | None = None
    # Internal cache slot; populated lazily by ``content``.
    _content_cache: dict[str, Any] | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ #
    # Lazy content access
    # ------------------------------------------------------------------ #

    @cached_property
    def content(self) -> dict[str, Any]:
        """Gunzip + json.loads the on-disk blob; cached after first call.

        Plan 080 §4: ``bucket/blobs/v1/<project>/context/<hh>/<hash>.json.gz``
        contains the layer blob envelope; the layer content lives under
        the ``content`` key. We return the content dict (i.e. what
        ``ContextLayer.content`` holds), not the wrapping envelope.

        Raises:
            FileNotFoundError: if the blob is missing (orphan layer_id).
            json.JSONDecodeError: if the blob is corrupt.
        """
        raw = gzip.decompress(self.bucket_path.read_bytes())
        envelope = json.loads(raw.decode("utf-8"))
        # The bucket blob envelope wraps the layer; the projection
        # returns the layer content (not the envelope).
        if isinstance(envelope, dict) and "content" in envelope:
            return envelope["content"]
        return envelope  # tolerate legacy / direct content blobs

    @property
    def resolved(self) -> bool:
        """Whether ``content`` has been materialized (no I/O triggered)."""
        # ``cached_property`` stores the resolved value under the
        # attribute name on the instance dict; check without triggering.
        return "content" in self.__dict__

    def materialize(self) -> ContextLayer:
        """Return a fully-realized ``ContextLayer`` model.

        Materializes content if not already cached, then validates
        against the sealed ``ContextLayer`` pydantic model (which
        recomputes the content hash and verifies ``layer_id`` matches).

        Use sparingly: this is the eager path. ``ctx list`` /
        ``bucket status`` should never call this.
        """
        if self.layer_type is None or self.capture_method is None or self.completeness is None:
            raise ValueError(
                "ContextLayerRef cannot materialize without "
                "layer_type/capture_method/completeness metadata; "
                "construct via from_event_payload or supply explicit fields"
            )
        return ContextLayer(
            layer_id=self.layer_id,
            layer_type=self.layer_type,  # type: ignore[arg-type]
            content=self.content,
            completeness=self.completeness,  # type: ignore[arg-type]
            capture_method=self.capture_method,  # type: ignore[arg-type]
        )

    def __repr__(self) -> str:
        return (
            f"ContextLayerRef(layer_id={self.layer_id!r}, "
            f"resolved={self.resolved})"
        )

    # ------------------------------------------------------------------ #
    # Factory: from event payload
    # ------------------------------------------------------------------ #

    @classmethod
    def from_event_payload(
        cls,
        payload: dict[str, Any],
        bucket_root: Path | None,
        *,
        project_slug: str | None = None,
    ) -> "ContextLayerRef":
        """Build a ref from a ``context_layer_captured`` event payload.

        The event payload carries the ``ContextLayer`` shape (layer_id,
        layer_type, capture_method, completeness, content). In a
        bucket-backed lazy projection, ``content`` is dropped from the
        returned ref's cache — it's re-loaded from disk via ``bucket_path``
        only when consumers ask for it. The blob path is computed from
        ``bucket_root``, ``project_slug``, and ``layer_id`` using the
        layout in plan 080 §4: ``<bucket_root>/blobs/v1/<project>/context/<hh>/<hash>.json.gz``.

        Args:
            payload: a ``context_layer_captured`` event payload dict
                (i.e. ``ContextLayer.model_dump(mode="json")``).
            bucket_root: the bucket directory (``~/.opentraces/projects/<slug>/bucket``);
                blobs hang off ``blobs/v1/<project>/context/...`` from here.
            project_slug: the project slug under ``blobs/v1/``. Required
                whenever ``bucket_root`` is supplied (no implicit default).

        Returns:
            A ``ContextLayerRef`` whose ``content`` is unresolved. If
            ``bucket_root`` is None the ref points at an unresolvable path
            (``Path('')``) and ``content`` access will raise — useful for
            test fixtures or contexts that only need the metadata.
        """
        layer_id = payload["layer_id"]
        layer_type = payload.get("layer_type")
        capture_method = payload.get("capture_method")
        completeness = payload.get("completeness")
        # Optional sizing hint; payload may not carry it. Consumers that
        # want byte_count populate it post-construction from the manifest.
        byte_count = payload.get("byte_count")

        if bucket_root is None:
            blob_path = Path("")
        else:
            if project_slug is None:
                raise ValueError(
                    "from_event_payload requires project_slug when bucket_root is set"
                )
            blob_path = _blob_path_for_layer(bucket_root, project_slug, layer_id)

        return cls(
            layer_id=layer_id,
            bucket_path=blob_path,
            layer_type=layer_type,
            capture_method=capture_method,
            completeness=completeness,
            byte_count=byte_count,
        )


# --------------------------------------------------------------------------- #
# Path helpers (mirror bucket_store layout to avoid a circular import)
# --------------------------------------------------------------------------- #


def _blob_path_for_layer(
    bucket_root: Path, project_slug: str, layer_id: str
) -> Path:
    """Compute the per-project blob path for ``layer_id``.

    Mirrors ``bucket_store.blobs_v1_context_path`` (plan 080 §4) without
    importing it — keeps this module dependency-free of the bucket
    writer layer.
    """
    digest_hex = layer_id.split(":", 1)[1] if ":" in layer_id else layer_id
    return (
        bucket_root
        / "blobs"
        / "v1"
        / project_slug
        / "context"
        / digest_hex[:2]
        / f"{digest_hex}.json.gz"
    )
