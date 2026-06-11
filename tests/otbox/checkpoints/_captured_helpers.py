"""Captured-session checkpoint helpers — plan 072 R1.

The captured-session checkpoint family (``c-captured-real-session``,
``c-captured-with-revert``, ``c-captured-with-secrets``,
``c-captured-multi-skill``, ``c-captured-with-pr-branch``) has two
source-of-truth tiers:

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
import os
import re
import shutil
import sys
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


# Absolute symlink target rooted in ANY machine's otbox box dir
# (same shape as snapshot._ANY_BOX_ROOT_RE, anchored for a whole target).
_BOX_ROOT_LINK_RE = re.compile(
    r"^(?P<root>/.*?/\.otbox/boxes/otb_[0-9a-fA-F]+)(?P<rest>/.*)?$"
)


def _retarget_box_symlinks(box: Box) -> int:
    """Re-point symlinks whose target lives under an origin box root.

    ``snapshot.rewrite_absolute_paths`` rewrites absolute box roots inside
    text-file CONTENTS (+ SQLite values), but a tar extraction preserves
    symlink TARGETS verbatim. A captured world carries e.g. the skill
    harness link ``home/.claude/skills/opentraces`` →
    ``<origin-box>/home/.agents/skills/opentraces``; after restore that
    target dangles (or points at a foreign box), ``doctor`` correctly
    reports it under ``hooks[].broken_harnesses`` and exits 3 — restored
    worlds must be healthy, so restore hygiene re-roots every such link
    onto the current box. Relative symlinks and links outside an
    ``/.otbox/boxes/otb_<id>`` root are left untouched. Best-effort:
    unreadable/unwritable entries are skipped, never fatal.
    """
    retargeted = 0
    if not box.root.exists():
        return retargeted
    # NB: pathlib's ``**`` does not follow directory symlinks, so this
    # cannot loop even after links are re-rooted into the same box.
    for path in box.root.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            target = os.readlink(path)
        except OSError:
            continue
        match = _BOX_ROOT_LINK_RE.match(target)
        if not match:
            continue
        new_target = str(box.root) + (match.group("rest") or "")
        if new_target == target:
            continue
        try:
            path.unlink()
            path.symlink_to(new_target)
        except OSError:
            continue
        retargeted += 1
    return retargeted


def _repair_dangling_venv_interpreters(box: Box) -> int:
    """Repoint dangling venv ``bin/python*`` symlinks at the current
    interpreter.

    Venv interpreter links target the CAPTURE machine's python (e.g.
    ``/opt/homebrew/.../python3.14``); ``_retarget_box_symlinks`` leaves
    non-box targets alone, so on a cross-machine restore (Linux CI
    restoring a Mac-captured artifact) the link dangles and any journey
    step invoking ``.testvenv/bin/python`` dies with rc=127. Stdlib-only
    steps survive under any interpreter, so a repaired link is strictly
    better than a dangling one. Links that resolve on this machine are
    never touched. Best-effort, never fatal.
    """
    repaired = 0
    if not box.root.exists():
        return repaired
    for path in box.root.rglob("python*"):
        if path.parent.name != "bin" or not path.is_symlink():
            continue
        if path.exists():
            continue
        try:
            path.unlink()
            path.symlink_to(sys.executable)
        except OSError:
            continue
        repaired += 1
    return repaired


def peek_artifact_world(capture_name: str) -> dict | None:
    """Stream an artifact archive and return cheap world-shape facts
    WITHOUT restoring it: ``{"trace_count": int,
    "security_fingerprinted": bool}``.

    ``trace_count`` sums ``traces`` entries across every project
    ``state.json`` in the archive; ``security_fingerprinted`` is True
    when any captured TraceRecord carries a non-empty
    ``metadata.security.tools_applied``. Returns ``None`` when the
    archive is absent or carries no readable ``state.json`` (callers
    treat that as "shape unknown" and reject).
    """
    archive, _metadata = _artifact_paths(capture_name)
    if not archive.exists():
        return None
    trace_count = 0
    security_fingerprinted = False
    state_seen = False
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                name = member.name
                if "/.opentraces/projects/" not in name:
                    continue
                if name.endswith("/state.json"):
                    handle = tar.extractfile(member)
                    if handle is None:
                        continue
                    try:
                        state = json.loads(handle.read().decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(state, dict):
                        trace_count += len(state.get("traces") or {})
                        state_seen = True
                elif "/traces/" in name and name.endswith(".jsonl"):
                    handle = tar.extractfile(member)
                    if handle is None:
                        continue
                    for raw_line in handle:
                        line = raw_line.decode("utf-8", "replace").strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            break
                        if isinstance(obj, dict):
                            sec = (obj.get("metadata") or {}).get("security") or {}
                            if sec.get("tools_applied"):
                                security_fingerprinted = True
                        break
    except (OSError, tarfile.TarError):
        return None
    if not state_seen:
        return None
    return {
        "trace_count": trace_count,
        "security_fingerprinted": security_fingerprinted,
    }


def restore_from_capture(
    driver, box: Box, capture_name: str, *,
    reconcile: bool = True,
    min_traces: int = 1,
    require_security_fingerprints: bool = False,
) -> dict | None:
    """Restore a captured snapshot artifact into ``box`` in-place.

    Plan 072 R1 contract:

      * Returns ``None`` when no artifact is present (caller falls
        through to the synthetic harness chain).
      * Returns the parsed ``metadata.json`` dict on success.
      * Contract gate: when the caller declares world-shape floors
        (``min_traces`` > 1 / ``require_security_fingerprints``), the
        archive is peeked BEFORE the box is wiped and an artifact that
        cannot satisfy the checkpoint's ``provides`` contract is
        rejected (``None`` → synthetic fallback) instead of building a
        world ``verify_provides`` would refuse anyway. Rejections are
        recorded in ``box.notes["capture_artifact_rejections"]``.
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

    if min_traces > 1 or require_security_fingerprints:
        shape = peek_artifact_world(capture_name)
        reasons: list[str] = []
        if shape is None:
            reasons.append("world shape unreadable (no state.json in archive)")
        else:
            have_traces = int(shape.get("trace_count") or 0)
            if have_traces < min_traces:
                reasons.append(
                    f"trace_count {have_traces} < required {min_traces}"
                )
            if require_security_fingerprints and not shape.get(
                "security_fingerprinted"
            ):
                reasons.append(
                    "no security-pipeline fingerprints on any captured trace"
                )
        if reasons:
            rejections = box.notes.setdefault("capture_artifact_rejections", {})
            rejections[capture_name] = "; ".join(reasons)
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

    # Symlink TARGETS are preserved verbatim by tar and untouched by the
    # text rewrite above — re-root any link still pointing into an origin
    # box (any machine's prefix) so restored worlds pass `doctor` clean.
    _retarget_box_symlinks(box)

    # Venv interpreter links point OUTSIDE any box root (at the capture
    # machine's python) and dangle on cross-machine restores; repair them
    # so `.testvenv/bin/python` journey steps survive on CI.
    _repair_dangling_venv_interpreters(box)

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
    #
    # Issue #30 note: `trace query` now lazily bootstraps a missing/stale
    # snapshot itself (one compact build + retry, rc=0 with rebuilt_index=true),
    # so the explicit rebuild below is no longer strictly required for the bare
    # first-query journeys. It is kept as a harmless world-prep step: it makes
    # the very first journey query deterministic across platforms and keeps the
    # legacy trace-index map/get/slice surface warm too.
    if driver is not None:
        driver.exec(
            box,
            ["bash", "-lc",
             'rm -rf "$HOME/.opentraces/index" '
             '"$HOME/.opentraces/bucket/projections/search" 2>/dev/null || true'],
        )
        # Then REBUILD it explicitly. Harmless world-prep (see issue #30 note
        # above): an explicit rebuild from the path-corrected bucket is
        # deterministic on every platform and warms the legacy map/get/slice
        # surface, even though a bare query would now self-heal on its own.
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


