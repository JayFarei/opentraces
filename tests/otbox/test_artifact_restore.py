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
  * ``test_captured_session_audit_includes_source_marker`` resolves
    the synthetic-path checkpoint (no real artifact ships in OSS)
    and asserts the ``capture_metadata.source == "synthetic"``
    marker landed in the audit.

Plan 073 will produce real artifacts via the Mac Mini runner; until
then the synthetic path is the load-bearing one in default CI.
"""

from __future__ import annotations

import json
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

    assert artifact_exists("c-captured-real-session") is False
    fake_box = Box(box_id=new_box_id())
    result = restore_from_capture(None, fake_box, "c-captured-real-session")
    assert result is None
    # Box root should not have been created on a None return.
    assert not fake_box.root.exists()


# ---------------------------------------------------------------------------
# round-trip contract — synthetic artifact built in tmp_path
# ---------------------------------------------------------------------------
def _build_fake_artifact(captures_root, capture_name: str, metadata: dict) -> None:
    """Stage a minimum-shape artifact under ``captures_root/<capture_name>/``.

    The artifact body is the smallest dirtree that lets
    ``restore_from_capture`` succeed: an empty ``home/`` /
    ``project/`` / ``fake-remote/`` skeleton + a ``meta.json`` at the
    box root (so the path-rewriter knows the origin box id).
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


# ---------------------------------------------------------------------------
# checkpoint integration — synthetic path stamps the source marker
# ---------------------------------------------------------------------------
def test_captured_session_audit_includes_source_marker(driver):
    """In default CI no real artifact ships → synthetic path runs →
    audit must carry ``capture_metadata.source == "synthetic"``.

    Plan 074 (drift detection) will branch on this marker; locking it
    in now means the consumer surface is ready for that work.
    """
    cp = resolve_checkpoint(driver, "c-captured-real-session")
    try:
        audit = cp.box.notes.get("c_captured_session_audit") or {}
        cap_meta = audit.get("capture_metadata") or {}
        # The synthetic path is the only one default CI exercises.
        assert cap_meta.get("source") == "synthetic", (
            f"audit did not record synthetic source marker; "
            f"capture_metadata={cap_meta}"
        )
        assert cap_meta == synthetic_capture_metadata()
    finally:
        if cp.box.root.exists():
            driver.teardown(cp.box)
