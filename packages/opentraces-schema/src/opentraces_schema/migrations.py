"""Registered forward-migrations for TraceRecord raw dicts.

VERSION-POLICY.md reserved this module for the day a breaking schema change
landed and needed an explicit migration. That day is the 0.3.x -> 0.6.0
upgrade: schema 0.6.0 removed ``Outcome.patch`` (the session unified diff) in
favour of the ``TraceRecord.patches[]`` spine. A record captured at schema
0.3.0 carries ``outcome.patch`` and has neither ``patches[]`` nor a trail, so a
bare ``TraceRecord.model_validate`` silently drops the diff (Pydantic
``extra="ignore"``).

``migrate_record`` is the single source of truth for upgrading a raw record
dict before validation. Both the HuggingFace shard migration
(``HFUploader.migrate_outdated_shards``) and the bucket-adoption path call it,
so legacy devtime data survives the upgrade by exactly one code path.

Contract: ``migrate_record`` is **additive**, **idempotent**, and never mutates
its input. Running it twice is byte-identical to running it once, because the
reconstructed ``patch_id`` is content-addressed and reconstruction is skipped
when ``patches[]`` is already populated.
"""
from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

# Provenance markers stamped on reconstructed artifacts so downstream consumers
# can tell a migrated patch from a hook-captured one.
RECONSTRUCTED_CAPTURE_METHOD = "legacy_outcome_patch_migration"
RECONSTRUCTED_LIMITATION = "reconstructed_from_outcome_patch"

_FILE_HEADER_RE = re.compile(r"^--- (?P<old>.+)$")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?P<new>.+)$")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_ab_prefix(path: str) -> str:
    """``a/foo.py`` / ``b/foo.py`` -> ``foo.py``; leaves other paths alone."""
    path = path.strip().split("\t", 1)[0]  # drop any trailing timestamp
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def reconstruct_patches_from_unified_diff(
    diff: str, *, commit_sha: str | None = None
) -> list[dict[str, Any]]:
    """Parse a unified diff into one ``Patch`` dict per file.

    Deterministic and content-addressed: ``patch_id`` is the sha256 of the
    file path plus that file's diff block, so the same diff always yields the
    same ids. Returns ``[]`` when the text is not a recognisable unified diff
    (the caller then falls back to ``metadata.legacy.patch``).
    """
    if not diff or not diff.strip():
        return []

    lines = diff.splitlines()
    # Split into per-file blocks on each '--- ' header.
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if _FILE_HEADER_RE.match(line):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append(current)

    patches: list[dict[str, Any]] = []
    for block in blocks:
        old_path = _strip_ab_prefix(block[0][len("--- ") :]) if block else ""
        new_path = ""
        for line in block[1:]:
            m = _NEW_FILE_RE.match(line)
            if m:
                new_path = _strip_ab_prefix(m.group("new"))
                break
        # Prefer the post-image path; fall back to the pre-image (deletes).
        file_path = new_path or old_path
        if not file_path or file_path == "/dev/null":
            file_path = old_path if old_path and old_path != "/dev/null" else new_path
        if not file_path or file_path == "/dev/null":
            continue
        block_text = "\n".join(block)
        patch: dict[str, Any] = {
            "patch_id": _sha256(f"{file_path}\n{block_text}"),
            "file_path": file_path,
            "capture_method": [RECONSTRUCTED_CAPTURE_METHOD],
            "limitations": [RECONSTRUCTED_LIMITATION],
        }
        patches.append(patch)
    return patches


def migrate_record(raw: dict[str, Any], target_version: str | None = None) -> dict[str, Any]:
    """Forward-migrate a raw TraceRecord dict so the current model loads it
    without data loss. Additive, idempotent, non-mutating.

    Current transforms:
      * 0.6.0 removal of ``Outcome.patch`` -> reconstruct ``patches[]`` from the
        legacy unified diff (preserving the raw diff under
        ``metadata.legacy.patch`` as a fallback), then drop the dead field.
    """
    if not isinstance(raw, dict):
        return raw
    out = copy.deepcopy(raw)

    outcome = out.get("outcome")
    legacy_patch = outcome.get("patch") if isinstance(outcome, dict) else None
    if legacy_patch:
        # Always preserve the raw diff so nothing is lost even if parsing fails.
        metadata = out.setdefault("metadata", {})
        if isinstance(metadata, dict):
            legacy = metadata.setdefault("legacy", {})
            if isinstance(legacy, dict):
                legacy.setdefault("patch", legacy_patch)

        # Reconstruct patches[] only when not already present (idempotent).
        if not out.get("patches"):
            reconstructed = reconstruct_patches_from_unified_diff(
                legacy_patch,
                commit_sha=(outcome.get("commit_sha") if isinstance(outcome, dict) else None),
            )
            if reconstructed:
                out["patches"] = reconstructed

        # Drop the dead field (Pydantic would ignore it anyway; explicit is
        # cleaner and keeps the migrated dict honest to the 0.6.0 shape).
        if isinstance(outcome, dict):
            outcome.pop("patch", None)

    return out
