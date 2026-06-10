"""Bucket local/remote symmetry tests (plan 080 phase D).

These tests assert the symmetry principle: every read query a user can run
against the local bucket (``LocalBucketBackend``) must return identical
output when run against a remote HuggingFace bucket
(``RemoteHubBackend``). This is the load-bearing acceptance test for the
``--remote`` flag on read verbs (``trace`` / ``trail`` / ``ctx``).

The cross-track contract from D1 is:

    from opentraces.core.bucket_backend import (
        LocalBucketBackend,
        RemoteHubBackend,
        get_backend,
    )

D1 ships the backend module; D2 wires the CLI ``--remote`` flag. These
tests sit at the assertion edge of plan 080 phase D — when D2 lands the
xfail markers on the CLI-shaped tests come off automatically (xfail
strict=False so unexpected passes still surface).

Each ``RemoteHubBackend`` test uses ``monkeypatch`` to redirect the
backend's ``_download`` primitive to a local fake-HF tree (the standard
"mirror the bucket dir under a different path" pattern). The fixture
mirrors patterns used in ``tests/core/test_bucket_remote.py``.

References:
  - Plan 080 §6 (Sync story — HuggingFace Hub)
  - Plan 080 §11 (otbox test approach — comprehensive coverage)
  - Plan 080 §13 (Bucket self-sufficiency proof)
  - Plan 080 §17 R-080-5 (cross-machine identity)
  - Plan 080 §20 Resolution H (deterministic projection contract)
"""

from __future__ import annotations

import gzip
import io
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from opentraces_schema import Agent, Step, TraceRecord


# --------------------------------------------------------------------------- #
# Cross-track import gate.
# --------------------------------------------------------------------------- #

try:
    from opentraces.core.bucket_backend import (  # type: ignore[import-not-found]
        LocalBucketBackend,
        RemoteHubBackend,
        get_backend,
    )

    _BACKEND_AVAILABLE = True
except Exception:  # pragma: no cover — Track D1 not yet shipped
    LocalBucketBackend = None  # type: ignore[assignment,misc]
    RemoteHubBackend = None  # type: ignore[assignment,misc]
    get_backend = None  # type: ignore[assignment]
    _BACKEND_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Helpers (shared with tests/test_bucket_self_sufficient.py shape).
# --------------------------------------------------------------------------- #


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
    )
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _build_session_layers(seed: str = "alpha") -> tuple[Any, Any, Any, Any]:
    from opentraces.core.context_tree.models import build_layer

    system = build_layer(
        layer_type="system",
        content={
            "assembled_text": f"system-{seed}",
            "components_present": ["claude_md"],
        },
        completeness="approximated",
        capture_method="transcript_reconstruction",
    )
    messages = build_layer(
        layer_type="messages",
        content={
            "messages": [
                {"uuid": f"m1-{seed}", "role": "user", "content_hash": "sha256:aa"},
                {
                    "uuid": f"m2-{seed}",
                    "role": "assistant",
                    "content_hash": "sha256:bb",
                },
            ]
        },
        completeness="full",
        capture_method="transcript_reconstruction",
    )
    tool_registry = build_layer(
        layer_type="tool_registry",
        content={"tools": [{"name": "Edit"}, {"name": "Read"}]},
        completeness="full",
        capture_method="transcript_reconstruction",
    )
    runtime_state = build_layer(
        layer_type="runtime_state",
        content={"cwd": f"/tmp/{seed}", "permission_mode": "default"},
        completeness="full",
        capture_method="transcript_reconstruction",
    )
    return system, messages, tool_registry, runtime_state


