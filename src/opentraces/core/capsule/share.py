"""Share layer: mint URL, write local dir, publish to HF, clipboard, gh issue.

Codex/eng critical: do NOT reuse ``bucket_remote.remote_push`` (whole-bucket
PRIVATE sync). ``publish_capsule`` uploads ONLY ``capsule.json`` + ``capsule.md``
to a public HF dataset repo under ``capsules/v1/<id>/`` and returns a
commit-SHA-pinned URL so the artifact a maintainer resolves next month is
byte-identical to what was shared.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import re
import shutil
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any, Sequence

from .contract import validate_capsule
from .render import render_capsule_markdown, render_issue_body

HF_HOST = "https://huggingface.co"
# #198 — the human-facing capsule microsite (the #199 render worker resolves the
# already-public, sha-pinned HF blob). The URL is minted here so the envelope
# records the address a person opens, next to the raw-blob agent URL.
VIEWER_HOST = "https://capsules.opentraces.ai"
CAPSULE_PREFIX = "capsules/v1"
BUNDLE_FILENAME = "capsule.bundle.tar.gz"


# --------------------------------------------------------------------------- #
# URL minting + local artifact
# --------------------------------------------------------------------------- #


def _hf_repo_id(url_or_id: str) -> str:
    """Accept hf://owner/repo, https://huggingface.co/datasets/owner/repo, owner/repo."""

    s = url_or_id.strip()
    s = re.sub(r"^hf://", "", s)
    m = re.search(r"huggingface\.co/datasets/([^/]+/[^/?#]+)", s)
    if m:
        return m.group(1)
    return s.strip("/")


def mint_capsule_url(repo_id: str, capsule_id: str, *, revision: str = "main") -> str:
    """The agent-consumable URL: a single self-contained capsule.json blob.

    ``/resolve/<revision>/`` serves the raw bytes (content-type application/json).
    Pin ``revision`` to the publish commit sha for immutability.
    """

    rid = _hf_repo_id(repo_id)
    return f"{HF_HOST}/datasets/{rid}/resolve/{revision}/{CAPSULE_PREFIX}/{capsule_id}/capsule.json"


def human_capsule_url(repo_id: str, capsule_id: str, *, revision: str = "main") -> str:
    """The human-clickable mirror: HF renders committed markdown as a page."""

    rid = _hf_repo_id(repo_id)
    return f"{HF_HOST}/datasets/{rid}/blob/{revision}/{CAPSULE_PREFIX}/{capsule_id}/capsule.md"


def mint_viewer_url(repo_id: str, capsule_id: str, *, revision: str = "main") -> str:
    """The microsite address a human opens (#198 / #199 render worker).

    The worker resolves the already-public, redacted, sha-pinned HF blob; this is
    a stable, revision-pinned handle recorded in the envelope's share block.
    """

    rid = _hf_repo_id(repo_id)
    return f"{VIEWER_HOST}/{rid}/{capsule_id}?rev={revision}"


# --------------------------------------------------------------------------- #
# Bundle archive hygiene (#157 Part 1): defense-before-detection. ``git archive``
# already honors ``.gitattributes export-ignore`` NATIVELY (no code needed), but
# it happily ships a committed ``.env`` / private key / cert that is NOT marked
# export-ignore. A default member-exclude filter drops those sensitive paths
# BEFORE gzip so they never reach the public dataset.
# --------------------------------------------------------------------------- #

_BUNDLE_EXCLUDE_GLOBS = (".env", ".env.*", "*.pem", "id_rsa*", "*.key", ".netrc")
_BUNDLE_EXCLUDE_DIRS = (".aws",)


def _member_is_sensitive(name: str) -> bool:
    """True when an archive member path is a committed secret/key by convention."""

    import fnmatch

    parts = Path(name).parts
    if any(part in _BUNDLE_EXCLUDE_DIRS for part in parts):
        return True
    base = Path(name).name
    return any(fnmatch.fnmatch(base, glob) for glob in _BUNDLE_EXCLUDE_GLOBS)


def _filter_bundle_tar(tar_bytes: bytes) -> tuple[bytes, list[str]]:
    """Drop sensitive members from a raw tar; return ``(filtered_bytes, excluded)``.

    When NOTHING matches the filter the ORIGINAL bytes are returned unchanged, so
    a secret-free bundle stays byte-identical to the raw ``git archive`` output
    (cross-machine reproducibility preserved). Only a bundle that actually carries
    a sensitive member is re-tarred; the re-tar copies each surviving member
    verbatim (same ``TarInfo``, same git-archive mtime) into a deterministic PAX
    tar, so two builds of the same commit remain byte-identical.
    """

    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        if not any(_member_is_sensitive(m.name) for m in tf.getmembers()):
            return tar_bytes, []

    excluded: list[str] = []
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf, tarfile.open(
        fileobj=out, mode="w:", format=tarfile.PAX_FORMAT
    ) as tw:
        for member in tf.getmembers():
            if _member_is_sensitive(member.name):
                excluded.append(member.name)
                continue
            if member.isreg():
                tw.addfile(member, tf.extractfile(member))
            else:
                tw.addfile(member)
    return out.getvalue(), excluded


def build_capsule_bundle(project_dir: Path, sha: str) -> tuple[dict[str, Any], bytes]:
    """git archive the repo at ``sha`` into a deterministic, hygiene-filtered tar.gz.

    Returns ``(bundle_metadata, tar_gz_bytes)``. The bundle makes the capsule
    hermetic: the test runs even when the commit is private / force-pushed away.
    A default member-exclude filter (``.env`` / keys / certs / ``.aws/`` / ``.netrc``)
    drops committed secrets BEFORE gzip (#157). ``.gitattributes export-ignore``
    is honored natively by ``git archive`` already.
    """

    import gzip
    import hashlib

    raw = subprocess.run(
        ["git", "-C", str(project_dir), "archive", "--format=tar", sha],
        capture_output=True, check=False,
    )
    if raw.returncode != 0:
        raise RuntimeError(
            f"git archive failed for {sha} in {project_dir}: "
            f"{raw.stderr.decode('utf-8', 'replace').strip()}"
        )
    filtered_tar, excluded = _filter_bundle_tar(raw.stdout)
    # mtime=0 for byte-identity across machines (matches the bucket convention).
    gz = gzip.compress(filtered_tar, mtime=0)
    meta = {
        "kind": "git_archive",
        "filename": BUNDLE_FILENAME,
        "source_sha": sha,
        "sha256": hashlib.sha256(gz).hexdigest(),
        "size_bytes": len(gz),
        # Additive provenance: which committed-secret members hygiene dropped.
        "excluded_members": excluded,
    }
    return meta, gz


