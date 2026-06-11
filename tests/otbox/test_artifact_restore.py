"""Plan 072 R5 — artifact-restore helper + checkpoint contract.

This pytest locks in the two-tier (artifact-preferred,
synthetic-fallback) source-of-truth contract for the captured-session
checkpoint family:

  * ``test_restore_from_capture_returns_none_when_absent`` proves the
    helper short-circuits cleanly when no committed artifact exists
    (default-CI state).
  * ``test_restore_from_capture_round_trips_synthetic_artifact``
    stages a tiny fake artifact in ``tmp_path``, points the captures
    root at it via ``OTBOX_CAPTURES_ROOT``, and confirms the helper
    extracts it + returns the metadata dict.
  * ``test_restore_from_capture_rejects_*`` lock the contract gate:
    an artifact whose world shape cannot satisfy the checkpoint's
    ``provides`` floors (trace count / security fingerprints) is
    rejected BEFORE the box is wiped, so the synthetic chain still
    has the parent world to build on.
  * ``test_captured_session_audit_includes_source_marker`` resolves
    ``c-captured-real-session`` and asserts the audit's
    ``capture_metadata.source`` matches the machine's artifact state:
    ``"artifact"`` where ``claude-linear-edit`` is present (plan B0
    real-agent capture), ``"synthetic"`` in default CI.
"""

from __future__ import annotations

import json
import os
import sys
import tarfile

import pytest

from tests.otbox.checkpoints._captured_helpers import (
    artifact_exists,
    restore_from_capture,
    synthetic_capture_metadata,
)
from tests.otbox.checkpoints import resolve_checkpoint
from tests.otbox.drivers import get_driver
from tests.otbox.env import Box, new_box_id


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """The otbox driver isolates HOME per box; the repo-wide conftest
    autouse fixture would otherwise redirect HOME elsewhere and break
    box lifecycle. Same override as test_agent_session_slice.py."""
    yield


@pytest.fixture(autouse=True)
def _no_release_fetch(monkeypatch):
    """The nightly exports ``OT_OTBOX_FETCH_CAPTURES=1``; these tests
    stage their own captures roots, so the release-fetch fallback must
    stay off — otherwise ``restore_from_capture`` downloads the real
    manifested artifact into the staged root mid-test and the absent
    case returns metadata instead of ``None``."""
    monkeypatch.delenv("OT_OTBOX_FETCH_CAPTURES", raising=False)


@pytest.fixture
def driver():
    return get_driver("local")


# ---------------------------------------------------------------------------
# helper short-circuit contract
# ---------------------------------------------------------------------------
def test_restore_from_capture_returns_none_when_absent(monkeypatch, tmp_path):
    """With ``OTBOX_CAPTURES_ROOT`` pointing at an empty dir, the
    helper must return ``None`` and leave the box untouched — the
    caller falls back to the synthetic chain.
    """
    captures_root = tmp_path / "captures"
    captures_root.mkdir()
    monkeypatch.setenv("OTBOX_CAPTURES_ROOT", str(captures_root))

    assert artifact_exists("claude-linear-edit") is False
    fake_box = Box(box_id=new_box_id())
    result = restore_from_capture(None, fake_box, "claude-linear-edit")
    assert result is None
    # Box root should not have been created on a None return.
    assert not fake_box.root.exists()


# ---------------------------------------------------------------------------
# round-trip contract — synthetic artifact built in tmp_path
# ---------------------------------------------------------------------------
def _build_fake_artifact(
    captures_root, capture_name: str, metadata: dict,
    *, traces: list[dict] | None = None,
    venv_python_target: str | None = None,
) -> None:
    """Stage a minimum-shape artifact under ``captures_root/<capture_name>/``.

    The artifact body is the smallest dirtree that lets
    ``restore_from_capture`` succeed: an empty ``home/`` /
    ``project/`` / ``fake-remote/`` skeleton + a ``meta.json`` at the
    box root (so the path-rewriter knows the origin box id). When
    ``traces`` is given, a project ``state.json`` + per-trace
    TraceRecord JSONLs are staged too (each entry:
    ``{"trace_id", "session_id", "record"}``) so the contract-gate
    peek has a world shape to read.
    """
    artifact_dir = captures_root / capture_name
    artifact_dir.mkdir(parents=True, exist_ok=True)

    body = artifact_dir / "_body"
    body.mkdir()
    (body / "home").mkdir()
    (body / "project").mkdir()
    (body / "fake-remote").mkdir()
    (body / "logs").mkdir()
    (body / "meta.json").write_text(json.dumps({
        "box_id": "otb_origin01",
        "driver": "local",
        "seed": None,
        "status": "captured",
        "notes": {},
    }, sort_keys=True))

    if traces:
        project_dir = body / "home" / ".opentraces" / "projects" / "p1"
        traces_dir = project_dir / "traces"
        traces_dir.mkdir(parents=True)
        state = {"traces": {}}
        for index, entry in enumerate(traces):
            trace_id = entry["trace_id"]
            state["traces"][trace_id] = {
                "trace_id": trace_id,
                "session_id": entry.get("session_id", f"sess-{index}"),
                "created_at": 1000.0 + index,
                "file_path": str(traces_dir / f"{trace_id}.jsonl"),
            }
            (traces_dir / f"{trace_id}.jsonl").write_text(
                json.dumps(entry.get("record") or {"trace_id": trace_id}) + "\n"
            )
        (project_dir / "state.json").write_text(json.dumps(state))

    if venv_python_target:
        # Mirror a captured ``.testvenv``: ``python`` links at the capture
        # machine's interpreter (the cross-machine dangling case), while
        # ``python3`` links at a path that resolves locally — and is NOT
        # sys.executable, so the test can tell "untouched" from "repaired".
        venv_bin = body / "project" / ".testvenv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").symlink_to(venv_python_target)
        (venv_bin / "python3").symlink_to("/bin/ls")

    archive = artifact_dir / "snapshot.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(body, arcname=".")

    (artifact_dir / "metadata.json").write_text(
        json.dumps(metadata, sort_keys=True, indent=2)
    )

    # Cleanup the staging dir so only the archive + metadata.json remain.
    import shutil

    shutil.rmtree(body)