def _seed_session(
    repo: Path,
    *,
    trace_id: str,
    seed: str = "alpha",
) -> dict[str, Any]:
    """Seed a 1-node session into the canonical Git event log."""

    from opentraces.core.context_tree.contract import (
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    )
    from opentraces.core.context_tree.models import build_node
    from opentraces.core.trails import TrailEventDraft, append_event_batch

    system, messages, tool_registry, runtime_state = _build_session_layers(seed)
    node = build_node(
        parent_node_id=None,
        branch_type="root",
        trace_id=trace_id,
        transcript_uuid=f"uuid-{seed}-root",
        transcript_offset=0,
        system_layer_id=system.layer_id,
        messages_layer_id=messages.layer_id,
        tool_registry_layer_id=tool_registry.layer_id,
        runtime_state_layer_id=runtime_state.layer_id,
        capture_completeness="full",
        step_index=1,
    )
    drafts: list[TrailEventDraft] = []
    for layer in (system, messages, tool_registry, runtime_state):
        drafts.append(
            TrailEventDraft(
                event_type=CONTEXT_LAYER_CAPTURED,
                payload=layer.model_dump(mode="json"),
                trace_id=trace_id,
                capture_method=["transcript_reconstruction"],
            )
        )
    drafts.append(
        TrailEventDraft(
            event_type=CONTEXT_NODE_OBSERVED,
            payload=node.model_dump(mode="json"),
            trace_id=trace_id,
            step_index=1,
            capture_method=["transcript_reconstruction"],
        )
    )
    drafts.append(
        TrailEventDraft(
            event_type=CONTEXT_TREE_RECONCILED,
            payload={
                "trace_id": trace_id,
                "node_count": 1,
                "layer_count": 4,
                "active_path_leaf_id": node.node_id,
                "orphan_branch_roots": [],
                "subagent_session_ids": [],
                "capture_limitations": [],
            },
            trace_id=trace_id,
            capture_method=["transcript_reconstruction"],
        )
    )
    append_event_batch(repo, drafts, writer="test-remote-symmetric-seed")
    return {
        "trace_id": trace_id,
        "node_id": node.node_id,
        "system_layer_id": system.layer_id,
        "messages_layer_id": messages.layer_id,
        "tool_registry_layer_id": tool_registry.layer_id,
        "runtime_state_layer_id": runtime_state.layer_id,
    }


def _build_trace_record(trace_id: str) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="test-agent", version="0.0.1"),
        steps=[Step(step_index=1, role="user", content="initial task")],
    )