def sibling_bundle_path(capsule_file: Path) -> Path | None:
    """The bundle next to a local capsule.json, if present."""

    cand = Path(capsule_file).parent / BUNDLE_FILENAME
    return cand if cand.exists() else None


def verify_bundle(bundle_path: Path, expected_sha256: str | None) -> bool:
    # #157 H5 — the run/extract HARD gate REQUIRES a pinned hash. An absent/empty
    # expected sha256 FAILS verification, so unbound bundle bytes are never extracted
    # or run: a bundle can only be used when its bytes match a carried hash. (A
    # capsule with no bundle at all never reaches this gate.)
    if not expected_sha256:
        return False
    import hashlib

    return hashlib.sha256(Path(bundle_path).read_bytes()).hexdigest() == expected_sha256


def write_capsule_dir(
    capsule: dict[str, Any],
    dest_root: Path,
    *,
    bundle_bytes: bytes | None = None,
    mini_bucket: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Write capsule.json + capsule.md (+ the bundle when present) under the dir.

    When ``mini_bucket`` (a :func:`build_mini_bucket` result) is supplied the
    scoped, redacted mini-bucket files are written under ``<id>/mini_bucket/``
    (#197). Omitting it keeps the historical write byte-identical.
    """

    cid = capsule["capsule_id"]
    out_dir = Path(dest_root) / CAPSULE_PREFIX / cid
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "capsule.json"
    md_path = out_dir / "capsule.md"
    # Canonical, stable JSON (sorted keys) so the same capsule is byte-identical
    # across machines.
    json_path.write_text(
        json.dumps(capsule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_capsule_markdown(capsule), encoding="utf-8")
    result = {"dir": out_dir, "json": json_path, "md": md_path}
    if bundle_bytes is not None and (capsule.get("bundle") or {}).get("filename"):
        bundle_path = out_dir / capsule["bundle"]["filename"]
        bundle_path.write_bytes(bundle_bytes)
        result["bundle"] = bundle_path
    if mini_bucket is not None:
        result["mini_bucket"] = write_mini_bucket(mini_bucket, out_dir / "mini_bucket")
    return result


# --------------------------------------------------------------------------- #
# Mini-bucket (#197): a scoped, redacted materialization of the source traces'
# companions on the bucket layout conventions, with content-addressed blobs and
# a deterministic digest. The capsule stops being an envelope-inline blob and
# becomes a genuine mini-bucket carried next to capsule.json.
# --------------------------------------------------------------------------- #


def _companion_faces():
    from ..bucket_layout import (
        trace_v1_context_path,
        trace_v1_sources_path,
        trace_v1_trail_path,
    )

    return (
        ("trail", trace_v1_trail_path),
        ("context", trace_v1_context_path),
        ("sources", trace_v1_sources_path),
    )


# #209 (W1): a REAL ``capsule create`` invocation calls ``build_mini_bucket``
# TWICE for the exact same (bucket, slug, trace_ids) — once inside
# ``export.py::export_capsule`` purely to stamp ``mini_bucket_digest`` on the
# envelope, and again from ``cli/capsule.py`` to materialize the actual mini-
# bucket files. On an outlier (>=400 MB decompressed) trail companion each
# call alone can cost tens of seconds, so the un-memoized double call was
# roughly HALF of "capsule seal on an outlier trace takes minutes" — a second
# lever alongside the parallel-dispatch chunking in ``companions.py``, closing
# the same "capsule seal on outlier companions" bug the process-parallel
# change targets, without touching sanitize_companion_dict/redact_companions
# themselves (still exactly one code path for producing the redacted bytes;
# this only skips a provably-identical repeat of that one path within a
# single process). Bounded process-lifetime LRU (not unbounded — each entry
# holds full companion blob bytes, so an unbounded cache across a long-lived
# process such as a test session would grow without limit).
#
# External review on PR #220 (critical 2): the pre-fix key was
# ``(bucket_dir, slug, trace_ids, tool_names)`` — it did NOT cover the raw
# companion CONTENT, so if a companion's bytes changed between two calls
# within one process (a test rewriting a fixture, a concurrent writer) the
# second call would silently return the FIRST call's stale redacted
# bytes/digest instead of re-reading and re-redacting the new content. Two
# changes close this: (1) the cache is now scoped to the tools=None DEFAULT
# path only — the only path every production call site (``export.py`` +
# ``cli/capsule.py``, the exact double-call this cache exists for) actually
# uses, mirroring companions.py's critical-1 fix so there is never a "same
# key, different bound tool instance" collision to worry about; (2) the key
# includes a content digest of every companion face actually read for the
# scope, so any byte change forces a cache miss by construction. Every read
# also returns a defensive deep copy, so a caller mutating a result can never
# corrupt the cached entry for a later hit.
_MINI_BUCKET_CACHE: "OrderedDict[tuple[Any, ...], dict[str, Any]]" = OrderedDict()
_MINI_BUCKET_CACHE_MAXSIZE = 16


def _read_companion_bytes(slug: str, trace_id: str) -> dict[str, bytes]:
    """Read raw (gzip) companion bytes for one trace, keyed by face name.

    A missing companion reads as ``b""`` (mirrors ``_build_mini_bucket_uncached``'s
    own read). Reading here once lets the cache-key content digest and the
    actual build share the same bytes instead of a second disk read per face.
    """
    result: dict[str, bytes] = {}
    for face, path_fn in _companion_faces():
        raw_path = path_fn(slug, trace_id)
        result[face] = raw_path.read_bytes() if raw_path.exists() else b""
    return result


def _mini_bucket_cache_key(
    slug: str,
    trace_ids: Sequence[str],
    raw_by_trace: dict[str, dict[str, bytes]],
) -> tuple[Any, ...]:
    """A hashable projection of ``build_mini_bucket``'s ACTUAL inputs.

    ``project_dir`` is deliberately excluded — the companion paths derive
    solely from ``paths.bucket_dir()`` (a process-global), never from
    ``project_dir`` (see ``build_mini_bucket``'s docstring) — so it carries no
    information the output depends on. ``paths.bucket_dir()`` IS included so a
    test suite that monkeypatches ``OPENTRACES_DIR`` between calls (a
    per-process global mutation) never serves a stale cross-test hit. The
    trailing content-digest tuple is the #220 critical-2 fix: a sha256 of
    each companion face's raw bytes (``None`` for a missing companion), in
    the same (trace, face) order they were read — any content change at all
    changes this tuple, so a stale hit is no longer possible.
    """
    from .. import paths as _paths

    content_digest = tuple(
        hashlib.sha256(raw_by_trace[tid][face]).hexdigest() if raw_by_trace[tid][face] else None
        for tid in trace_ids
        for face, _ in _companion_faces()
    )
    return (str(_paths.bucket_dir()), slug, tuple(trace_ids), content_digest)


def build_mini_bucket(
    project_dir: Path,
    slug: str,
    trace_ids: Sequence[str],
    *,
    tools: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Materialize a scoped, redacted mini-bucket for ``trace_ids``.

    For each trace, reads the raw ``trail/context/sources.jsonl.gz`` companions
    from the bucket layout, re-redacts them through the substrate capability
    (``redact_companions`` → ``sanitize_companion_dict``), and lays them out as
    content-addressed blobs (``blobs/v1/<slug>/<hh>/<sha256>.jsonl.gz``) plus a
    per-trace spine (``traces/v1/<slug>/<trace>/trace.json``) that references the
    companion blobs by content digest. Every trace is INDEPENDENTLY resolvable
    from its spine; ``mini_bucket_digest`` is a pure content hash (gzip- and
    machine-independent). Returns ``{files, manifest, digest, schema_version}``.

    Memoized within one process (#209 W1), scoped to the ``tools=None``
    default-registry path only — every production call site uses exactly
    that path, and an explicit ``tools`` argument (which may carry a bound
    instance with no stable cache identity) always takes the uncached,
    always-correct path instead. The cache key is the bucket root + slug +
    trace_ids + a content digest of every companion byte actually read (#220
    critical 2) — the ONLY inputs the output actually depends on (see
    ``_mini_bucket_cache_key``) — so a caller that (correctly) calls this
    twice for the same scope within one CLI invocation pays the redaction
    cost once, and any in-process content change is never served stale.
    Every return is a defensive deep copy so a caller can freely mutate its
    result without corrupting the cache. ``project_dir`` is accepted for
    symmetry with the export choke point; the companion paths derive from
    ``paths.bucket_dir()`` (which honors test ``OPENTRACES_DIR`` monkeypatching).
    """

    if tools is not None:
        return _build_mini_bucket_uncached(project_dir, slug, trace_ids, tools=tools)

    raw_by_trace = {tid: _read_companion_bytes(slug, tid) for tid in trace_ids}
    cache_key = _mini_bucket_cache_key(slug, trace_ids, raw_by_trace)

    cached = _MINI_BUCKET_CACHE.get(cache_key)
    if cached is not None:
        _MINI_BUCKET_CACHE.move_to_end(cache_key)
        return copy.deepcopy(cached)

    result = _build_mini_bucket_uncached(
        project_dir, slug, trace_ids, tools=None, raw_by_trace=raw_by_trace
    )

    _MINI_BUCKET_CACHE[cache_key] = result
    if len(_MINI_BUCKET_CACHE) > _MINI_BUCKET_CACHE_MAXSIZE:
        _MINI_BUCKET_CACHE.popitem(last=False)
    return copy.deepcopy(result)


def _build_mini_bucket_uncached(
    project_dir: Path,
    slug: str,
    trace_ids: Sequence[str],
    *,
    tools: Sequence[Any] | None = None,
    raw_by_trace: dict[str, dict[str, bytes]] | None = None,
) -> dict[str, Any]:
    """The actual builder — see ``build_mini_bucket`` for the public contract.

    ``raw_by_trace``, when supplied, is the ALREADY-READ raw companion bytes
    per trace (keyed by face) — avoids a second disk read when the caller
    already read them to compute the cache-key content digest.
    """

    from ..bucket_layout import _path_part
    from .companions import redact_companions
    from .contract import (
        MINI_BUCKET_MANIFEST_SCHEMA_VERSION,
        MINI_BUCKET_TRACE_SCHEMA_VERSION,
        compute_mini_bucket_digest,
    )

    files: dict[str, bytes] = {}
    blobs: dict[str, str] = {}
    traces: dict[str, Any] = {}
    trace_digests: dict[str, dict[str, str | None]] = {}

    for tid in trace_ids:
        companions: dict[str, Any] = {}
        face_digests: dict[str, str | None] = {}
        trace_raw = raw_by_trace.get(tid) if raw_by_trace is not None else None
        for face, path_fn in _companion_faces():
            if trace_raw is not None:
                raw = trace_raw.get(face, b"")
            else:
                raw_path = path_fn(slug, tid)
                raw = raw_path.read_bytes() if raw_path.exists() else b""
            gz, redaction = redact_companions(raw, tools=tools)
            content = gzip.decompress(gz) if gz else b""
            if content:
                digest_hex = hashlib.sha256(content).hexdigest()
                digest = f"sha256:{digest_hex}"
                relpath = (
                    f"blobs/v1/{_path_part(slug)}/{digest_hex[:2]}/{digest_hex}.jsonl.gz"
                )
                files[relpath] = gz
                blobs[digest] = relpath
                companions[face] = {"blob": digest, "redaction": redaction}
                face_digests[face] = digest
            else:
                companions[face] = {"blob": None, "redaction": redaction}
                face_digests[face] = None

        spine = {
            "schema_version": MINI_BUCKET_TRACE_SCHEMA_VERSION,
            "trace_id": tid,
            "project_slug": slug,
            "companions": {
                face: {"blob": companions[face]["blob"]}
                for face in ("trail", "context", "sources")
            },
        }
        spine_rel = f"traces/v1/{_path_part(slug)}/{_path_part(tid)}/trace.json"
        files[spine_rel] = (json.dumps(spine, sort_keys=True, ensure_ascii=False) + "\n").encode(
            "utf-8"
        )
        traces[tid] = {"slug": slug, "trace_json": spine_rel, "companions": companions}
        trace_digests[tid] = face_digests

    digest = compute_mini_bucket_digest(trace_digests)
    manifest = {
        "schema_version": MINI_BUCKET_MANIFEST_SCHEMA_VERSION,
        "digest": digest,
        "traces": traces,
        "blobs": blobs,
    }
    files["mini_bucket_manifest.json"] = (
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    return {
        "schema_version": MINI_BUCKET_MANIFEST_SCHEMA_VERSION,
        "files": files,
        "manifest": manifest,
        "digest": digest,
    }


def verify_mini_bucket(mini: dict[str, Any]) -> list[str]:
    """Integrity check: content-addressed blobs, no dangling refs.

    Returns a list of human-readable issues ( empty == integral ). Checks that
    every companion blob a trace references resolves to a file whose content hash
    equals its content address, and that the blob index carries no orphan or
    missing-file entry.
    """

    files = mini.get("files") or {}
    manifest = mini.get("manifest") or {}
    blobs = manifest.get("blobs") or {}
    issues: list[str] = []
    referenced: set[str] = set()

    for tid, trec in (manifest.get("traces") or {}).items():
        spine_rel = trec.get("trace_json")
        if spine_rel not in files:
            issues.append(f"missing trace spine for {tid}: {spine_rel}")
        for face, meta in (trec.get("companions") or {}).items():
            blob = (meta or {}).get("blob")
            if blob is None:
                continue
            referenced.add(blob)
            relpath = blobs.get(blob)
            if relpath is None:
                issues.append(f"dangling companion ref {tid}/{face}: {blob} not in blob index")
                continue
            if relpath not in files:
                issues.append(f"dangling blob {tid}/{face}: {relpath} missing from files")
                continue
            try:
                content = gzip.decompress(files[relpath])
            except (OSError, ValueError):
                issues.append(f"corrupt blob {relpath}")
                continue
            digest_hex = blob.split(":", 1)[1] if ":" in blob else blob
            if hashlib.sha256(content).hexdigest() != digest_hex:
                issues.append(f"content-address mismatch for {relpath}")

    for blob, relpath in blobs.items():
        if blob not in referenced:
            issues.append(f"orphan blob {blob} ({relpath}) referenced by no trace")
        if relpath not in files:
            issues.append(f"blob index points at missing file {relpath}")
    return issues


def write_mini_bucket(mini: dict[str, Any], dest_dir: Path) -> list[Path]:
    """Write the materialized mini-bucket files under ``dest_dir``."""

    from .._bucket_io import _atomic_write_bytes

    dest = Path(dest_dir)
    written: list[Path] = []
    for rel, data in (mini.get("files") or {}).items():
        target = dest / rel
        _atomic_write_bytes(target, data)
        written.append(target)
    return written


def recompute_written_mini_bucket_digest(mini_dir: Path) -> str | None:
    """Recompute the mini-bucket digest from the files ON DISK under ``mini_dir``.

    Returns ``None`` when no ``mini_bucket_manifest.json`` is present. Used at
    publish time to prove the stamped ``mini_bucket_digest`` matches the bytes
    actually uploaded (#197 H4 — no hollow claim). The digest is a pure content
    hash, so it is recomputed from the uncompressed companion blobs, not the
    manifest's self-reported digest.
    """

    from .contract import compute_mini_bucket_digest

    mini_dir = Path(mini_dir)
    manifest_path = mini_dir / "mini_bucket_manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    blobs = manifest.get("blobs") or {}
    trace_digests: dict[str, dict[str, str | None]] = {}
    for tid, trec in (manifest.get("traces") or {}).items():
        face_digests: dict[str, str | None] = {}
        for face in ("trail", "context", "sources"):
            blob = ((trec.get("companions") or {}).get(face) or {}).get("blob")
            if blob is None:
                face_digests[face] = None
                continue
            rel = blobs.get(blob)
            content = gzip.decompress((mini_dir / rel).read_bytes())
            face_digests[face] = f"sha256:{hashlib.sha256(content).hexdigest()}"
        trace_digests[tid] = face_digests
    return compute_mini_bucket_digest(trace_digests)


def carried_section_inventory(capsule: dict[str, Any]) -> dict[str, Any]:
    """Counts + surfaces of what the capsule carries — NEVER a leaked byte (#198).

    The seal-time preview/approve inventory: a reviewer sees WHAT would ship
    (how many steps, which context layers, whether a test/bundle/mini-bucket is
    present) without seeing the content itself.
    """

    slice_ = capsule.get("slice") or {}
    steps = slice_.get("steps") or []
    anchors = capsule.get("trail_anchors") or []
    packet = capsule.get("context_resume_packet") or {}
    privacy_scope = capsule.get("privacy_scope") or {}
    layer_keys = ("system_layer", "messages_layer", "tool_registry_layer", "runtime_state_layer")
    context_layers = [
        k for k in layer_keys if isinstance(packet.get(k), dict) and packet.get(k)
    ]
    carried_sections = (
        "summary", "intent", "failing_step", "slice", "context_resume_packet",
        "trail_anchors", "repo_pin", "test", "environment", "bundle", "product",
    )
    surfaces = [name for name in carried_sections if capsule.get(name)]
    return {
        "schema_version": "opentraces.capsule.carried_inventory.v1",
        "steps": len(steps),
        "trail_anchors": len(anchors),
        "context_layers": context_layers,
        "has_context_resume_packet": bool(packet and not packet.get("error")),
        "has_test": bool(capsule.get("test")),
        "has_bundle": bool(capsule.get("bundle")),
        "mini_bucket_digest": capsule.get("mini_bucket_digest"),
        "messages_included": bool(privacy_scope.get("messages_included")),
        "system_prompt_included": bool(privacy_scope.get("system_prompt_included")),
        "messages_completeness": privacy_scope.get("messages_completeness"),
        "surfaces": surfaces,
    }


# --------------------------------------------------------------------------- #
# Egress clearance (#198): the ONE shared predicate on the capsule publish door.
# --------------------------------------------------------------------------- #


class CapsuleClearanceError(RuntimeError):
    """A source trace is not cleared for egress; publish refuses (zero bytes)."""

    def __init__(self, withheld: list[dict[str, Any]]):
        self.withheld = withheld
        ids = ", ".join(
            f"{w.get('trace_id')} ({w.get('clearance')})" for w in withheld
        )
        super().__init__(
            "capsule publish refused: source trace(s) not cleared for egress "
            f"({ids}). A capsule sourced from an unscanned/withheld trace cannot "
            "leave the machine. Scan/clear the trace(s) first."
        )


def _capsule_source_trace_ids(capsule: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    src = capsule.get("source") or {}
    tid = str(src.get("trace_id") or "").strip()
    if tid:
        ids.append(tid)
    for entry in capsule.get("sources") or []:
        t = str((entry or {}).get("trace_id") or "").strip()
        if t and t not in ids:
            ids.append(t)
    return ids


def enforce_capsule_clearance(
    capsule: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
    allow_sourceless_egress: bool = False,
) -> dict[str, Any]:
    """Refuse egress unless EVERY source trace is cleared (ADR-0008 §3).

    Uses the ONE shared predicate ``egress_clearance.clearance_for_trace``. A
    multi-trace capsule refuses if ANY source trace is not ``cleared``. Raises
    :class:`CapsuleClearanceError` carrying the withheld partition; on success
    returns ``{"cleared": [trace_id, ...]}``.

    #198 C1 — an EMPTY source-trace set is fail-closed: 'no clearable source' is
    treated as not-cleared and REFUSES, so the degenerate/abuse path cannot
    silently clear. A legitimate source-free capsule must opt in EXPLICITLY via
    ``allow_sourceless_egress=True`` (never the silent default).
    """

    from ..egress_clearance import CLEARED, clearance_for_trace

    tids = _capsule_source_trace_ids(capsule)
    if not tids:
        if allow_sourceless_egress:
            return {"cleared": []}
        raise CapsuleClearanceError(
            [{"trace_id": "(none)", "clearance": "no_clearable_source"}]
        )
    withheld: list[dict[str, Any]] = []
    for tid in tids:
        state = clearance_for_trace(tid, manifest=manifest)
        if state != CLEARED:
            withheld.append({"trace_id": tid, "clearance": state})
    if withheld:
        raise CapsuleClearanceError(withheld)
    return {"cleared": tids}


# --------------------------------------------------------------------------- #
# Capsule loading / resolving (the consume side)
# --------------------------------------------------------------------------- #


class CapsuleResolveError(RuntimeError):
    pass


def load_capsule_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CapsuleResolveError(f"capsule file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CapsuleResolveError(f"capsule file is not valid JSON: {path}") from exc
    return validate_capsule(data)


def resolve_capsule(ref: str) -> dict[str, Any]:
    """Resolve a capsule from a local path, an https URL, or an hf:// ref.

    Zero bespoke parsing for the consumer: one call returns the validated
    frozen envelope.
    """

    # Local file
    p = Path(ref)
    if p.exists():
        return load_capsule_file(p)

    if ref.startswith(("http://", "https://", "hf://")):
        return _resolve_remote_capsule(ref)

    raise CapsuleResolveError(
        f"could not resolve capsule ref {ref!r} (not a local file, http(s) URL, "
        "or hf:// ref)."
    )


def _resolve_remote_capsule(ref: str) -> dict[str, Any]:
    # hf://owner/repo/capsules/v1/<id>  OR  a full resolve/ URL
    url = ref
    if ref.startswith("hf://"):
        rid_and_path = ref[len("hf://") :]
        # owner/repo/capsules/v1/<id>[/capsule.json]
        parts = rid_and_path.split("/")
        if len(parts) < 2:
            raise CapsuleResolveError(f"malformed hf:// capsule ref: {ref!r}")
        repo_id = "/".join(parts[:2])
        rest = "/".join(parts[2:]) or ""
        if not rest.endswith("capsule.json"):
            rest = f"{rest.rstrip('/')}/capsule.json" if rest else ""
        url = f"{HF_HOST}/datasets/{repo_id}/resolve/main/{rest}"
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - trusted HF host
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        raise CapsuleResolveError(f"failed to fetch capsule from {url}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CapsuleResolveError(f"remote capsule is not valid JSON: {url}") from exc
    return validate_capsule(data)


# --------------------------------------------------------------------------- #
# HF publish (capsule-only, NOT bucket_remote)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Bundle secret-scan (#157 Part 1): a publish GATE, NEVER a trust factor. A
# secret-bearing bundle does not degrade to a lower tier — it does not leave.
# --------------------------------------------------------------------------- #


class BundleSecretFindingError(RuntimeError):
    """A capsule's source bundle carries a secret; publish REFUSES (zero bytes).

    The unrecoverable failure mode is a committed/hardcoded secret shipping to a
    PUBLIC HF dataset. This blocks by default; the operator may override after
    review with ``i_accept_bundle_findings=True`` (which records an explicit
    acknowledgement in ``bundle.secret_scan.acknowledged``). The message names
    the detectors but NEVER the secret bytes.
    """

    def __init__(self, secret_scan: dict[str, Any], findings: list[Any]):
        self.secret_scan = secret_scan
        self.findings = findings
        detectors = ", ".join(sorted({getattr(f, "detector_name", "?") for f in findings})) or "?"
        super().__init__(
            "capsule publish refused: the source bundle contains "
            f"{secret_scan.get('findings_count', len(findings))} secret finding(s) "
            f"[{detectors}]. A secret-bearing bundle is an unrecoverable public leak "
            "and does NOT leave the machine. Remove the secret (or exclude the file) "
            "and re-seal, or pass i_accept_bundle_findings=True to override after review."
        )


class BundleUnscannedError(RuntimeError):
    """A capsule's source bundle could NOT be secret-scanned (scanner absent) and is
    being PUBLISHED; publish fails CLOSED (zero bytes) (#157 H3).

    A raw ``git archive`` bundle uploaded to a PUBLIC HF dataset must be certified
    free of secrets before it leaves. When trufflehog is not installed the scan is
    an honest skip (``skipped_no_trufflehog``) — but an UNSCANNED bundle is NOT
    certified safe, so it does not leave. Install the scanner
    (``opentraces setup trufflehog``) and re-publish, or accept the risk after review
    with ``i_accept_bundle_findings=True``. Non-publish paths (export/local) never
    hit this gate; only the egress door fails closed.
    """

    def __init__(self, secret_scan: dict[str, Any]):
        self.secret_scan = secret_scan
        super().__init__(
            "capsule publish refused: the source bundle could not be secret-scanned "
            f"(status={secret_scan.get('status')!r}); a scanner (trufflehog) is not "
            "installed, so the bundle cannot be certified free of secrets. An UNSCANNED "
            "bundle does NOT leave to a PUBLIC dataset. Install trufflehog "
            "(`opentraces setup trufflehog`) and re-publish, or pass "
            "i_accept_bundle_findings=True to publish the unscanned bundle after review."
        )


def scan_bundle_bytes(bundle_bytes: bytes, *, verify: bool = False) -> tuple[dict[str, Any], list[Any]]:
    """Secret-scan the EXACT shipped bundle bytes; return ``(secret_scan, findings)``.

    Extracts the bundle to a throwaway temp dir (reusing the untrusted-archive
    path-traversal guard ``run.py::_safe_extractall``) and runs the native
    ``trufflehog filesystem`` scan (``security/trufflehog.py::scan_file``) — a
    bundle IS a directory after extraction, so ZERO adapter is needed. ``verify``
    stays ``False`` (never make outbound verification probes from a publish path).

    The returned ``secret_scan`` stamp is ALWAYS present (the load-bearing honesty
    artifact): ``{status, blocked, findings_count, scanned_at, trufflehog_version,
    acknowledged}``. When trufflehog is absent the scan is an HONEST skip
    (``status="skipped_no_trufflehog"``, ``blocked=False``) — never a silent clean
    claim. The stamp NEVER records a raw secret value.
    """

    import io
    import tarfile
    import tempfile
    from datetime import datetime, timezone

    from ...security.trufflehog import find_trufflehog, scan_file
    from .run import _safe_extractall

    scanned_at = datetime.now(timezone.utc).isoformat()
    version = find_trufflehog()
    if version is None:
        return (
            {
                "status": "skipped_no_trufflehog",
                "blocked": False,
                "findings_count": 0,
                "scanned_at": scanned_at,
                "trufflehog_version": None,
                "acknowledged": False,
            },
            [],
        )

    with tempfile.TemporaryDirectory(prefix="capsule-bundle-scan-") as tmp:
        dest = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:gz") as tf:
            _safe_extractall(tf, dest)
        findings = scan_file(dest, verify=verify)

    blocked = bool(findings)
    return (
        {
            "status": "blocked" if blocked else "clean",
            "blocked": blocked,
            "findings_count": len(findings),
            "scanned_at": scanned_at,
            "trufflehog_version": version,
            "acknowledged": False,
        },
        findings,
    )


def publish_capsule(
    capsule: dict[str, Any],
    *,
    repo_id: str,
    token: str | None,
    private: bool = False,
    bundle_bytes: bytes | None = None,
    require_clearance: bool = True,
    clearance_manifest: dict[str, Any] | None = None,
    allow_sourceless_egress: bool = False,
    mini_bucket: dict[str, Any] | None = None,
    i_accept_bundle_findings: bool = False,
) -> dict[str, Any]:
    """Upload capsule.json + capsule.md (+ the bundle + the mini-bucket when present) to ``capsules/v1/<id>/`` on HF.

    Returns ``{repo_id, revision, capsule_url, human_url, viewer_url, capsule_id}``
    with the URL pinned to the publish commit sha when the hub returns one.

    #198 — when ``require_clearance`` is set (the CLI egress door always sets it)
    the shared egress predicate is evaluated FIRST: a capsule sourced from an
    unscanned/withheld trace raises :class:`CapsuleClearanceError` before any HF
    call, so a refusal moves ZERO bytes. ``clearance_manifest`` supplies a
    push-time snapshot (no TOCTOU); ``None`` falls back to the live bucket lookup.
    The R7 ``ensure_redacted`` gate is preserved (wrapped, not replaced), as is
    the sha-pin double-commit.

    #197 H4 — ``mini_bucket`` (a :func:`build_mini_bucket` result) is written into
    the uploaded folder so the scoped, redacted mini-bucket the capsule's
    ``mini_bucket_digest`` claims ACTUALLY ships. Before upload the digest is
    recomputed over the written files and asserted equal to the stamp; a capsule
    that stamps a digest but supplies no mini-bucket refuses (no hollow claim).
    """

    # #198 clearance gate — evaluated BEFORE any HF import/call so a refusal is
    # provably zero-byte. Wraps (does not replace) the R7 redaction gate below.
    # Egress is off-by-default (require_clearance defaults True).
    if require_clearance:
        enforce_capsule_clearance(
            capsule,
            manifest=clearance_manifest,
            allow_sourceless_egress=allow_sourceless_egress,
        )

    # #157 bundle secret-scan GATE — a publish GATE, NEVER a trust factor. Scan the
    # EXACT shipped bytes; a finding BLOCKS (zero bytes out) unless the operator
    # explicitly accepts. Evaluated BEFORE any HF import/call so a refusal is
    # provably zero-byte. The stamp is ALWAYS recorded (clean OR blocked) on the
    # capsule's bundle block below, so every published bundle carries the artifact.
    pending_secret_scan: dict[str, Any] | None = None
    if bundle_bytes is not None:
        pending_secret_scan, _findings = scan_bundle_bytes(bundle_bytes)
        unscanned = pending_secret_scan.get("status") == "skipped_no_trufflehog"
        if pending_secret_scan["blocked"] and not i_accept_bundle_findings:
            raise BundleSecretFindingError(pending_secret_scan, _findings)
        # #157 H3 — scanner-absent PUBLISH fails CLOSED: an UNSCANNED bundle cannot
        # be certified free of secrets, so it does not leave to a PUBLIC dataset
        # without an explicit override. Evaluated BEFORE any HF call (zero bytes).
        if unscanned and not i_accept_bundle_findings:
            raise BundleUnscannedError(pending_secret_scan)
        pending_secret_scan["acknowledged"] = bool(
            (pending_secret_scan["blocked"] or unscanned) and i_accept_bundle_findings
        )

    try:
        from huggingface_hub import HfApi
    except Exception as exc:  # pragma: no cover - dep present in env
        raise RuntimeError("huggingface_hub is required to publish a capsule") from exc

    # R7: never emit an un-redacted envelope, regardless of how it was built
    # (export redacts; a hand-assembled freeze_capsule does not).
    from .redaction import ensure_redacted

    capsule = ensure_redacted(capsule)

    rid = _hf_repo_id(repo_id)
    cid = capsule["capsule_id"]
    api = HfApi(token=token)
    api.create_repo(repo_id=rid, repo_type="dataset", private=private, exist_ok=True)

    # Self-reference: stamp the capsule with its own (latest-revision) URLs before
    # upload so a capsule.json resolved standalone still knows where it lives. The
    # sha-pinned URL we RETURN below is the immutable handle for the issue body.
    capsule = dict(capsule)
    capsule["share"] = {
        "capsule_url": mint_capsule_url(rid, cid, revision="main"),
        "human_url": human_capsule_url(rid, cid, revision="main"),
        "viewer_url": mint_viewer_url(rid, cid, revision="main"),
        "published_revision": None,
    }
    # #157 — stamp the always-present bundle.secret_scan honesty artifact onto the
    # capsule so it ships in capsule.json (post-redaction; the stamp holds only
    # counts/status/version, never a secret byte, so ensure_redacted leaves it).
    if pending_secret_scan is not None:
        bundle_meta = dict(capsule.get("bundle") or {})
        bundle_meta["secret_scan"] = pending_secret_scan
        capsule["bundle"] = bundle_meta

    import tempfile

    base = f"{CAPSULE_PREFIX}/{cid}"
    stamped_digest = capsule.get("mini_bucket_digest")
    with tempfile.TemporaryDirectory() as tmp:
        artifacts = write_capsule_dir(
            capsule, Path(tmp), bundle_bytes=bundle_bytes, mini_bucket=mini_bucket
        )
        # #197 H4 — the capsule may only claim a mini_bucket_digest over content
        # it actually ships. Recompute the digest over the written mini-bucket and
        # refuse a hollow claim (stamped-but-absent) or a mismatch.
        if stamped_digest:
            written_digest = recompute_written_mini_bucket_digest(
                artifacts["dir"] / "mini_bucket"
            )
            if written_digest is None:
                raise RuntimeError(
                    "capsule stamps a mini_bucket_digest but no mini-bucket was "
                    "supplied to publish; pass mini_bucket= so the claimed "
                    "content ships (no hollow digest)."
                )
            if written_digest != stamped_digest:
                raise RuntimeError(
                    f"mini-bucket digest mismatch: capsule stamps "
                    f"{stamped_digest!r} but the written mini-bucket hashes to "
                    f"{written_digest!r}."
                )
        commit = api.upload_folder(
            repo_id=rid,
            repo_type="dataset",
            folder_path=str(artifacts["dir"]),
            path_in_repo=base,
            commit_message=f"capsule {cid}",
        )

    revision = getattr(commit, "oid", None) or "main"

    # Pin the PUBLISHED capsule.json to an immutable commit sha (plan 090 U0).
    # The folder upload wrote share.revision="main"/published_revision=None because
    # the oid is only known after the commit returns. Re-upload just capsule.json,
    # byte-stamped with the sha, in a SECOND commit, and hand out the SECOND
    # commit's revision: a HF `/resolve/<oid>/` URL serves the blob AS IT EXISTED AT
    # THAT COMMIT, so the pinned capsule.json is only served at the re-upload commit
    # — resolving at the first commit would still return the stale main/None folder
    # version. The embedded share self-references the first (data) commit, whose
    # bundle is byte-identical; a blob cannot contain its own commit's oid, so
    # ``published_revision`` (non-null, immutable) is the load-bearing marker rather
    # than a perfectly-circular self-URL.
    pin_revision = revision
    if revision != "main":
        pinned = dict(capsule)
        pinned["share"] = {
            "capsule_url": mint_capsule_url(rid, cid, revision=revision),
            "human_url": human_capsule_url(rid, cid, revision=revision),
            "viewer_url": mint_viewer_url(rid, cid, revision=revision),
            "revision": revision,
            "published_revision": revision,
        }
        payload = (
            json.dumps(pinned, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        pin_commit = api.upload_file(
            path_or_fileobj=payload,
            path_in_repo=f"{base}/capsule.json",
            repo_id=rid,
            repo_type="dataset",
            commit_message=f"capsule {cid} (pin share to {revision[:12]})",
        )
        pin_revision = getattr(pin_commit, "oid", None) or revision

    return {
        "repo_id": rid,
        "capsule_id": cid,
        # The commit the handed URLs resolve at — serves the SHA-pinned capsule.json.
        "revision": pin_revision,
        # The immutable data/content commit embedded in the capsule (non-null marker).
        "published_revision": revision if revision != "main" else None,
        "capsule_url": mint_capsule_url(rid, cid, revision=pin_revision),
        "human_url": human_capsule_url(rid, cid, revision=pin_revision),
        "viewer_url": mint_viewer_url(rid, cid, revision=pin_revision),
    }


# --------------------------------------------------------------------------- #
# Clipboard
# --------------------------------------------------------------------------- #


def copy_to_clipboard(text: str) -> tuple[bool, str]:
    """Best-effort clipboard copy. Returns (ok, tool_or_reason). Never raises."""

    candidates = (
        ["pbcopy"],            # macOS
        ["wl-copy"],           # Wayland
        ["xclip", "-selection", "clipboard"],  # X11
        ["xsel", "--clipboard", "--input"],
        ["clip"],              # Windows
    )
    for cmd in candidates:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, check=True)
                return True, cmd[0]
            except Exception as exc:  # pragma: no cover - platform dependent
                return False, f"{cmd[0]} failed: {exc}"
    return False, "no clipboard tool found (pbcopy/wl-copy/xclip/xsel/clip)"


# --------------------------------------------------------------------------- #
# GitHub issue (idempotent on the capsule marker, NOT a branch)
# --------------------------------------------------------------------------- #


class GhError(RuntimeError):
    pass


class GhUnavailableError(RuntimeError):
    pass


def gh_available() -> bool:
    return shutil.which("gh") is not None


def _require_gh() -> str:
    gh = shutil.which("gh")
    if not gh:
        raise GhUnavailableError(
            "GitHub CLI (`gh`) not found. Install via `brew install gh` or see "
            "https://cli.github.com/."
        )
    return gh


def _issue_marker(capsule_id: str) -> str:
    return f"<!-- opentraces-capsule: {capsule_id} -->"


def find_capsule_issue(repo: str, capsule_id: str) -> dict[str, Any] | None:
    """Find an existing issue carrying this capsule's marker (idempotency key)."""

    gh = _require_gh()
    out = subprocess.run(
        [
            gh, "issue", "list", "--repo", repo, "--state", "all",
            "--search", f"opentraces-capsule: {capsule_id}",
            "--json", "number,url,body", "--limit", "20",
        ],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise GhError(out.stderr or out.stdout or "gh issue list failed")
    try:
        rows = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        rows = []
    marker = _issue_marker(capsule_id)
    for row in rows:
        if marker in (row.get("body") or ""):
            return {"number": row.get("number"), "url": row.get("url")}
    return None


def create_or_update_issue(
    *, repo: str, capsule_id: str, title: str, body: str,
) -> dict[str, Any]:
    """Idempotent: update the existing capsule issue if present, else create."""

    gh = _require_gh()
    existing = find_capsule_issue(repo, capsule_id)
    if existing is not None:
        out = subprocess.run(
            [gh, "issue", "edit", str(existing["number"]), "--repo", repo, "--body-file", "-"],
            input=body, capture_output=True, text=True, check=False,
        )
        if out.returncode != 0:
            raise GhError(out.stderr or out.stdout or "gh issue edit failed")
        return {"number": existing["number"], "url": existing["url"], "action": "updated"}

    out = subprocess.run(
        [gh, "issue", "create", "--repo", repo, "--title", title, "--body-file", "-"],
        input=body, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise GhError(out.stderr or out.stdout or "gh issue create failed")
    url = (out.stdout or "").strip().splitlines()[-1] if out.stdout else ""
    return {"number": None, "url": url, "action": "created"}


def parse_issue_ref(ref: str) -> tuple[str | None, int | None]:
    """Parse an issue reference into ``(repo, number)``.

    Accepts: ``https://github.com/owner/repo/issues/8``, ``owner/repo#8``, or a
    bare ``8`` (repo must be supplied separately).
    """

    s = ref.strip()
    m = re.search(r"github\.com/([^/]+/[^/]+)/issues/(\d+)", s)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"([^/\s]+/[^/\s#]+)#(\d+)$", s)
    if m:
        return m.group(1), int(m.group(2))
    if s.isdigit():
        return None, int(s)
    return None, None


def issue_state(repo: str, number: int) -> dict[str, Any]:
    """Return ``{state, title, url, verdict}`` for an issue (verdict from comments)."""

    gh = _require_gh()
    out = subprocess.run(
        [gh, "issue", "view", str(number), "--repo", repo,
         "--json", "state,title,url,body,comments"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise GhError(out.stderr or out.stdout or "gh issue view failed")
    data = json.loads(out.stdout or "{}")
    verdict = None
    for comment in data.get("comments") or []:
        m = re.search(r"opentraces-capsule-verdict:\s*\S+\s+state=(\w+)", comment.get("body") or "")
        if m:
            verdict = m.group(1)  # last verdict wins
    cid = None
    mid = re.search(r"opentraces-capsule:\s*(\S+)\s*-->", data.get("body") or "")
    if mid:
        cid = mid.group(1)
    return {
        "state": data.get("state"),
        "title": data.get("title"),
        "url": data.get("url"),
        "verdict": verdict,
        "capsule_id": cid,
    }


def comment_issue(repo: str, number: int, body: str) -> None:
    gh = _require_gh()
    out = subprocess.run(
        [gh, "issue", "comment", str(number), "--repo", repo, "--body-file", "-"],
        input=body, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise GhError(out.stderr or out.stdout or "gh issue comment failed")


def close_issue(repo: str, number: int, *, reason: str = "completed") -> None:
    gh = _require_gh()
    out = subprocess.run(
        [gh, "issue", "close", str(number), "--repo", repo, "--reason", reason],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise GhError(out.stderr or out.stdout or "gh issue close failed")


__all__ = [
    "BUNDLE_FILENAME",
    "BundleSecretFindingError",
    "BundleUnscannedError",
    "CapsuleClearanceError",
    "CapsuleResolveError",
    "GhError",
    "GhUnavailableError",
    "build_capsule_bundle",
    "build_mini_bucket",
    "scan_bundle_bytes",
    "carried_section_inventory",
    "close_issue",
    "comment_issue",
    "copy_to_clipboard",
    "enforce_capsule_clearance",
    "sibling_bundle_path",
    "verify_bundle",
    "verify_mini_bucket",
    "create_or_update_issue",
    "find_capsule_issue",
    "gh_available",
    "issue_state",
    "parse_issue_ref",
    "human_capsule_url",
    "load_capsule_file",
    "mint_capsule_url",
    "mint_viewer_url",
    "publish_capsule",
    "recompute_written_mini_bucket_digest",
    "render_capsule_markdown",
    "render_issue_body",
    "resolve_capsule",
    "write_capsule_dir",
    "write_mini_bucket",
]