def test_restore_from_capture_round_trips_synthetic_artifact(
    monkeypatch, tmp_path,
):
    """A staged fake artifact is restored and metadata is returned."""
    captures_root = tmp_path / "captures"
    captures_root.mkdir()
    monkeypatch.setenv("OTBOX_CAPTURES_ROOT", str(captures_root))
    # Re-route the box state root so the test doesn't touch the real .otbox/.
    monkeypatch.setattr(
        "tests.otbox.checkpoints._captured_helpers.BOXES_DIR",
        tmp_path / "boxes",
    )
    monkeypatch.setattr(
        "tests.otbox.env.BOXES_DIR",
        tmp_path / "boxes",
    )

    fake_metadata = {
        "captured_at": "2026-05-16T10:00:00+00:00",
        "scenario_name": "round-trip-fixture",
        "scenario_digest": "deadbeef" * 8,
        "agent": "echo",
        "binary_name": "echo",
        "binary_path": "/usr/bin/echo",
        "binary_version": "8.32",
        "turn_count": 1,
        "base_checkpoint": "c-installed-source",
        "opentraces_schema_version": "0.0.0-test",
        "opentraces_cli_version": "0.0.0-test",
    }
    _build_fake_artifact(captures_root, "round-trip-fixture", fake_metadata)

    assert artifact_exists("round-trip-fixture") is True

    box = Box(box_id=new_box_id())
    result = restore_from_capture(None, box, "round-trip-fixture")

    assert isinstance(result, dict)
    assert result["scenario_name"] == "round-trip-fixture"
    assert result["binary_version"] == "8.32"
    # The home + project skeleton from the archive should have landed.
    assert (box.root / "home").is_dir()
    assert (box.root / "project").is_dir()
    # And the meta.json must now reflect the CURRENT (not origin) box id.
    saved = json.loads((box.root / "meta.json").read_text())
    assert saved["box_id"] == box.box_id


def test_restore_repairs_dangling_venv_interpreter(monkeypatch, tmp_path):
    """A venv ``bin/python`` link targeting the capture machine's
    interpreter dangles after a cross-machine restore; the helper must
    repoint it at the current interpreter (journey steps invoking
    ``.testvenv/bin/python`` die with rc=127 otherwise). A link that
    already resolves must be left untouched."""
    captures_root = tmp_path / "captures"
    captures_root.mkdir()
    monkeypatch.setenv("OTBOX_CAPTURES_ROOT", str(captures_root))
    monkeypatch.setattr(
        "tests.otbox.checkpoints._captured_helpers.BOXES_DIR",
        tmp_path / "boxes",
    )
    monkeypatch.setattr(
        "tests.otbox.env.BOXES_DIR",
        tmp_path / "boxes",
    )

    foreign_python = str(tmp_path / "no-such-machine" / "bin" / "python3.14")
    _build_fake_artifact(
        captures_root,
        "venv-repair-fixture",
        {"scenario_name": "venv-repair-fixture", "agent": "echo"},
        venv_python_target=foreign_python,
    )

    box = Box(box_id=new_box_id())
    result = restore_from_capture(None, box, "venv-repair-fixture")
    assert isinstance(result, dict)

    venv_bin = box.root / "project" / ".testvenv" / "bin"
    dangling = venv_bin / "python"
    assert dangling.is_symlink()
    assert os.readlink(dangling) == sys.executable
    assert dangling.exists()
    # The resolving link keeps its original (non-sys.executable) target.
    assert os.readlink(venv_bin / "python3") == "/bin/ls"