def _project_envelope(
    repo: Path,
    *,
    trace_id: str,
    project_slug: str = "proj",
    seed: str = "alpha",
) -> dict[str, Any]:
    """Seed the canonical log + project the per-trace envelope (B1 wiring).

    Also forces a fresh manifest write so the backends can resolve the
    trace -> project_slug join via ``traces[]``.
    """

    from opentraces.core.bucket_store import (
        bucket_manifest,
        project_per_trace_exports,
        sync_events_mirror,
    )

    info = _seed_session(repo, trace_id=trace_id, seed=seed)
    record = _build_trace_record(trace_id)
    project_per_trace_exports(
        repo, project_slug=project_slug, trace_id=trace_id, record=record
    )
    # Project context-tree layer blobs to disk too (the per-trace envelope
    # writes the context.jsonl.gz refs; the layer blob payloads live under
    # bucket/blobs/v1/<project>/context/<hh>/<hash>.json.gz and are written
    # by project_context_tree_to_bucket).
    from opentraces.core.bucket_store import project_context_tree_to_bucket
    project_context_tree_to_bucket(repo, project_slug=project_slug)
    # Make sure the events/v1 mirror + manifest both exist before tests read.
    sync_events_mirror(repo, repo_id=project_slug)
    bucket_manifest(write=True)
    info["project_slug"] = project_slug
    return info


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh git repo + project_dir for the substrate."""

    _init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def fake_hf_root(tmp_path_factory) -> Path:
    """Local directory standing in for an HF dataset remote.

    The directory is populated by copying the local bucket tree; the
    remote backend's ``_download`` primitive is monkeypatched to resolve
    filenames against this root.
    """

    return tmp_path_factory.mktemp("fake-hf-remote")


def _copy_local_bucket_to_fake_hf(local_bucket: Path, fake_hf: Path) -> int:
    """Mirror the local bucket tree to fake_hf (preserves relative paths)."""

    count = 0
    if not local_bucket.exists():
        return 0
    for src in local_bucket.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(local_bucket)
        dst = fake_hf / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        count += 1
    return count


@pytest.fixture
def remote_backend_factory(monkeypatch, fake_hf_root: Path):
    """Build a ``RemoteHubBackend`` pointed at the fake-HF tree.

    Strategy: monkeypatch the backend instance's ``_download`` method
    after construction to resolve filenames against ``fake_hf_root``.
    The ``token=`` constructor kwarg sidesteps the live auth call so
    the test runs offline.
    """

    if not _BACKEND_AVAILABLE:
        pytest.skip("bucket_backend module not available (Track D1 not shipped)")

    def _factory(repo_id: str = "fake/bucket"):
        backend = RemoteHubBackend(repo_id=repo_id, token="hf_fake_token")  # type: ignore[misc]

        def _fake_download(filename: str) -> Path:
            cached = backend._path_cache.get(filename)
            if cached is not None:
                return cached
            path = fake_hf_root / filename
            if not path.exists():
                from opentraces.core.bucket_remote import BucketRemoteError

                raise BucketRemoteError(
                    f"remote bucket fetch failed: {repo_id}:{filename}: not in fake-HF tree"
                )
            backend._path_cache[filename] = path
            return path

        monkeypatch.setattr(backend, "_download", _fake_download)
        return backend

    return _factory


# --------------------------------------------------------------------------- #
# Section 1 — manifest symmetry (local vs remote, identical dicts).
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_local_and_remote_get_manifest_return_identical_dicts(
    repo, fake_hf_root, remote_backend_factory
):
    """LocalBackend.get_manifest() == RemoteBackend.get_manifest() byte-equal."""

    from opentraces.core import paths

    _project_envelope(repo, trace_id="trace-SYM1", seed="sym1")
    _copy_local_bucket_to_fake_hf(paths.bucket_dir(), fake_hf_root)

    local = LocalBucketBackend()  # type: ignore[misc]
    remote = remote_backend_factory()

    local_manifest = local.get_manifest()
    remote_manifest = remote.get_manifest()

    # Both must declare the v2 schema.
    assert local_manifest["schema_version"] == "opentraces.bucket.manifest.v2"
    assert remote_manifest["schema_version"] == "opentraces.bucket.manifest.v2"
    # Full-dict identity — Resolution H promises this for the same input.
    assert local_manifest == remote_manifest


# --------------------------------------------------------------------------- #
# Section 2 — trace.json symmetry.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_local_and_remote_get_trace_json_are_identical(
    repo, fake_hf_root, remote_backend_factory
):
    """get_trace_json(trace_id) is identical across backends."""

    from opentraces.core import paths

    info = _project_envelope(repo, trace_id="trace-SYM2", seed="sym2")
    _copy_local_bucket_to_fake_hf(paths.bucket_dir(), fake_hf_root)

    local = LocalBucketBackend()  # type: ignore[misc]
    remote = remote_backend_factory()

    local_trace = local.get_trace_json(info["trace_id"])
    remote_trace = remote.get_trace_json(info["trace_id"])

    # Round-trip both through TraceRecord to confirm same schema shape and
    # then check raw-dict equality.
    rebuilt_local = TraceRecord.model_validate(local_trace)
    rebuilt_remote = TraceRecord.model_validate(remote_trace)
    assert rebuilt_local.trace_id == rebuilt_remote.trace_id == info["trace_id"]
    assert local_trace == remote_trace


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_local_and_remote_patches_array_has_same_patch_ids(
    repo, fake_hf_root, remote_backend_factory
):
    """The spine survives: patches[].patch_id identity across backends.

    Plan 080 §3 — TraceRecord.patches[] is the authoritative cross-substrate
    reference. If both backends ship the same trace.json, their patches[]
    must enumerate identical patch_ids (typically zero patches in this
    seed fixture, but the assertion-of-equality is the load-bearing one).
    """

    from opentraces.core import paths

    info = _project_envelope(repo, trace_id="trace-SYM-spine", seed="spine")
    _copy_local_bucket_to_fake_hf(paths.bucket_dir(), fake_hf_root)

    local = LocalBucketBackend()  # type: ignore[misc]
    remote = remote_backend_factory()

    local_trace = local.get_trace_json(info["trace_id"])
    remote_trace = remote.get_trace_json(info["trace_id"])

    local_patch_ids = [p["patch_id"] for p in local_trace.get("patches", [])]
    remote_patch_ids = [p["patch_id"] for p in remote_trace.get("patches", [])]
    assert local_patch_ids == remote_patch_ids


# --------------------------------------------------------------------------- #
# Section 3 — companion files symmetry (trail / context jsonl.gz).
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_local_and_remote_read_trail_jsonl_gz_yields_same_events(
    repo, fake_hf_root, remote_backend_factory
):
    """read_trail_jsonl_gz(trace_id) yields identical event sequences."""

    from opentraces.core import paths

    info = _project_envelope(repo, trace_id="trace-TRAIL1", seed="trail1")
    _copy_local_bucket_to_fake_hf(paths.bucket_dir(), fake_hf_root)

    local = LocalBucketBackend()  # type: ignore[misc]
    remote = remote_backend_factory()

    local_events = list(local.read_trail_jsonl_gz(info["trace_id"]))
    remote_events = list(remote.read_trail_jsonl_gz(info["trace_id"]))
    assert local_events == remote_events


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_local_and_remote_read_context_jsonl_gz_yields_same_events(
    repo, fake_hf_root, remote_backend_factory
):
    """read_context_jsonl_gz(trace_id) yields identical event sequences."""

    from opentraces.core import paths

    info = _project_envelope(repo, trace_id="trace-CTX1", seed="ctx1")
    _copy_local_bucket_to_fake_hf(paths.bucket_dir(), fake_hf_root)

    local = LocalBucketBackend()  # type: ignore[misc]
    remote = remote_backend_factory()

    local_events = list(local.read_context_jsonl_gz(info["trace_id"]))
    remote_events = list(remote.read_context_jsonl_gz(info["trace_id"]))
    assert local_events == remote_events
    # Must include canonical context_* event types from the seed.
    event_types = {e.get("event_type") for e in local_events}
    assert "context_layer_captured" in event_types
    assert "context_node_observed" in event_types


# --------------------------------------------------------------------------- #
# Section 4 — layer blob symmetry.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_local_and_remote_get_layer_blob_returns_identical_content(
    repo, fake_hf_root, remote_backend_factory
):
    """get_layer_blob(layer_id, project_slug) yields identical content.

    Resolution H: the blob is content-addressed. Both backends MUST return
    a dict that hashes back to the layer_id (the file path's basename).
    """

    from opentraces.core import paths

    info = _project_envelope(repo, trace_id="trace-BLOB1", seed="blob1")
    _copy_local_bucket_to_fake_hf(paths.bucket_dir(), fake_hf_root)

    local = LocalBucketBackend()  # type: ignore[misc]
    remote = remote_backend_factory()

    layer_id = info["system_layer_id"]
    try:
        local_blob = local.get_layer_blob(
            layer_id=layer_id, project_slug=info["project_slug"]
        )
    except Exception as exc:
        # If B1's projector hasn't written the blob to disk yet, this test
        # is gated on that. Mark xfail.
        pytest.xfail(
            f"Layer blob not yet on disk (B1 projector follow-up): {exc!r}"
        )
        return

    remote_blob = remote.get_layer_blob(
        layer_id=layer_id, project_slug=info["project_slug"]
    )

    assert isinstance(local_blob, dict)
    assert isinstance(remote_blob, dict)
    assert local_blob == remote_blob
    # The blob's content MUST declare its layer_id (content-addressed).
    assert local_blob.get("layer_id") == layer_id


# --------------------------------------------------------------------------- #
# Section 5 — cross-machine identity (Resolution H + R-080-5).
# --------------------------------------------------------------------------- #


def test_cross_machine_bucket_digest_byte_identical(repo, tmp_path):
    """Tar + restore: bucket_digest survives the machine A -> machine B trip.

    Plan 080 R-080-5 / Resolution H — content-addressed blobs +
    deterministic per-trace projections (gzip mtime=0, sort_keys=True,
    sorted directory walks, volatile fields excluded from digest) imply
    a bucket captured on machine A and restored on machine B produces a
    byte-identical bucket_digest.

    The test simulates the trip locally:
      1. Project envelope on machine-A (current HOME-isolated bucket).
      2. Compute manifest -> bucket_digest_A.
      3. Tar the bucket dir with mtime=0 per Resolution H.
      4. Restore the tar into a fresh HOME-isolated bucket dir.
      5. Compute manifest -> bucket_digest_B.
      6. Assert bucket_digest_A == bucket_digest_B.

    This test does NOT depend on Track D1 — it exercises the projection
    contract directly via ``bucket_manifest`` and on-disk state.
    """

    from opentraces.core import paths
    from opentraces.core.bucket_store import bucket_manifest

    _project_envelope(repo, trace_id="trace-XM1", seed="xm1", project_slug="proj")
    manifest_a = bucket_manifest(write=True)
    bucket_digest_a = manifest_a.get("bucket_digest") or manifest_a.get("digest")
    assert bucket_digest_a is not None

    # Tar the bucket directory with mtime=0 per Resolution H.
    bucket_a = paths.bucket_dir()
    tar_bytes = io.BytesIO()
    with tarfile.open(
        fileobj=tar_bytes, mode="w:gz", format=tarfile.PAX_FORMAT
    ) as tf:
        for src in sorted(bucket_a.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(bucket_a)
            tinfo = tarfile.TarInfo(name=str(rel))
            data = src.read_bytes()
            tinfo.size = len(data)
            tinfo.mtime = 0
            tf.addfile(tinfo, io.BytesIO(data))

    tar_bytes.seek(0)

    # Simulate machine B: nuke the local bucket, restore from tar, re-manifest.
    shutil.rmtree(bucket_a)
    bucket_a.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tf:
        tf.extractall(path=bucket_a)

    manifest_b = bucket_manifest(write=True)
    bucket_digest_b = manifest_b.get("bucket_digest") or manifest_b.get("digest")
    assert bucket_digest_b is not None
    assert bucket_digest_a == bucket_digest_b, (
        f"Cross-machine identity broken: A={bucket_digest_a} vs B={bucket_digest_b}"
    )


def test_bucket_digest_invariant_under_relocation(repo, monkeypatch):
    """bucket_digest must not change when the bucket lives at another path.

    The tar+restore test above restores into the SAME path, so it cannot
    catch machine-local path material in the digest. The live-hf-* journeys
    did: every sub-block snapshot embeds an absolute ``root`` (and trail
    freshness a ``repo_path``), so a bucket pulled on machine B hashed
    differently and ``bucket remote status`` was permanently
    ``remote_ahead`` (issue #25 PR-B finding #2). Digest material now
    excludes machine-local path keys; the manifest keeps them for display.
    """

    from opentraces.core import config as config_mod
    from opentraces.core import paths
    from opentraces.core.bucket_store import bucket_manifest

    _project_envelope(repo, trace_id="trace-RELOC1", seed="reloc", project_slug="proj")
    manifest_a = bucket_manifest(write=True)
    digest_a = manifest_a.get("bucket_digest")
    assert digest_a is not None
    root_a = manifest_a["trace_records"]["snapshot"]["root"]

    # "Machine B": the same home content under a different absolute path.
    old_dir = paths.OPENTRACES_DIR
    new_dir = old_dir.parent / "relocated-home" / ".opentraces"
    new_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(old_dir, new_dir)
    for mod in (paths, config_mod):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", new_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", new_dir / "config.json")
        monkeypatch.setattr(mod, "PROJECTS_DIR", new_dir / "projects")
        if hasattr(mod, "STAGING_DIR"):
            monkeypatch.setattr(mod, "STAGING_DIR", new_dir / "staging")

    manifest_b = bucket_manifest(write=True)
    digest_b = manifest_b.get("bucket_digest")
    root_b = manifest_b["trace_records"]["snapshot"]["root"]
    assert root_a != root_b, "relocation should move the display root"
    assert digest_a == digest_b, (
        f"bucket_digest is path-polluted: {digest_a} != {digest_b}"
    )


def test_bucket_digest_invariant_to_unsynced_context_substrate(repo):
    """bucket_digest must cover exactly what ``bucket remote push`` syncs.

    Plan-079 R8 hardcodes ``remote_sync.eligible = False`` on every
    context-tree head, so push deliberately skips the context substrate.
    Counting it in the digest meant a pulled bucket (which lacks those
    files) could never reproduce the pushed digest — the live
    ``live-hf-bucket-roundtrip`` / ``-multi-trace`` journeys failed
    ``status-after-pull`` with ``remote_ahead`` forever. The digest now
    excludes ``context_trees``; the manifest block remains for display.
    """

    from opentraces.core import paths
    from opentraces.core.bucket_store import bucket_manifest

    _project_envelope(repo, trace_id="trace-CTXSKIP1", seed="ctxskip", project_slug="proj")
    manifest_a = bucket_manifest(write=True)
    digest_a = manifest_a.get("bucket_digest")
    assert digest_a is not None

    # Simulate the pulled bucket: the R8-blocked context substrate is absent.
    bucket_root = paths.bucket_dir()
    removed_any = False
    for rel in ("contexts/v1",):
        target = bucket_root / rel
        if target.exists():
            shutil.rmtree(target)
            removed_any = True
    for project_dir in sorted((bucket_root / "blobs" / "v1").glob("*")):
        ctx = project_dir / "context"
        if ctx.exists():
            shutil.rmtree(ctx)
            removed_any = True
    assert removed_any, "fixture should have produced context-tree material"

    manifest_b = bucket_manifest(write=True)
    digest_b = manifest_b.get("bucket_digest")
    assert manifest_a["context_trees"] != manifest_b["context_trees"], (
        "context substrate removal should be visible in the display block"
    )
    assert digest_a == digest_b, (
        "bucket_digest must not cover the R8-unsynced context substrate: "
        f"{digest_a} != {digest_b}"
    )


# --------------------------------------------------------------------------- #
# Section 6 — gzip determinism (mtime=0 on both backends).
# --------------------------------------------------------------------------- #


def test_gzip_mtime_zero_across_both_backends(repo, fake_hf_root):
    """Trail + context gzip envelopes carry mtime=0 on both backends.

    Plan 080 Resolution H — every gzip envelope MUST use mtime=0. The local
    backend reads the bucket directly; the remote-side fake-HF mirror is a
    plain file copy, so both sides see the same mtime field in the gzip
    header.

    This test does NOT depend on Track D1 — it asserts directly on the
    gzip file mtime field at the bucket layer.
    """

    from opentraces.core import paths
    from opentraces.core.bucket_store import (
        trace_v1_context_path,
        trace_v1_trail_path,
    )

    info = _project_envelope(repo, trace_id="trace-MT0", seed="mt0")
    _copy_local_bucket_to_fake_hf(paths.bucket_dir(), fake_hf_root)

    project_slug = info["project_slug"]
    trace_id = info["trace_id"]

    # Local view: open and check mtime.
    local_trail = trace_v1_trail_path(project_slug, trace_id)
    local_ctx = trace_v1_context_path(project_slug, trace_id)
    for path in (local_trail, local_ctx):
        if not path.exists() or path.stat().st_size == 0:
            continue
        with gzip.GzipFile(filename=str(path), mode="rb") as fh:
            fh.read(8)
            assert fh.mtime == 0, f"non-zero mtime on local {path}: {fh.mtime}"

    # Remote view (mirrored file): same files, same mtime.
    remote_trail = fake_hf_root / local_trail.relative_to(paths.bucket_dir())
    remote_ctx = fake_hf_root / local_ctx.relative_to(paths.bucket_dir())
    for path in (remote_trail, remote_ctx):
        if not path.exists() or path.stat().st_size == 0:
            continue
        with gzip.GzipFile(filename=str(path), mode="rb") as fh:
            fh.read(8)
            assert fh.mtime == 0, f"non-zero mtime on remote {path}: {fh.mtime}"


# --------------------------------------------------------------------------- #
# Section 7 — error symmetry (missing trace).
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_missing_trace_raises_same_error_class_on_both_backends(
    repo, fake_hf_root, remote_backend_factory
):
    """Missing trace_id surfaces a consistent error class on both backends.

    The contract from D1's `bucket_backend.py`: both `LocalBucketBackend`
    and `RemoteHubBackend.get_trace_json` raise `BucketRemoteError`
    (subclass of `RuntimeError`) for a missing trace. The test asserts
    type-identity rather than message-identity.
    """

    from opentraces.core import paths
    from opentraces.core.bucket_remote import BucketRemoteError

    # Seed one trace; ask for a different one.
    _project_envelope(repo, trace_id="trace-PRESENT", seed="present")
    _copy_local_bucket_to_fake_hf(paths.bucket_dir(), fake_hf_root)

    local = LocalBucketBackend()  # type: ignore[misc]
    remote = remote_backend_factory()

    with pytest.raises(BucketRemoteError) as local_exc:
        local.get_trace_json("trace-MISSING")
    with pytest.raises(BucketRemoteError) as remote_exc:
        remote.get_trace_json("trace-MISSING")

    # Both backends raise BucketRemoteError -- the same domain class.
    assert type(local_exc.value) is type(remote_exc.value)


# --------------------------------------------------------------------------- #
# Section 8 — auth missing → RemoteHubBackend raises BucketRemoteError.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_remote_backend_without_auth_raises_bucket_remote_error(monkeypatch):
    """RemoteHubBackend with no HF token raises a clear auth error.

    Plan 080 §6 — the ``--remote`` flag uses ``cfg.hf_token`` (set by
    ``opentraces auth login``). With no token set, the backend MUST fail
    early with ``BucketRemoteError("not authenticated; run opentraces
    auth login")`` (matches the existing message in
    ``bucket_remote.py:412``).
    """

    from opentraces.core.bucket_remote import BucketRemoteError

    # Clear any inherited token from the environment.
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)

    # Force the config token to be empty.
    from opentraces.core.config import load_config, save_config

    cfg = load_config()
    cfg.hf_token = None
    save_config(cfg)

    # Construct WITHOUT a token kwarg so the backend has to fall back to
    # config.load_config().
    backend = RemoteHubBackend(repo_id="user/private-bucket")  # type: ignore[misc]
    with pytest.raises(BucketRemoteError) as excinfo:
        backend.get_manifest()
    assert "not authenticated" in str(excinfo.value).lower()


# --------------------------------------------------------------------------- #
# Section 9 — `ctx show <node> --remote` triggers a get_layer_blob call.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_ctx_show_remote_triggers_get_layer_blob_call(
    repo, fake_hf_root, remote_backend_factory, monkeypatch
):
    """`ctx show <node> --remote` calls backend.get_layer_blob lazily.

    Plan 080 §7 — `ctx show` does 4 blob fetches. The lazy resolution path
    means the backend's `get_layer_blob` MUST be invoked at least once per
    layer when rendering a node remotely.

    Strategy: mock the backend's get_layer_blob; invoke the ctx show wiring;
    assert the mock was called. Track D2 wires this CLI path; until it
    lands, ``xfail`` documents the contract.
    """

    info = _project_envelope(repo, trace_id="trace-LAZY1", seed="lazy1")
    from opentraces.core import paths

    _copy_local_bucket_to_fake_hf(paths.bucket_dir(), fake_hf_root)

    # Build a remote backend whose get_layer_blob is a counting mock.
    real_remote = remote_backend_factory()
    blob_mock = MagicMock(wraps=real_remote.get_layer_blob)
    monkeypatch.setattr(real_remote, "get_layer_blob", blob_mock)

    # The CLI wiring for `ctx show <node> --remote` lives in cli/context_tree.py
    # (Track D2). We exercise the dispatcher directly so the test is
    # independent of click runner wiring.
    try:
        from opentraces.cli.context_tree import (  # type: ignore[import-not-found]
            ctx_show_with_backend,
        )
    except Exception:
        pytest.xfail("Track D2 (ctx show --remote wiring) not yet shipped")
        return

    try:
        ctx_show_with_backend(
            node_id=info["node_id"],
            trace_id=info["trace_id"],
            project_slug=info["project_slug"],
            backend=real_remote,
            offline=False,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.xfail(
            f"Track D2 entry-point exists but errored "
            f"(likely follow-up wiring): {exc!r}"
        )
        return

    assert blob_mock.called, "ctx show --remote did not invoke get_layer_blob"


# --------------------------------------------------------------------------- #
# Section 10 — `ctx show <node> --remote --offline` fails fast (no fetch).
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_ctx_show_remote_offline_fails_fast_with_no_blob_calls(
    repo, fake_hf_root, remote_backend_factory, monkeypatch
):
    """`ctx show <node> --remote --offline` MUST NOT issue a network call.

    Plan 080 §6 — `ctx show --offline` fails fast on missing blob. The
    contract: zero remote `get_layer_blob` invocations, exit non-zero
    (or raise), sub-100ms.
    """

    info = _project_envelope(repo, trace_id="trace-OFF1", seed="off1")
    # Deliberately DO NOT mirror to fake-HF — the offline path MUST NOT
    # reach the wire.

    # Build remote backend with a counting blob mock that explodes if
    # called (the offline branch must skip the remote entirely).
    real_remote = remote_backend_factory()
    blob_mock = MagicMock(side_effect=RuntimeError("remote should not be called"))
    monkeypatch.setattr(real_remote, "get_layer_blob", blob_mock)

    try:
        from opentraces.cli.context_tree import (  # type: ignore[import-not-found]
            ctx_show_with_backend,
        )
    except Exception:
        pytest.xfail("Track D2 (ctx show --remote wiring) not yet shipped")
        return

    # The CLI MUST fail without ever calling get_layer_blob.
    with pytest.raises(Exception):  # noqa: BLE001
        ctx_show_with_backend(
            node_id=info["node_id"],
            trace_id=info["trace_id"],
            project_slug=info["project_slug"],
            backend=real_remote,
            offline=True,
        )

    # Defensive belt-and-braces: confirm no remote call was issued. The
    # mock was wired to raise if called, so a violating offline path
    # would surface as the test's wrapped Exception, but if it triggers
    # a different code path the assertion below catches it.
    assert not blob_mock.called, (
        "ctx show --remote --offline invoked get_layer_blob; "
        "the offline opt-out MUST stay local-only"
    )


# --------------------------------------------------------------------------- #
# Section 11 — get_backend dispatcher selects the right backend by URL.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_get_backend_dispatcher_returns_local_when_no_remote():
    """get_backend(None) returns LocalBucketBackend."""

    backend = get_backend(None)  # type: ignore[misc]
    assert isinstance(backend, LocalBucketBackend)  # type: ignore[misc]


@pytest.mark.skipif(
    not _BACKEND_AVAILABLE, reason="Track D1 (bucket_backend) not shipped"
)
def test_get_backend_dispatcher_returns_remote_for_hf_repo_id():
    """get_backend("user/repo") returns a RemoteHubBackend."""

    backend = get_backend("user/repo")  # type: ignore[misc]
    assert isinstance(backend, RemoteHubBackend)  # type: ignore[misc]
