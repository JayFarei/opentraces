"""Captured-session checkpoint helpers — plan 072 R1.

The captured-session checkpoint family (``c-captured-real-session``,
``c-captured-with-revert``, ``c-captured-multi-skill``,
``c-captured-with-pr-branch``) has two source-of-truth tiers:

  1. *Artifact-preferred.* If a pre-captured snapshot artifact exists
     under ``tests/otbox/captures/<capture_name>/``, restore that into
     the current box (higher-fidelity, real-agent driven; produced by
     plan 071's ``capture-refresh`` pipeline on the Mac Mini runner).
  2. *Synthetic-fallback.* Otherwise, the checkpoint's existing
     synthetic harness chain (fake claude + fixture corpus) runs
     unchanged (default-CI safe, no artifacts shipped in OSS by
     default).

This module exports ``restore_from_capture`` — the single helper each
captured-session checkpoint calls at the top of its delta. The return
value (a metadata dict on hit, ``None`` on miss) is the signal the
checkpoint uses to decide whether to short-circuit or run the
synthetic chain.

The restore path reuses the same path-rewriting logic
``snapshot.restore_snapshot`` does for fresh-box restores, but
operates *in-place* on the current (already-provisioned) box so the
caller doesn't have to swap box identities mid-checkpoint.
"""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

from ..env import BOXES_DIR, REPO_ROOT, Box
from ..snapshot import rewrite_absolute_paths as _rewrite_paths_in_place


# tests/otbox/captures/<capture_name>/{snapshot.tar.gz, metadata.json}
_CAPTURES_ROOT = REPO_ROOT / "tests" / "otbox" / "captures"


def _captures_root() -> Path:
    """Return the captures root, overridable via ``OTBOX_CAPTURES_ROOT``.

    The env-var hook is used by ``test_artifact_restore.py`` so the
    tests can stage a fake artifact in ``tmp_path`` without polluting
    the committed ``tests/otbox/captures/`` tree.
    """
    import os

    override = os.environ.get("OTBOX_CAPTURES_ROOT")
    if override:
        return Path(override)
    return _CAPTURES_ROOT


def _artifact_paths(capture_name: str) -> tuple[Path, Path]:
    root = _captures_root() / capture_name
    return root / "snapshot.tar.gz", root / "metadata.json"


def artifact_exists(capture_name: str) -> bool:
    """True when both the archive and metadata sidecar are present."""
    archive, metadata = _artifact_paths(capture_name)
    return archive.exists() and metadata.exists()