# ---------------------------------------------------------------------------
# contract gate — sub-contract artifacts are rejected pre-wipe
# ---------------------------------------------------------------------------
def test_restore_from_capture_rejects_artifact_below_trace_floor(
    monkeypatch, tmp_path,
):
    """A staged 1-trace artifact must be rejected when the caller
    declares ``min_traces=3`` — BEFORE the box is wiped, so the
    synthetic chain still has its parent world to build on.
    """
    captures_root = tmp_path / "captures"
    captures_root.mkdir()
    monkeypatch.setenv("OTBOX_CAPTURES_ROOT", str(captures_root))

    _build_fake_artifact(
        captures_root, "one-trace-fixture",
        {"scenario_name": "one-trace-fixture"},
        traces=[{"trace_id": "t1", "session_id": "uuid-1"}],
    )
    assert artifact_exists("one-trace-fixture") is True

    box = Box(box_id=new_box_id())
    result = restore_from_capture(None, box, "one-trace-fixture", min_traces=3)
    assert result is None
    assert not box.root.exists()
    assert "trace_count 1 < required 3" in (
        box.notes.get("capture_artifact_rejections") or {}
    ).get("one-trace-fixture", "")


def test_restore_from_capture_rejects_unfingerprinted_security_artifact(
    monkeypatch, tmp_path,
):
    """An artifact whose captured trace carries no
    ``metadata.security.tools_applied`` fingerprints must be rejected
    when the caller requires them (c-captured-with-secrets contract).
    """
    captures_root = tmp_path / "captures"
    captures_root.mkdir()
    monkeypatch.setenv("OTBOX_CAPTURES_ROOT", str(captures_root))

    _build_fake_artifact(
        captures_root, "no-findings-fixture",
        {"scenario_name": "no-findings-fixture"},
        traces=[{
            "trace_id": "t1",
            "session_id": "uuid-1",
            "record": {"trace_id": "t1", "metadata": {"security": {"tools_applied": []}}},
        }],
    )

    box = Box(box_id=new_box_id())
    result = restore_from_capture(
        None, box, "no-findings-fixture", require_security_fingerprints=True,
    )
    assert result is None
    assert not box.root.exists()

    # The same shape WITH fingerprints passes the gate (and restores).
    _build_fake_artifact(
        captures_root, "with-findings-fixture",
        {"scenario_name": "with-findings-fixture"},
        traces=[{
            "trace_id": "t2",
            "session_id": "uuid-2",
            "record": {
                "trace_id": "t2",
                "metadata": {"security": {"tools_applied": ["regex"]}},
            },
        }],
    )
    monkeypatch.setattr(
        "tests.otbox.checkpoints._captured_helpers.BOXES_DIR",
        tmp_path / "boxes",
    )
    monkeypatch.setattr("tests.otbox.env.BOXES_DIR", tmp_path / "boxes")
    box2 = Box(box_id=new_box_id())
    result2 = restore_from_capture(
        None, box2, "with-findings-fixture", require_security_fingerprints=True,
    )
    assert isinstance(result2, dict)
    assert result2["scenario_name"] == "with-findings-fixture"


# ---------------------------------------------------------------------------
# checkpoint integration — audit stamps the provenance source marker
# ---------------------------------------------------------------------------
def test_captured_session_audit_includes_source_marker(driver):
    """The audit's ``capture_metadata.source`` must match the
    machine's artifact state: ``"artifact"`` where the plan-B0
    ``claude-linear-edit`` capture is present, ``"synthetic"`` in
    default CI (no artifacts ship in OSS).

    Plan 074 (drift detection) branches on this marker; locking it in
    means the consumer surface is ready for that work.
    """
    expect_artifact = artifact_exists("claude-linear-edit")
    cp = resolve_checkpoint(driver, "c-captured-real-session")
    try:
        audit = cp.box.notes.get("c_captured_session_audit") or {}
        cap_meta = audit.get("capture_metadata") or {}
        if expect_artifact:
            assert cap_meta.get("source") == "artifact", (
                f"artifact present but audit did not record artifact source; "
                f"capture_metadata={cap_meta}"
            )
            assert cap_meta.get("agent_binary_name") == "claude", cap_meta
            # Real-agent worlds mint UUID session ids; the audit must
            # carry the real one, not the synthetic corpus constant.
            assert audit.get("session_id"), audit
            assert audit.get("trace_id"), audit
            assert int(audit.get("edit_step_index") or 0) >= 1, audit
        else:
            assert cap_meta.get("source") == "synthetic", (
                f"audit did not record synthetic source marker; "
                f"capture_metadata={cap_meta}"
            )
            assert cap_meta == synthetic_capture_metadata()
    finally:
        if cp.box.root.exists():
            driver.teardown(cp.box)
