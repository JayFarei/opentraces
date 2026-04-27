"""Trace Trails object identity and resource references.

Machine JSON uses typed object refs. Graph endpoints and resolver keys use
canonical ``ot://`` refs. Short handles are display-only.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any
from urllib.parse import quote, unquote, urlparse

ID_ALGORITHM = "sha256"
TRACE_PATCH_CANONICALIZATION = "opentraces.trace_patch.v1"
GIT_ANCHOR_CANONICALIZATION = "opentraces.git_anchor.v1"
TRACE_SLICE_CANONICALIZATION = "opentraces.trace_slice.v1"
SNAPSHOT_CANONICALIZATION = "opentraces.trace_snapshot.v1"

DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

DISPLAY_PREFIX = {
    "trace": "t",
    "trace_step": "step",
    "trace_patch": "tp",
    "git_anchor": "ga",
    "trace_slice": "ts",
    "trace_snapshot": "snap",
    "git_commit": "c",
    "source_session": "s",
}


def canonical_json(data: Any) -> str:
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def content_digest(canonicalization: str, material: dict[str, Any]) -> str:
    preimage = f"{canonicalization}\0{canonical_json(material)}"
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def content_ref(
    *,
    kind: str,
    canonicalization: str,
    material: dict[str, Any],
    relation: str | None = None,
) -> dict[str, Any]:
    return object_ref(
        kind=kind,
        id=content_digest(canonicalization, material),
        id_format="digest",
        algorithm=ID_ALGORITHM,
        canonicalization=canonicalization,
        relation=relation,
    )


def object_ref(
    *,
    kind: str,
    id: str,
    id_format: str,
    algorithm: str | None = None,
    canonicalization: str | None = None,
    provider: str | None = None,
    relation: str | None = None,
    repo_ref: str | None = None,
    parent_ref: str | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": kind,
        "id": id,
        "id_format": id_format,
        "ref": resource_ref(
            kind=kind,
            id=id,
            id_format=id_format,
            algorithm=algorithm,
            repo_ref=repo_ref,
            parent_ref=parent_ref,
        ),
        "display_id": display_id(kind, id),
    }
    if algorithm:
        out["algorithm"] = algorithm
    if canonicalization:
        out["canonicalization"] = canonicalization
    if provider:
        out["provider"] = provider
    if relation:
        out["relation"] = relation
    if repo_ref:
        out["repo_ref"] = repo_ref
    if parent_ref:
        out["parent_ref"] = parent_ref
    if index is not None:
        out["index"] = index
    return out


def trace_ref(trace_id: str) -> dict[str, Any]:
    return object_ref(
        kind="trace",
        id=trace_id,
        id_format="uuid" if _is_uuid(trace_id) else "trace_id",
    )


def source_session_ref(provider: str, session_id: str) -> dict[str, Any]:
    return object_ref(
        kind="source_session",
        id=session_id,
        id_format="provider_scoped",
        provider=provider,
    )


def trace_step_ref(trace_id: str, step_id: str, index: int | None = None) -> dict[str, Any]:
    return object_ref(
        kind="trace_step",
        id=step_id,
        id_format="trace_scoped",
        parent_ref=trace_ref(trace_id)["ref"],
        index=index,
    )


def trace_patch_ref(id: str) -> dict[str, Any]:
    return object_ref(
        kind="trace_patch",
        id=normalize_id(id),
        id_format="digest",
        algorithm=ID_ALGORITHM,
        canonicalization=TRACE_PATCH_CANONICALIZATION,
    )


def git_anchor_ref(id: str, relation: str = "anchored_in_git") -> dict[str, Any]:
    return object_ref(
        kind="git_anchor",
        id=normalize_id(id),
        id_format="digest",
        algorithm=ID_ALGORITHM,
        canonicalization=GIT_ANCHOR_CANONICALIZATION,
        relation=relation,
    )


def trace_slice_ref(id: str) -> dict[str, Any]:
    return object_ref(
        kind="trace_slice",
        id=normalize_id(id),
        id_format="digest",
        algorithm=ID_ALGORITHM,
        canonicalization=TRACE_SLICE_CANONICALIZATION,
    )


def trace_snapshot_ref(id: str) -> dict[str, Any]:
    return object_ref(
        kind="trace_snapshot",
        id=normalize_id(id),
        id_format="digest",
        algorithm=ID_ALGORITHM,
        canonicalization=SNAPSHOT_CANONICALIZATION,
    )


def git_commit_ref(
    commit_id: dict[str, str] | str | None,
    *,
    repo_ref: str = "ot://repo/local",
) -> dict[str, Any] | None:
    if commit_id is None:
        return None
    if isinstance(commit_id, dict):
        algorithm = commit_id.get("algo") or commit_id.get("algorithm") or "sha1"
        hex_id = commit_id.get("hex") or commit_id.get("id")
    else:
        algorithm = "sha1" if len(commit_id) == 40 else ID_ALGORITHM
        hex_id = commit_id
    if not hex_id:
        return None
    return object_ref(
        kind="git_commit",
        id=hex_id.lower(),
        id_format="git_oid",
        algorithm=algorithm,
        repo_ref=repo_ref,
    )


def resource_ref(
    *,
    kind: str,
    id: str,
    id_format: str | None = None,
    algorithm: str | None = None,
    repo_ref: str | None = None,
    parent_ref: str | None = None,
) -> str:
    safe_id = quote(id, safe="-._~")
    if kind == "trace":
        return f"ot://trace/{safe_id}"
    if kind == "trace_step":
        if not parent_ref:
            raise ValueError("trace_step refs require parent_ref")
        return f"{parent_ref}/steps/{safe_id}"
    if kind == "source_session":
        return f"ot://source-session/{safe_id}"
    if kind == "trace_patch":
        return f"ot://trace-patch/{algorithm or ID_ALGORITHM}/{safe_id}"
    if kind == "git_anchor":
        return f"ot://git-anchor/{algorithm or ID_ALGORITHM}/{safe_id}"
    if kind == "trace_slice":
        return f"ot://trace-slice/{algorithm or ID_ALGORITHM}/{safe_id}"
    if kind == "trace_snapshot":
        return f"ot://trace-snapshot/{algorithm or ID_ALGORITHM}/{safe_id}"
    if kind == "git_commit":
        root = repo_ref or "ot://repo/local"
        return f"{root}/git/commit/{algorithm or 'sha1'}/{safe_id}"
    if id_format == "digest":
        return f"ot://{kind.replace('_', '-')}/{algorithm or ID_ALGORITHM}/{safe_id}"
    return f"ot://{kind.replace('_', '-')}/{safe_id}"


def display_id(kind: str, id: str, length: int = 8) -> str:
    prefix = DISPLAY_PREFIX.get(kind, kind.replace("_", "-"))
    return f"{prefix}:{id[:length]}"


def normalize_id(value: str | dict[str, Any] | None) -> str:
    if isinstance(value, dict):
        raw = value.get("id") or value.get("hex") or ""
    else:
        raw = value or ""
    raw = str(raw)
    if raw.startswith("ot://"):
        parsed = urlparse(raw)
        segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
        if parsed.netloc in {
            "trace-patch",
            "git-anchor",
            "trace-slice",
            "trace-snapshot",
        } and len(segments) >= 2:
            return segments[1].lower()
        if parsed.netloc == "repo" and len(segments) >= 5 and segments[-3] == "commit":
            return segments[-1].lower()
        if segments:
            return segments[-1].lower()
    if ":" in raw:
        return raw.rsplit(":", 1)[-1].lower()
    return raw.lower()


def id_from_payload(payload: dict[str, Any], kind: str) -> str | None:
    ref_key = f"{kind}_ref"
    id_key = f"{kind}_id"
    if isinstance(payload.get(ref_key), dict) and payload[ref_key].get("id"):
        return normalize_id(payload[ref_key]["id"])
    if payload.get(id_key):
        return normalize_id(payload[id_key])
    return None


def ref_from_payload(payload: dict[str, Any], kind: str) -> dict[str, Any] | None:
    ref_key = f"{kind}_ref"
    existing = payload.get(ref_key)
    if isinstance(existing, dict) and existing.get("kind") and existing.get("id"):
        return existing
    object_id = id_from_payload(payload, kind)
    if not object_id:
        return None
    if kind == "trace_patch":
        return trace_patch_ref(object_id)
    if kind == "git_anchor":
        return git_anchor_ref(object_id, relation=payload.get("relation") or "anchored_in_git")
    if kind == "trace_slice":
        return trace_slice_ref(object_id)
    if kind == "trace_snapshot":
        return trace_snapshot_ref(object_id)
    return None


def assert_digest(value: str) -> str:
    value = normalize_id(value)
    if not DIGEST_RE.fullmatch(value):
        raise ValueError("digest id must be a 64 character lowercase hex string")
    return value


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return True