def iter_artifacts() -> list[tuple[str, Path, Path]]:
    """Walk every committed capture artifact under the captures root.

    Yields ``(name, archive_path, metadata_path)`` triples. A directory
    counts as an artifact when it has *either* sidecar — half-committed
    states are returned so downstream auditors (e.g. the freshness
    pytest) can surface them as drift signals rather than silently
    skip.
    """
    root = _captures_root()
    if not root.exists():
        return []
    out: list[tuple[str, Path, Path]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        archive, metadata = _artifact_paths(entry.name)
        if archive.exists() or metadata.exists():
            out.append((entry.name, archive, metadata))
    return out


def _clear_box_contents(box: Box) -> None:
    """Remove the post-provision skeleton so the artifact can land cleanly.

    The artifact archive contains a full box state (home + project +
    fake-remote + logs). The current box has only the post-provision
    skeleton from the parent checkpoint chain — we wipe those entries
    (one level deep) before extraction so the archive's contents end
    up at the canonical box-root layout.
    """
    if not box.root.exists():
        return
    for child in box.root.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass


def _read_origin_box_id(box_root: Path) -> str | None:
    """Read the ``box_id`` recorded in the just-extracted ``meta.json``.

    Returns ``None`` when the meta file is absent or malformed —
    callers fall back to treating the artifact as path-agnostic
    (extraction still succeeds, just no rewrite).
    """
    meta_path = box_root / "meta.json"
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    box_id = data.get("box_id")
    return box_id if isinstance(box_id, str) and box_id else None


def restore_from_capture(
    driver, box: Box, capture_name: str, *, reconcile: bool = True
) -> dict | None:
    """Restore a captured snapshot artifact into ``box`` in-place.

    Plan 072 R1 contract:

      * Returns ``None`` when no artifact is present (caller falls
        through to the synthetic harness chain).
      * Returns the parsed ``metadata.json`` dict on success.
      * Extracts the archive into ``box.root`` after clearing the
        post-provision skeleton, then rewrites the origin-box root
        in every text file under ``~/.opentraces`` + the project
        marker + the venv shebangs (same surface as
        ``snapshot.restore_snapshot``).
      * Re-saves ``box.meta.json`` so the current box id (not the
        origin's) is the identity the rest of the checkpoint code
        sees.

    ``driver`` is accepted for parity with the rest of the checkpoint
    surface (so the helper can grow driver-mediated extraction on
    Tier 1 later); on Tier 0 it is currently unused.
    """
    archive, metadata_path = _artifact_paths(capture_name)
    if not archive.exists() or not metadata_path.exists():
        # Supply-chain fallback (otbox 2.0 phase 0): when opted in, fetch the
        # manifested artifact from the GitHub release instead of silently
        # degrading to the synthetic harness. Hash mismatch raises — a
        # corrupt artifact must never masquerade as restored.
        import os as _os

        if _os.environ.get("OT_OTBOX_FETCH_CAPTURES") == "1":
            from ..captures_manifest import fetch_scenario, manifest_entry

            if manifest_entry(capture_name) is not None:
                fetch_scenario(capture_name, _captures_root())
        if not archive.exists() or not metadata_path.exists():
            return None

    try:
        metadata = json.loads(metadata_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None

    box.root.mkdir(parents=True, exist_ok=True)
    _clear_box_contents(box)

    with tarfile.open(archive, "r:gz") as tar:
        # ``filter="fully_trusted"`` matches snapshot.restore_snapshot
        # — the archive came from our own capture-refresh pipeline.
        tar.extractall(box.root, filter="fully_trusted")

    origin_box_id = _read_origin_box_id(box.root)
    if origin_box_id:
        origin_root = str(BOXES_DIR / origin_box_id)
        _rewrite_paths_in_place(box, origin_root)

    # Carry forward the archived box's notes (the capture pipeline
    # may have recorded its own audit) before we save the new identity.
    archived_notes: dict = {}
    if box.meta_path.exists():
        try:
            archived_meta = json.loads(box.meta_path.read_text())
            archived_notes = archived_meta.get("notes", {}) or {}
        except (OSError, json.JSONDecodeError):
            pass
    if archived_notes:
        # Merge: archived notes first, then anything the current box
        # already had (parent checkpoint state) wins on key collision.
        merged = {**archived_notes, **box.notes}
        box.notes = merged

    # Overwrite the extracted meta.json with the CURRENT box identity
    # — every downstream paths() call must resolve against this box.
    box.save()

    # Drop the derived SEARCH INDEX on restore (otbox 2.0 nightly hardening):
    # the index is a fully rebuildable SQLite projection that bakes absolute
    # paths from the CAPTURE machine. On a cross-machine restore those paths
    # don't exist, and `trace query` CRASHES (rc=3) reading the stale index
    # rather than rebuilding it — the real root cause of the on-main nightly's
    # codex/pi restored-world cluster (revealed once the step-level error was
    # surfaced). Deleting it forces a clean lazy rebuild from the bucket
    # (whose paths the text rewrite already corrected). Pure derived-artifact
    # cleanup; the bucket spine is untouched.
    if driver is not None:
        driver.exec(
            box,
            ["bash", "-lc",
             'rm -rf "$HOME/.opentraces/index" '
             '"$HOME/.opentraces/bucket/projections/search" 2>/dev/null || true'],
        )
        # Then REBUILD it explicitly. `trace query` does NOT lazily rebuild a
        # missing index on CI — it returns status=maintenance_needed (exit 3).
        # An explicit rebuild from the path-corrected bucket is deterministic
        # on every platform (the journeys' first step is often a bare query
        # that assumes a ready index).
        driver.exec(box, [*driver.cli_argv(box), "trace", "index", "rebuild"])

    # Cross-machine reconcile (otbox 2.0 phase 0): restoring a captured world
    # onto a different machine leaves path-keyed trail projections stale
    # (stale_count=1 until a watcher tick re-anchors them). Reconciling is
    # world PREPARATION — the journeys' "no staleness after capture" claims
    # stay intact rather than being weakened to tolerate restore artifacts.
    project_dir = box.root / "project"
    if reconcile and driver is not None and project_dir.exists():
        tick = driver.exec(
            box,
            [*driver.cli_argv(box), "setup", "watcher", "tick",
             "--project", str(project_dir), "--json"],
        )
        metadata = dict(metadata)
        metadata["post_restore_tick_ok"] = bool(tick.ok)

    return metadata


# ---------------------------------------------------------------------------
# audit-derivation helpers — shared between synthetic + artifact paths
# ---------------------------------------------------------------------------
def capture_metadata_from_artifact(metadata: dict) -> dict:
    """Project the captured-artifact ``metadata.json`` into the
    standardized ``capture_metadata`` block stored in checkpoint audits.

    Plan 072 R4 — every captured-session audit (artifact-restored or
    synthetic-derived) MUST include a ``capture_metadata`` key so
    downstream consumers/journeys can distinguish provenance and (for
    plan 074) detect stale captures.
    """
    return {
        "source": "artifact",
        "captured_at": metadata.get("captured_at"),
        "agent_binary_name": metadata.get("binary_name"),
        "agent_binary_version": metadata.get("binary_version"),
        "opentraces_schema_version": metadata.get("opentraces_schema_version"),
        "scenario_digest": metadata.get("scenario_digest"),
    }


def synthetic_capture_metadata() -> dict:
    """The ``capture_metadata`` marker stamped onto synthetic-path audits.

    Pinned literal so journeys + tests can branch on
    ``capture_metadata.source == "synthetic"`` without ambiguity.
    """
    return {
        "source": "synthetic",
        "harness": "tests/otbox/fake_harnesses/claude",
    }


def read_state_json(driver, box: Box) -> tuple[str, dict]:
    """Glob + read the single project state.json after artifact restore.

    Returns ``(state_dir, state)`` where ``state_dir`` is the absolute
    path to the per-project directory under
    ``~/.opentraces/projects/`` and ``state`` is the parsed dict. The
    captured-session checkpoints all opt in exactly one project, so
    the glob expects a single match.
    """
    from . import CheckpointError  # local import — avoid cycle at module load

    paths = driver.paths(box)
    state_dirs = [
        path for path in driver.glob(box, f"{paths['opentraces_dir']}/projects/*")
        if driver.exec(box, ["test", "-f", f"{path}/state.json"]).ok
    ]
    if len(state_dirs) != 1:
        raise CheckpointError(
            f"expected exactly one opted-in project after artifact restore, "
            f"got {len(state_dirs)} under {paths['opentraces_dir']}/projects"
        )
    state_dir = state_dirs[0]
    raw = driver.exec(box, ["cat", f"{state_dir}/state.json"])
    if not raw.ok:
        raise CheckpointError(
            f"could not read project state at {state_dir}/state.json: "
            f"{raw.stderr.strip() or '<no stderr>'}"
        )
    try:
        state = json.loads(raw.stdout)
    except json.JSONDecodeError as exc:
        raise CheckpointError(
            f"malformed state.json at {state_dir}/state.json: {exc}"
        ) from None
    return state_dir, state


def trace_for_session(state: dict, session_id: str) -> tuple[str | None, int]:
    """Find ``(trace_id, step_count)`` for a session_id in state.json."""
    for trace in (state.get("traces") or {}).values():
        if trace.get("session_id") == session_id:
            return trace.get("trace_id"), int(trace.get("step_count") or 0)
    return None, 0


# ---------------------------------------------------------------------------
# Shared checkpoint-delta primitives (extracted from per-checkpoint copies)
# ---------------------------------------------------------------------------
def harness_interpreter() -> str:
    """The interpreter the fake-agent harness runs under.

    The fake-claude harness is otbox's OWN scaffolding: it imports
    opentraces_schema (pydantic) to build TraceRecords, exactly like a real
    agent's runtime would have its own deps. The box's project ``.testvenv``
    is built ``--no-deps`` (it only needs the opentraces CLI, resolved via
    the repo venv), so running the harness under it crashed on CI with
    ModuleNotFoundError: pydantic (the .testvenv only "worked" locally
    because the host base python leaked pydantic in). sys.executable is the
    test interpreter, which by construction has the deps on every platform.
    """
    import sys

    return sys.executable


def harness_source_with_shebang(src_path: Path) -> str:
    """Read a fake-harness file and pin its shebang to an interpreter that
    has the test deps (pydantic / opentraces_schema).

    The committed harness ships ``#!/usr/bin/env python3`` for local dev,
    where ``python3`` is usually the active venv. On CI the bare ``python3``
    is the runner's system interpreter WITHOUT the editable install, so the
    fake-claude session module crashed with ModuleNotFoundError: pydantic
    (surfaced by the first on-main nightly). We rewrite line 1 to the
    interpreter currently running the tests (``sys.executable``), which by
    construction has the deps, on every platform.
    """
    import sys

    text = src_path.read_text()
    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        lines[0] = f"#!{sys.executable}\n"
    return "".join(lines)


def check(result, *, checkpoint: str, label: str) -> None:
    """Raise CheckpointError with a uniform format on a non-ok exec result."""
    from . import CheckpointError

    if not result.ok:
        head = (result.stderr or result.stdout or "").strip().splitlines()
        tail = "\n  ".join(head[-12:]) if head else ""
        raise CheckpointError(
            f"{checkpoint} step {label!r} failed (rc={result.returncode}):\n  {tail}"
        )


def git(driver, box: Box, *args: str, checkpoint: str = "captured-session"):
    """Run ``git <args>`` in the box, raising via ``check`` on failure."""
    result = driver.exec(box, ["git", *args])
    check(result, checkpoint=checkpoint, label=f"git {' '.join(args)}")
    return result


def encode_claude_path(project: str) -> str:
    """Mirror ``opentraces.core.repo_identity.encode_claude_path``.

    Imported lazily to avoid a hard dependency on opentraces at
    helper-module import time (some checkpoint code paths run before
    the installed CLI is on sys.path).
    """
    from opentraces.core.repo_identity import encode_claude_path as _enc

    return _enc(Path(project))