def traces_by_created_at(state: dict) -> list[dict]:
    """Every trace entry in ``state.json``, oldest first.

    Real-agent artifacts mint UUID session ids, so the audit derivers
    cannot key off the synthetic-corpus session names; capture order
    (``created_at``) is the stable enumeration both world shapes share.
    """
    traces = [t for t in (state.get("traces") or {}).values() if isinstance(t, dict)]
    return sorted(traces, key=lambda t: float(t.get("created_at") or 0.0))


def load_trace_record(driver, box: Box, trace: dict) -> dict:
    """Parse the TraceRecord JSONL a state.json trace entry points at.

    The restore path-rewrite already re-rooted ``file_path`` to the
    current box. Returns ``{}`` on any read/parse failure — callers
    fall back to state.json-level fields.
    """
    path = trace.get("file_path")
    if not isinstance(path, str) or not path:
        return {}
    raw = driver.exec(box, ["cat", path])
    if not raw.ok:
        return {}
    first = next((line for line in raw.stdout.splitlines() if line.strip()), "")
    if not first:
        return {}
    try:
        data = json.loads(first)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


# Tool names whose call mutates a file — the step a journey "explains".
EDIT_TOOL_NAMES = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def edit_step_index_from_record(record: dict) -> int | None:
    """The 1-based ``step_index`` of the first file-mutating tool call.

    Real sessions land their Edit on whatever step the agent chose, so
    artifact-restored audits derive it from the TraceRecord instead of
    assuming the synthetic corpus's pinned step.
    """
    for step in record.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for call in step.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("tool_name") in EDIT_TOOL_NAMES:
                idx = step.get("step_index")
                if isinstance(idx, int):
                    return idx
    return None


def transcript_path_for_session(driver, box: Box, session_id: str) -> str:
    """Locate the on-disk Claude transcript for ``session_id``.

    The artifact's transcript directory is named after the ORIGIN
    box's encoded project path (directory names are not rewritten on
    restore), so the path is globbed rather than re-encoded.
    """
    if not session_id:
        return ""
    paths = driver.paths(box)
    matches = driver.glob(
        box, f"{paths['home']}/.claude/projects/*/{session_id}.jsonl"
    )
    return matches[0] if matches else ""


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
