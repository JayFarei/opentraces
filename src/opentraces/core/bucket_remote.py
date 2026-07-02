"""Private bucket remote adapters.

The bucket remote is private workspace infrastructure. It is intentionally
separate from dataset remotes, which are publication destinations.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:  # pragma: no cover - exercised by tests through monkeypatching.
    from huggingface_hub import HfApi
except Exception:  # pragma: no cover
    HfApi = None  # type: ignore[assignment]

from . import paths
from .bucket_fake_remote import (
    fake_remote_diff,
    fake_remote_pull,
    fake_remote_push,
    fake_remote_root,
    fake_remote_status,
)
from .bucket_store import (
    BUCKET_MANIFEST_SCHEMA,
    bucket_manifest,
    classify_bucket_remote_state,
    write_bucket_sync_state,
)
from .config import load_config


REMOTE_SCHEMA = "opentraces.bucket.remote.v1"


logger = logging.getLogger(__name__)


class BucketRemoteError(RuntimeError):
    """Raised when a configured bucket remote cannot be used."""


def remote_status(*, fake_root: Path | None = None) -> dict[str, Any]:
    """Return provider-aware private bucket remote status.

    Every branch carries an additive ``security_gate`` block (issue #94) so the
    sync-diagnostic surface explains the security posture that actually gates a
    push — not only the digest relation.

    The gate is computed FIRST, from a read-only, byte-capped read of the
    PERSISTED ``bucket/manifest.json`` (codex finding #1/#2): it never calls
    ``bucket_manifest`` / ``bucket_security_overview`` (both SCAN) and never
    writes ``manifest.json``. Computing it before any status helper runs means
    the gate reflects the genuinely-persisted state, NOT a manifest the digest
    helper builds — a missing/oversized persisted manifest yields
    ``security_gate.state == "unknown"``. (The configured-remote digest helpers
    DO still scan to compute the local digest; that pre-existing scan is #97,
    out of #94's scope — only the GATE is guaranteed scan-free here.)
    """

    if fake_root is not None:
        gate = _security_gate(remote_configured=True)
        payload = _fake_payload(fake_remote_status(fake_root))
        payload["security_gate"] = gate
        return payload
    cfg = load_config()
    remote = cfg.bucket.remote
    if cfg.bucket.storage != "remote" or not remote.enabled:
        # Ambient fake remote = a fake-remote root wired via env/config. Detect
        # it cheaply (no scan) so the gate's remote-configured axis is correct.
        remote_configured = fake_remote_root() is not None
        gate = _security_gate(remote_configured=remote_configured)
        fake = _ambient_fake_status()
        if fake is not None:
            fake["security_gate"] = gate
            return fake
        payload = {
            "schema_version": REMOTE_SCHEMA,
            "state": "unconfigured",
            "provider": remote.provider,
            "advice": "run opentraces setup bucket",
            "security_gate": gate,
        }
        return payload
    gate = _security_gate(remote_configured=True)
    if remote.provider == "fake":
        payload = _fake_payload(fake_remote_status())
    else:
        payload = _hf_status(remote.url, cfg.hf_token)
    payload["security_gate"] = gate
    return payload


def _security_gate(*, remote_configured: bool) -> dict[str, Any]:
    """Build the bucket-sync security gate for ``bucket remote status``.

    Reads the PERSISTED ``bucket/manifest.json`` via the shared read-only,
    byte-capped reader (``read_persisted_manifest_capped`` — NEVER
    ``bucket_manifest`` / ``bucket_security_overview``, both of which SCAN, and
    never writing the file). A missing / oversized / unreadable persisted
    manifest yields ``state == "unknown"`` rather than triggering a scan or a
    write (codex finding #1/#2; the byte cap matches doctor's ``too-large``
    threshold). ``eligible`` composes the security axis (remediation is None)
    with the remote-configured axis; ``blocking_reasons`` and ``advice`` list
    the remote-unconfigured step first, then the security remediation (codex
    finding #3 / issue contract).
    """

    from .bucket_security import bucket_sync_security_gate
    from .bucket_store import read_persisted_manifest_capped

    state, manifest = read_persisted_manifest_capped()
    if state != "ok" or manifest is None:
        # absent / too_large / error -> unknown, WITHOUT scanning or writing.
        return {
            "state": "unknown",
            "reason": f"manifest_{state}",
            "configured": None,
            "unfiltered_count": None,
            "security_stale_count": None,
            "eligible": False,
            "blocking_reasons": (
                ["remote_unconfigured"] if not remote_configured else []
            ),
            "remediation": None,
            "advice": (
                ["opentraces setup bucket"] if not remote_configured else []
            ),
        }

    trace_records = manifest.get("trace_records") or {}
    unfiltered = int(trace_records.get("unfiltered_count") or 0)
    stale = int(trace_records.get("security_stale_count") or 0)
    gate = bucket_sync_security_gate(unfiltered=unfiltered, stale=stale)

    blocking_reasons: list[str] = []
    if not remote_configured:
        blocking_reasons.append("remote_unconfigured")
    blocking_reasons.extend(gate["blocking_reasons"])

    return {
        "state": "ok",
        "configured": gate["configured"],
        "unfiltered_count": gate["unfiltered_count"],
        "security_stale_count": gate["security_stale_count"],
        "eligible": remote_configured and gate["remediation"] is None,
        "blocking_reasons": blocking_reasons,
        "remediation": gate["remediation"],
        "advice": _security_advice_chain(
            remote_configured=remote_configured, remediation=gate["remediation"]
        ),
    }


def _security_advice_chain(
    *, remote_configured: bool, remediation: dict[str, Any] | None
) -> list[str]:
    """Ordered, copy-pasteable remediation commands for the security gate.

    ``setup bucket`` first when the remote is unconfigured, then the security
    chain: ``policy --policy basic`` -> ``run --all`` when the filter is not
    configured, else just ``run --all``.
    """

    chain: list[str] = []
    if not remote_configured:
        chain.append("opentraces setup bucket")
    if remediation:
        if remediation.get("state") == "not_configured":
            chain.append("opentraces bucket security policy --policy basic")
        chain.append("opentraces bucket security run --all")
    return chain


def remote_diff(*, fake_root: Path | None = None) -> dict[str, Any]:
    if fake_root is not None:
        return _fake_payload(fake_remote_diff(fake_root))
    status = remote_status()
    return {
        **status,
        "different": status.get("state")
        in {"missing", "different", "local_ahead", "remote_ahead", "diverged", "error"},
    }


def remote_push(
    *,
    fake_root: Path | None = None,
    force: bool = False,
    unsafe_push: bool = False,
) -> dict[str, Any]:
    if fake_root is not None:
        return _fake_payload(
            _substrate_aware_fake_push(
                fake_root=fake_root, force=force, unsafe_push=unsafe_push
            )
        )
    cfg = load_config()
    remote = cfg.bucket.remote
    if cfg.bucket.storage != "remote" or not remote.enabled:
        if fake_remote_root() is not None:
            return _fake_payload(
                _substrate_aware_fake_push(force=force, unsafe_push=unsafe_push)
            )
        raise BucketRemoteError("private bucket remote is not configured")
    if remote.provider == "fake":
        return _fake_payload(
            _substrate_aware_fake_push(force=force, unsafe_push=unsafe_push)
        )
    return _hf_push(remote.url, cfg.hf_token, force=force, unsafe_push=unsafe_push)


def remote_pull(
    *,
    fake_root: Path | None = None,
    force: bool = False,
    eager: bool = False,
) -> dict[str, Any]:
    """Pull the configured private bucket remote.

    Plan 080 §6 pull patterns:

    - Selective (default, ``eager=False``): fetch ``manifest.json`` plus
      per-trace envelope files (``trace.json``, ``trail.jsonl.gz``,
      ``context.jsonl.gz``, ``sources.jsonl.gz``) and the event mirror
      index. Layer / raw blobs stay LAZY and are fetched on demand by
      ``ctx show`` / ``ctx diff``.
    - Eager (``eager=True``): fetch every referenced blob upfront. Use
      for offline analysis or full mirror snapshots.
    """

    if fake_root is not None:
        return _mark_pull_dirty(
            _fake_payload(_substrate_aware_fake_pull(fake_root=fake_root, force=force))
        )
    cfg = load_config()
    remote = cfg.bucket.remote
    if cfg.bucket.storage != "remote" or not remote.enabled:
        if fake_remote_root() is not None:
            return _mark_pull_dirty(
                _fake_payload(_substrate_aware_fake_pull(force=force))
            )
        raise BucketRemoteError("private bucket remote is not configured")
    if remote.provider == "fake":
        return _mark_pull_dirty(_fake_payload(_substrate_aware_fake_pull(force=force)))
    return _mark_pull_dirty(_hf_pull(remote.url, cfg.hf_token, force=force, eager=eager))


def _mark_pull_dirty(result: dict[str, Any]) -> dict[str, Any]:
    """Best-effort search-snapshot dirty mark after a pull changed the bucket.

    A completed pull rewrites/deletes trace envelopes under the bucket traces
    tree, so the read-only trace search snapshot must be rebuilt. Only marks
    when the pull actually transferred files (``files_downloaded`` for the HF
    adapter, ``files_copied`` for the fake adapter); a selective pull that
    copied nothing left the local bucket untouched.
    """

    changed = bool(result.get("files_downloaded") or result.get("files_copied"))
    if result.get("state") == "pulled" and changed:
        try:
            from .trace_search_state import mark_search_snapshot_dirty

            mark_search_snapshot_dirty("bucket_remote_pull")
        except Exception:
            pass
    return result


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
    if status.get("state") not in {"missing", "local_ahead"}:
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
    # Plan 080 §20 Resolution E: hard schema_version check on every manifest
    # read path. No v1-reader path (dev branch, no shims).
    remote_schema = remote_manifest.get("schema_version")
    if remote_schema and remote_schema != BUCKET_MANIFEST_SCHEMA:
        raise BucketRemoteError(
            f"Remote bucket manifest schema {remote_schema} incompatible with "
            f"local {BUCKET_MANIFEST_SCHEMA}; run 'opentraces setup bucket "
            "--migrate' on the writer machine"
        )
    remote_digest = remote_manifest.get("digest")
    relation = classify_bucket_remote_state(
        provider="huggingface",
        target=repo_id,
        local_digest=local.get("digest"),
        remote_digest=remote_digest,
    )
    return _hf_status_payload(
        repo_id,
        state=relation["state"],
        local_digest=local.get("digest"),
        remote_digest=remote_digest,
        last_sync_digest=relation.get("last_sync_digest"),
        remote_updated_at=remote_manifest.get("updated_at"),
    )


def _hf_push(
    url: str | None,
    token: str | None,
    *,
    force: bool = False,
    unsafe_push: bool = False,
) -> dict[str, Any]:
    repo_id = _hf_repo_id(url)
    api = _hf_api(token)
    # Records captured before security tools were enabled carry a stale
    # security envelope. The daemon path refreshes them implicitly via the
    # index-sync bridge; the discrete push verb must do the same so both
    # paths judge eligibility against current-config security state.
    try:
        from .bucket_store import sync_trace_records_from_local_stores

        sync_trace_records_from_local_stores(prune=False)
    except Exception:  # noqa: BLE001 - refresh is best-effort; the gate below stays authoritative
        pass
    manifest = bucket_manifest(write=True, include_objects=False)
    sync = manifest.get("sync") or {}
    if sync.get("eligible") is not True:
        reasons = ", ".join(str(reason) for reason in sync.get("blocked_reasons") or [])
        raise BucketRemoteError(
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
    # Egress gate (#162): refuse before ANY network call if the FRESH manifest
    # still withholds any trace. The bucket-level sync.eligible flag above is
    # all-or-nothing and misses status_unknown rows (they do not increment
    # unfiltered_count); this per-trace gate closes that hole so a not-cleared
    # trace can never physically egress while being reported "withheld".
    # Placed before _hf_status so a refusal touches zero bytes on the wire.
    from .bucket_sync import enforce_push_clearance

    partition = enforce_push_clearance(manifest)
    status = _hf_status(url, token)
    if status.get("state") in {"remote_ahead", "diverged"} and not force:
        raise BucketRemoteError(
            "remote bucket has changes that are not in the local bucket; "
            "pull first or pass --force to overwrite"
        )
    api.create_repo(
        repo_id=repo_id,
        repo_type="dataset",
        exist_ok=True,
        private=True,
    )
    files_uploaded = 0
    context_tree_files_uploaded = 0
    blocked_projects, skipped_substrates = _context_tree_skip_set(
        unsafe_push=unsafe_push
    )
    # Plan 080 §6 push order: (a) blobs, (b) event batches + index,
    # (c) per-trace envelopes (trail/context/sources), (d) trace.json
    # spine LAST per trace, (e) manifest.json absolute last. A partial
    # push leaves the remote consistent: manifest only enumerates
    # traces whose envelopes have landed; envelopes only reference
    # blobs that have landed.
    bucket_root = paths.bucket_dir()
    passes = _ordered_bucket_upload_passes(
        skip_blocked_project_slugs=blocked_projects
    )
    pass_counts: dict[str, int] = {}
    for pass_name, pass_paths in passes:
        pass_counts[pass_name] = len(pass_paths)
        logger.info(
            "bucket push pass %s: %d files", pass_name, len(pass_paths)
        )
        # Stderr emission so otbox journeys can observe pass order
        # without enabling logging. Format is stable contract.
        print(
            f"push pass {pass_name}: {len(pass_paths)} files",
            file=sys.stderr,
            flush=True,
        )
        for path in pass_paths:
            rel = path.relative_to(bucket_root).as_posix()
            api.upload_file(
                path_or_fileobj=str(path),
                path_in_repo=rel,
                repo_id=repo_id,
                repo_type="dataset",
            )
            files_uploaded += 1
            if rel.startswith("contexts/v1/") or rel.startswith(
                "blobs/v1/"
            ) and "/context/" in rel:
                context_tree_files_uploaded += 1
    write_bucket_sync_state(
        provider="huggingface",
        target=repo_id,
        digest=manifest.get("digest"),
        remote_digest=manifest.get("digest"),
        direction="push",
    )
    verification = _safe_verify_context_tree()
    return {
        "schema_version": REMOTE_SCHEMA,
        "provider": "huggingface",
        "state": "pushed",
        "repo_id": repo_id,
        "digest": manifest.get("digest"),
        "files_uploaded": files_uploaded,
        "skipped_substrates": skipped_substrates,
        "context_tree_files_uploaded": context_tree_files_uploaded,
        "pass_counts": pass_counts,
        "verification": verification,
        "push_partition": partition,
    }


def _hf_pull(
    url: str | None,
    token: str | None,
    *,
    force: bool = False,
    eager: bool = False,
) -> dict[str, Any]:
    repo_id = _hf_repo_id(url)
    api = _hf_api(token)
    try:
        files = sorted(api.list_repo_files(repo_id=repo_id, repo_type="dataset"))
    except Exception as exc:  # noqa: BLE001
        raise BucketRemoteError(f"remote bucket is not readable: {repo_id}: {exc}") from exc
    if "manifest.json" not in files:
        raise BucketRemoteError(f"remote bucket is missing manifest.json: {repo_id}")
    status = _hf_status(url, token)
    if status.get("state") in {"local_ahead", "diverged"} and not force:
        raise BucketRemoteError(
            "local bucket has changes that are not in the remote bucket; "
            "push first or pass --force to overwrite"
        )
    # Plan 080 §6 pull patterns. Selective default: fetch the manifest,
    # per-trace envelopes (trace.json + trail/context/sources jsonl.gz),
    # and the event log mirror; leave blobs lazy for ``ctx show`` to
    # fetch on demand. Eager OR force: fetch everything upfront (force
    # preserves the existing wipe-and-replace mirror contract).
    pull_targets: list[str]
    files_skipped_lazy = 0
    full_fetch = eager or force
    if full_fetch:
        pull_targets = [
            name
            for name in files
            if name and name != ".gitattributes" and not name.endswith("/")
        ]
    else:
        pull_targets = []
        for name in files:
            if not name or name == ".gitattributes" or name.endswith("/"):
                continue
            if _is_lazy_blob_path(name):
                files_skipped_lazy += 1
                continue
            pull_targets.append(name)
    with tempfile.TemporaryDirectory(prefix="opentraces-bucket-pull-") as tmp_name:
        tmp_root = Path(tmp_name) / "bucket"
        tmp_root.mkdir(parents=True)
        copied = 0
        for name in pull_targets:
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
        # ``--force`` and ``--eager`` both mean "treat the remote as the
        # source of truth" — wipe-and-replace mirror semantics. Without
        # either flag, selective pull merges pulled envelopes over the
        # local cache, preserving cached blobs and any local-only files.
        if full_fetch:
            if local.exists():
                shutil.rmtree(local)
            local.mkdir(parents=True, exist_ok=True)
            _copy_tree(tmp_root, local)
        else:
            local.mkdir(parents=True, exist_ok=True)
            _copy_tree(tmp_root, local)
    manifest = bucket_manifest(write=True, include_objects=False)
    write_bucket_sync_state(
        provider="huggingface",
        target=repo_id,
        digest=manifest.get("digest"),
        remote_digest=manifest.get("digest"),
        direction="pull",
    )
    verification = _safe_verify_context_tree()
    return {
        "schema_version": REMOTE_SCHEMA,
        "provider": "huggingface",
        "state": "pulled",
        "repo_id": repo_id,
        "digest": manifest.get("digest"),
        "files_downloaded": copied,
        "files_skipped_lazy": files_skipped_lazy,
        "eager": eager,
        "verification": verification,
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
    last_sync_digest: str | None = None,
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
        "last_sync_digest": last_sync_digest,
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


# ---------------------------------------------------------------------------
# Plan 079 — substrate-aware push policy + post-transfer verify
# ---------------------------------------------------------------------------


def _context_tree_skip_set(*, unsafe_push: bool) -> tuple[set[str], list[str]]:
    """Return ``(blocked_project_slugs, skipped_substrates)`` for one push.

    A project's Context Tree paths are blocked when any per-trace head
    for that project reports ``remote_sync_eligible is False``. The
    ``--unsafe-push`` flag clears the block (plan 079 R8).
    """

    if unsafe_push:
        return set(), []
    try:
        from .bucket_store import iter_context_tree_traces
    except ImportError:
        return set(), []
    try:
        rows = iter_context_tree_traces()
    except Exception:
        return set(), []
    blocked: set[str] = set()
    for row in rows:
        if row.get("remote_sync_eligible") is False:
            slug = row.get("project_slug")
            if slug:
                blocked.add(slug)
    skipped = ["context_tree"] if blocked else []
    return blocked, skipped


def _ordered_bucket_upload_paths(
    *, skip_blocked_project_slugs: set[str]
) -> list[Path]:
    """Walk the bucket and order paths for upload.

    Ordering rule (plan 079 R9): for every project, all
    ``contexts/v1/<project>/layers/blobs/**`` paths must precede the
    ``contexts/v1/<project>/<trace>/head.json`` paths for the same
    project. Non-context-tree paths and shared-blob paths follow last.
    """

    bucket_root = paths.bucket_dir()
    if not bucket_root.exists():
        return []
    project_blobs: dict[str, list[Path]] = {}
    project_heads: dict[str, list[Path]] = {}
    other: list[Path] = []
    for path in sorted(bucket_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(bucket_root).as_posix()
        if rel.startswith("contexts/v1/"):
            parts = rel.split("/")
            if len(parts) >= 3:
                project_slug = parts[2]
                if project_slug in skip_blocked_project_slugs:
                    continue
                if project_slug == "_shared":
                    other.append(path)
                    continue
                if "/layers/blobs/" in rel:
                    project_blobs.setdefault(project_slug, []).append(path)
                else:
                    project_heads.setdefault(project_slug, []).append(path)
                continue
        other.append(path)
    ordered: list[Path] = []
    for project_slug in sorted(set(project_blobs) | set(project_heads)):
        ordered.extend(project_blobs.get(project_slug, []))
        ordered.extend(project_heads.get(project_slug, []))
    ordered.extend(other)
    return ordered


# Pass names used by ``_ordered_bucket_upload_passes``. Stable contract
# for otbox journey assertions ("push pass <name>: <n> files").
_PUSH_PASS_BLOBS = "blobs"
_PUSH_PASS_EVENTS = "events"
_PUSH_PASS_ENVELOPES = "envelopes"
_PUSH_PASS_SPINE = "spine"
_PUSH_PASS_MANIFEST = "manifest"


def _ordered_bucket_upload_passes(
    *, skip_blocked_project_slugs: set[str]
) -> list[tuple[str, list[Path]]]:
    """Group bucket files into the 5 ordered push passes (plan 080 §6).

    Order (load-bearing for partial-failure recovery):

    a. ``blobs``     — ``bucket/blobs/v1/<project>/context/**`` and
                       ``bucket/blobs/v1/<project>/raw/**`` (the
                       content-addressed layer + raw bodies).
    b. ``events``    — ``bucket/events/v1/batches/**`` plus
                       ``bucket/events/v1/index.json``.
    c. ``envelopes`` — ``bucket/traces/v1/<project>/<trace>/{trail,
                       context,sources}.jsonl.gz`` (per-trace projections
                       that reference blobs).
    d. ``spine``     — ``bucket/traces/v1/<project>/<trace>/trace.json``
                       per trace, written LAST per trace (the spine; its
                       presence is the consumer's per-trace ready signal).
    e. ``manifest``  — ``bucket/manifest.json`` (ABSOLUTE LAST; whole-bucket
                       ready-to-read signal).

    Legacy plan-079 paths (``contexts/v1/...``) and any other unrecognised
    paths land in the appropriate pass on a best-effort basis: layer blob
    paths go in ``blobs``, head/projection files go in ``envelopes``, and
    truly unclassified files go in ``envelopes`` (before manifest) so they
    can never out-order the ready signal.
    """

    bucket_root = paths.bucket_dir()
    if not bucket_root.exists():
        return [
            (_PUSH_PASS_BLOBS, []),
            (_PUSH_PASS_EVENTS, []),
            (_PUSH_PASS_ENVELOPES, []),
            (_PUSH_PASS_SPINE, []),
            (_PUSH_PASS_MANIFEST, []),
        ]
    blobs: list[Path] = []
    events: list[Path] = []
    envelopes: list[Path] = []
    spine: list[Path] = []
    manifest: list[Path] = []
    for path in sorted(bucket_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(bucket_root).as_posix()
        pass_name = _classify_bucket_path_pass(
            rel, skip_blocked_project_slugs=skip_blocked_project_slugs
        )
        if pass_name is None:
            continue
        if pass_name == _PUSH_PASS_BLOBS:
            blobs.append(path)
        elif pass_name == _PUSH_PASS_EVENTS:
            events.append(path)
        elif pass_name == _PUSH_PASS_ENVELOPES:
            envelopes.append(path)
        elif pass_name == _PUSH_PASS_SPINE:
            spine.append(path)
        elif pass_name == _PUSH_PASS_MANIFEST:
            manifest.append(path)
    return [
        (_PUSH_PASS_BLOBS, blobs),
        (_PUSH_PASS_EVENTS, events),
        (_PUSH_PASS_ENVELOPES, envelopes),
        (_PUSH_PASS_SPINE, spine),
        (_PUSH_PASS_MANIFEST, manifest),
    ]


def _classify_bucket_path_pass(
    rel: str, *, skip_blocked_project_slugs: set[str]
) -> str | None:
    """Classify a relative bucket path into a push pass (plan 080).

    Returns ``None`` to skip the path entirely (e.g. blocked project
    context blobs under the legacy ``contexts/v1/<project>/`` layout).
    """

    # Plan 080 layout — primary classification.
    if rel == "manifest.json":
        return _PUSH_PASS_MANIFEST
    if rel.startswith("blobs/v1/"):
        # ``blobs/v1/<project>/(context|raw)/...``. Blocked projects get
        # their context blobs dropped, raw blobs are not gated.
        parts = rel.split("/")
        if len(parts) >= 4:
            project_slug = parts[2]
            kind = parts[3]
            if (
                kind == "context"
                and project_slug in skip_blocked_project_slugs
            ):
                return None
        return _PUSH_PASS_BLOBS
    if rel.startswith("events/v1/"):
        return _PUSH_PASS_EVENTS
    if rel.startswith("traces/v1/"):
        # ``traces/v1/<project>/<trace>/<file>``. trace.json is the spine;
        # all other per-trace files are envelopes.
        if rel.endswith("/trace.json"):
            return _PUSH_PASS_SPINE
        return _PUSH_PASS_ENVELOPES

    # Legacy / plan-079 layout — keep pushable so we never strand files.
    if rel.startswith("contexts/v1/"):
        parts = rel.split("/")
        if len(parts) >= 3:
            project_slug = parts[2]
            if (
                project_slug != "_shared"
                and project_slug in skip_blocked_project_slugs
            ):
                return None
        if "/layers/blobs/" in rel:
            return _PUSH_PASS_BLOBS
        return _PUSH_PASS_ENVELOPES
    if rel.startswith("events/trail/v1/"):
        return _PUSH_PASS_EVENTS
    if rel.startswith("objects/raw/v1/"):
        return _PUSH_PASS_BLOBS
    if rel.startswith("objects/traces/v1/") or rel.startswith("trace-records/"):
        return _PUSH_PASS_ENVELOPES

    # Fallback: classify into envelopes pass so it lands before manifest
    # but after blobs+events. Better to push something we didn't expect
    # than to silently drop it.
    return _PUSH_PASS_ENVELOPES


def _is_lazy_blob_path(rel: str) -> bool:
    """Return True if ``rel`` is a layer/raw blob that selective pull skips.

    Plan 080 §6: layer blobs and raw-body blobs are fetched on demand by
    ``ctx show`` / ``ctx diff``. Selective pull leaves them on the remote.
    """

    if rel.startswith("blobs/v1/"):
        parts = rel.split("/")
        if len(parts) >= 4 and parts[3] in {"context", "raw"}:
            return True
    # Legacy plan-079 / pre-080 layouts.
    if "/layers/blobs/" in rel:
        return True
    if rel.startswith("objects/raw/v1/"):
        return True
    return False


def _safe_verify_context_tree() -> dict[str, Any]:
    """Best-effort post-transfer Context Tree integrity check."""

    try:
        from .bucket_store import verify_context_tree_layer_refs
    except ImportError:
        return {
            "state": "unavailable",
            "trace_count": 0,
            "dangling_layer_refs_count": 0,
        }
    try:
        return verify_context_tree_layer_refs()
    except Exception as exc:  # pragma: no cover — defensive surfacing
        return {
            "state": "error",
            "error": str(exc),
            "trace_count": 0,
            "dangling_layer_refs_count": 0,
        }


def _substrate_aware_fake_push(
    fake_root: Path | None = None,
    *,
    force: bool = False,
    unsafe_push: bool = False,
) -> dict[str, Any]:
    """Wrap ``fake_remote_push`` with substrate-aware filtering + verify.

    ``fake_remote_push`` copies the full local bucket. Pre-filter Context
    Tree paths for ineligible projects by temporarily moving them aside,
    then restore them after the copy. Manifest is rebuilt inside
    ``fake_remote_push``, so its digest reflects the filtered bucket.
    """

    blocked, skipped_substrates = _context_tree_skip_set(unsafe_push=unsafe_push)
    bucket_root = paths.bucket_dir()
    context_tree_files_uploaded = 0
    moved_paths: list[tuple[Path, Path]] = []
    staging_dir: Path | None = None
    if blocked and bucket_root.exists():
        staging_dir = Path(tempfile.mkdtemp(prefix="opentraces-bucket-skip-"))
        for slug in sorted(blocked):
            src = bucket_root / "contexts" / "v1" / slug
            if not src.exists():
                continue
            dst = staging_dir / slug
            shutil.move(str(src), str(dst))
            moved_paths.append((src, dst))
    try:
        if unsafe_push and bucket_root.exists():
            ctx_root = bucket_root / "contexts" / "v1"
            if ctx_root.exists():
                for path in ctx_root.rglob("*"):
                    if path.is_file():
                        context_tree_files_uploaded += 1
        result = fake_remote_push(remote_root=fake_root, force=force)
    finally:
        for src, dst in moved_paths:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
        if staging_dir is not None and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
    verification = _safe_verify_context_tree()
    return {
        **result,
        "skipped_substrates": skipped_substrates,
        "context_tree_files_uploaded": context_tree_files_uploaded,
        "verification": verification,
    }


def _substrate_aware_fake_pull(
    fake_root: Path | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Wrap ``fake_remote_pull`` so it always runs verify post-transfer."""

    result = fake_remote_pull(remote_root=fake_root, force=force)
    verification = _safe_verify_context_tree()
    return {**result, "verification": verification}
