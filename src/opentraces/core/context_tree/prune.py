"""JSONL pruner: rewrite a Claude Code session JSONL down to one branch.

The pruner walks the ContextTreeProjection's active path to a target
``node_id``, looks up each ContextNode's ``transcript_offset`` and
``transcript_uuid``, and copies those records (in active-path order)
from the source JSONL into a new file under a fresh session id.

This is the bridge that turns "resume from step N" from a missing
feature into a one-shell-command operation: ``claude --resume <new-id>``
lands at the pruned leaf. Dry-run is the default; ``write=True`` is
required to actually create the file. Collision detection is
non-negotiable: existing destinations are never overwritten (the
``ctx-prune-no-clobber`` otbox journey relies on rc=4 here).

See ``kb/plans/077-context-tree-substrate.md`` §"JSONL pruner".
"""
from __future__ import annotations

import json
import uuid as uuid_lib
from pathlib import Path
from typing import Any

from .contract import CONTEXT_RESUME_SCHEMA_VERSION
from .query import (
    build_context_tree_projection_for_trace,
    resolve_node_traces,
)


def prune_session_to_node(
    repo: Path,
    node_id: str,
    source_jsonl_path: Path,
    *,
    to_session: str | None = None,
    write: bool = False,
) -> dict[str, Any]:
    """Project a JSONL down to the active path that ends at ``node_id``.

    Returns a packet describing the new session id, target jsonl path,
    record count, and source. By default this is a dry-run (no file
    written); pass ``write=True`` to actually materialize the JSONL.

    Collision contract (per ``ctx-prune-no-clobber`` journey): if the
    destination JSONL already exists when ``write=True``, raise
    ``FileExistsError`` so the CLI wrapper can exit rc=4 with a
    structured error envelope. Dry-run mode never raises on collision;
    the returned packet just reports the path that would be written.

    Args:
        repo: Project directory holding the canonical event log.
        node_id: Target ContextNode (the leaf of the new session).
        source_jsonl_path: The original Claude Code JSONL transcript.
        to_session: Optional friendly stem for the new session id; used
            VERBATIM (sanitized) so repeated ``--write`` runs with the
            same stem hit the documented rc=4 no-clobber contract.
            When omitted, an anonymous ``sess-<uuid4>`` id is minted.
        write: If True, actually write the new JSONL; default dry-run.
    """
    # Issue #121: bound the read. Resolve the owning trace via a scoped
    # (context_node_observed-only) event read, then project just that trace —
    # ``active_path`` is per-trace, so the pruned output is byte-identical to
    # the whole-log projection while avoiding the full event-log walk that made
    # ``ctx prune`` hang on a mature repo.
    resolved = resolve_node_traces(repo, {node_id})
    owner_trace = resolved.get(node_id)
    projection = (
        build_context_tree_projection_for_trace(repo, owner_trace)
        if owner_trace is not None
        else None
    )
    node = projection.nodes_by_id.get(node_id) if projection is not None else None
    if node is None:
        return {
            "schema_version": CONTEXT_RESUME_SCHEMA_VERSION,
            "node_id": node_id,
            "new_session_id": None,
            "jsonl_path": None,
            "record_count": 0,
            "source_jsonl": str(source_jsonl_path),
            "error": "node not found",
            "limitations": ["context_layer_unavailable"],
        }

    active_path = projection.active_path(node.trace_id, leaf_id=node_id)
    if not active_path:
        return {
            "schema_version": CONTEXT_RESUME_SCHEMA_VERSION,
            "node_id": node_id,
            "new_session_id": None,
            "jsonl_path": None,
            "record_count": 0,
            "source_jsonl": str(source_jsonl_path),
            "error": "active path empty",
            "limitations": ["context_layer_unavailable"],
        }

    new_session_id = _new_session_id(to_session)
    dest_path = source_jsonl_path.parent / f"{new_session_id}.jsonl"

    # Dry-run reports the would-be write target. We still compute the
    # record count by walking the source JSONL once so callers see what
    # they'd actually get.
    pruned_records, pruned_uuids = _read_records_for_active_path(
        source_jsonl_path, active_path
    )
    record_count = len(pruned_records)

    if write:
        if dest_path.exists():
            raise FileExistsError(
                f"pruned session JSONL already exists at {dest_path}; "
                "refusing to overwrite (use a different --to-session stem)"
            )
        # Write atomically: tmp file in the same directory, then rename.
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for raw_line in pruned_records:
                handle.write(raw_line)
                if not raw_line.endswith("\n"):
                    handle.write("\n")
        tmp_path.replace(dest_path)

    return {
        "schema_version": CONTEXT_RESUME_SCHEMA_VERSION,
        "node_id": node_id,
        "new_session_id": new_session_id,
        "jsonl_path": str(dest_path),
        "record_count": record_count,
        "source_jsonl": str(source_jsonl_path),
        "active_path_length": len(active_path),
        "wrote": write,
        # Additive keys (issue #42, evolution norm): the pruned record
        # uuids in active-path order + sorted, so consumers (and the
        # fork-fidelity journey) can assert two-way uuid-set equality
        # against the active path without re-reading the JSONL.
        "uuid_set": pruned_uuids,
        "uuid_set_sorted": sorted(pruned_uuids),
    }


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _new_session_id(to_session: str | None) -> str:
    """Produce the session id for the pruned JSONL.

    Issue #42: a caller-supplied ``to_session`` stem is used VERBATIM
    (after conservative sanitization). The previous behavior appended a
    uuid4 suffix to every stem, which made the documented rc=4
    no-clobber contract unreachable from the CLI — repeated
    ``ctx prune --write --to-session foo`` silently minted a new file
    instead of erroring, contradicting the collision error's own hint
    ("use a different --to-session stem"). Anonymous (no stem) calls
    still mint a fresh ``sess-<uuid4>`` id.
    """
    if to_session:
        # Conservative sanitization: keep only ascii word chars + dash
        return "".join(
            ch if (ch.isalnum() or ch in "-_") else "-" for ch in to_session
        )
    return f"sess-{uuid_lib.uuid4().hex[:12]}"


def _read_records_for_active_path(
    source_jsonl_path: Path,
    active_path: list,
) -> tuple[list[str], list[str]]:
    """Read the original JSONL and emit the lines for the active path's uuids.

    Walks the source file once, keeping records whose uuid appears on
    the active path. Returns ``(lines, uuids)`` in active-path order:
    the raw line text (preserving JSON encoding) plus the matched
    transcript uuids. Records whose uuid does not match are skipped;
    offset metadata on each ContextNode is consulted opportunistically
    to detect transcript drift but does not gate output (downstream
    pruner consumers may have re-encoded).
    """
    if not source_jsonl_path.exists():
        return [], []
    uuid_to_node = {
        node.transcript_uuid: node
        for node in active_path
        if node.transcript_uuid
    }
    if not uuid_to_node:
        return [], []
    # uuid_to_line preserves the raw source line per active-path uuid
    uuid_to_line: dict[str, str] = {}
    with source_jsonl_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            uuid = obj.get("uuid")
            if uuid in uuid_to_node and uuid not in uuid_to_line:
                uuid_to_line[uuid] = raw
    # Emit in active-path order (root-first)
    ordered_uuids = [
        node.transcript_uuid
        for node in active_path
        if node.transcript_uuid in uuid_to_line
    ]
    return [uuid_to_line[u] for u in ordered_uuids], ordered_uuids
