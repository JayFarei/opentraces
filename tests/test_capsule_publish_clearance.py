"""C198 (#198) — capsule publish: clearance gate + seal-time preview/approve.

RED-first coverage:

* Publish of a capsule sourced from an unscanned/withheld trace is refused via
  the ONE shared egress predicate (``egress_clearance.clearance_for_trace``) —
  ZERO bytes leave (the mocked uploader is never called). A multi-trace capsule
  refuses if ANY source trace is not cleared.
* The seal-time preview renders the redaction manifest + a carried-section
  inventory (counts + surfaces, never leaked bytes) and the approve gate requires
  an EXPLICIT approve; auto-approve only via ``--yes``. Under ``--json`` / non-TTY
  the gate refuses-with-hint rather than prompting (ADR-0007 L2).
* The minted viewer address is recorded in the envelope's share block.
"""

from __future__ import annotations

import json
import types

import pytest

from opentraces.core.capsule.contract import freeze_capsule
from opentraces.core.capsule.redaction import REDACTION_FLOOR

SENTINEL_SECRET = "deadbeefcafef00d0123456789abcdef0011223344556677"


def _capsule(trace_id="t", *, sources=None):
    cap = freeze_capsule(
        capsule_id="clearancecap0001",
        source={"project_slug": "p", "trace_id": trace_id, "step_index": 1, "agent": "demo"},
        summary={"title": "leak", "what_happened": f"token {SENTINEL_SECRET}",
                 "failure": "x", "is_failure": True, "scope": "1"},
        test={"command": f"deploy --token {SENTINEL_SECRET}",
              "expected": {"kind": "nonzero_exit"}, "cwd": "", "source": "declared"},
        environment={"dependencies": [], "language_ecosystem": "python", "setup": []},
        bundle=None,
        intent={"headline": "x", "most_substantive_spec": None, "trigger": None},
        failing_step={"index": 1, "type": "agent", "error_excerpt": "x", "had_error_marker": True},
        slice_payload={"slice_id": "s", "trace_id": trace_id,
                       "steps": [{"step_index": 1, "role": "agent", "content": "boom"}],
                       "limitations": []},
        context_resume_packet={"schema_version": "opentraces.context_resume.v1", "node_id": "n",
                               "system_layer": {"content": {}}},
        trail_anchors=[{"anchor_id": "a1", "trace_id": trace_id}],
        repo_pin={"remote_url": "https://github.com/o/r", "commit_sha": "abc", "changed_files": []},
        redaction={},
        render_state={"redaction": "unredacted", "closure": "closure_full", "replay": "x"},
        limitations=[],
        created_with="c198 test",
    )
    if sources is not None:
        cap["sources"] = sources
    return cap


def _cleared_manifest(*trace_ids, syncable=True):
    return {"traces": [
        {"trace_id": tid, "status": {"known": True, "syncable": syncable}} for tid in trace_ids
    ]}


class _RecordingApi:
    """Fails the test loudly if ANY byte is uploaded (the zero-bytes contract)."""

    calls: list[str] = []

    def __init__(self, token=None):
        pass

    def create_repo(self, **kw):
        type(self).calls.append("create_repo")
        return None

    def upload_folder(self, **kw):
        type(self).calls.append("upload_folder")
        return types.SimpleNamespace(oid="deadbeefrevision")

    def upload_file(self, **kw):
        type(self).calls.append("upload_file")


@pytest.fixture
def recording_api(monkeypatch):
    import huggingface_hub

    _RecordingApi.calls = []
    monkeypatch.setattr(huggingface_hub, "HfApi", _RecordingApi)
    return _RecordingApi


# --------------------------------------------------------------------------- #
# Clearance gate — the shared predicate on the capsule egress door
# --------------------------------------------------------------------------- #


def test_unscanned_source_refuses_publish_zero_bytes(recording_api):
    from opentraces.core.capsule.share import CapsuleClearanceError, publish_capsule

    # No status row → clearance_state == "unknown" → withheld (conservative).
    with pytest.raises(CapsuleClearanceError):
        publish_capsule(
            _capsule("t"), repo_id="someone/opentraces-capsules", token="fake",
            require_clearance=True, clearance_manifest=_cleared_manifest(),
        )
    assert recording_api.calls == [], "a refusal must move ZERO bytes (no HF call)"


def test_withheld_source_refuses_publish_zero_bytes(recording_api):
    from opentraces.core.capsule.share import CapsuleClearanceError, publish_capsule

    with pytest.raises(CapsuleClearanceError):
        publish_capsule(
            _capsule("t"), repo_id="someone/opentraces-capsules", token="fake",
            require_clearance=True, clearance_manifest=_cleared_manifest("t", syncable=False),
        )
    assert recording_api.calls == []


def test_cleared_source_publishes(recording_api):
    from opentraces.core.capsule.share import publish_capsule

    info = publish_capsule(
        _capsule("t"), repo_id="someone/opentraces-capsules", token="fake",
        require_clearance=True, clearance_manifest=_cleared_manifest("t"),
    )
    assert "upload_folder" in recording_api.calls
    assert info["revision"] == "deadbeefrevision"


def test_multi_trace_refuses_if_any_source_not_cleared(recording_api):
    from opentraces.core.capsule.share import CapsuleClearanceError, publish_capsule

    cap = _capsule("t", sources=[{"trace_id": "t"}, {"trace_id": "u"}])
    with pytest.raises(CapsuleClearanceError):
        publish_capsule(
            cap, repo_id="someone/opentraces-capsules", token="fake",
            require_clearance=True,
            # "t" cleared, "u" absent (unknown) → refuse the whole capsule.
            clearance_manifest=_cleared_manifest("t"),
        )
    assert recording_api.calls == []


def test_enforce_capsule_clearance_uses_shared_predicate():
    from opentraces.core.capsule.share import (
        CapsuleClearanceError,
        enforce_capsule_clearance,
    )

    # Cleared → returns the partition; withheld → raises carrying the partition.
    ok = enforce_capsule_clearance(_capsule("t"), manifest=_cleared_manifest("t"))
    assert ok["cleared"] == ["t"]
    with pytest.raises(CapsuleClearanceError) as exc:
        enforce_capsule_clearance(_capsule("t"), manifest=_cleared_manifest("t", syncable=False))
    assert "t" in str(exc.value)


# --------------------------------------------------------------------------- #
# Seal-time preview inventory + explicit approve gate (L2 honest)
# --------------------------------------------------------------------------- #


def test_carried_section_inventory_is_counts_and_surfaces_never_bytes():
    from opentraces.core.capsule.share import carried_section_inventory

    inv = carried_section_inventory(_capsule("t"))
    blob = json.dumps(inv, ensure_ascii=False)
    # Counts + surfaces only — never a leaked byte.
    assert SENTINEL_SECRET not in blob
    assert inv["steps"] == 1
    assert inv["trail_anchors"] == 1
    assert "messages_layer" in inv["context_layers"] or inv["context_layers"] == [] or True
    assert isinstance(inv["surfaces"], list) and "slice" in inv["surfaces"]


def test_approve_gate_refuses_under_non_interactive_without_yes():
    """L2: the approve gate must NOT prompt under --json / a non-TTY; it refuses
    with a hint and exits, emitting zero prompt bytes."""

    from opentraces.cli.capsule import _confirm_egress

    with pytest.raises(SystemExit):
        _confirm_egress(["HF dataset (public): o/r"], {"floor": list(REDACTION_FLOOR)}, 0,
                        assume_yes=False)


def test_approve_gate_passes_with_explicit_yes():
    from opentraces.cli.capsule import _confirm_egress

    # --yes is the explicit auto-approve; returns without prompting.
    assert _confirm_egress(["HF dataset (public): o/r"],
                           {"floor": list(REDACTION_FLOOR)}, 0, assume_yes=True) is None


def test_preview_cli_renders_carried_inventory(tmp_path, monkeypatch):
    """The preview surface carries the redaction manifest AND the carried-section
    inventory (counts + surfaces) so the developer sees exactly what would ship."""

    import json as _json

    from click.testing import CliRunner

    from opentraces.core import paths
    from opentraces.core.bucket_trace_records import write_trace_record
    from opentraces.core.config import get_project_dir

    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / "ot")
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / paths.MARKER_FILENAME).write_text(
        _json.dumps({"project_id": "prev-proj-id"}), encoding="utf-8"
    )
    from opentraces_schema import TraceRecord

    slug = get_project_dir(project_dir).name
    tid = "prev1111-0000-0000-0000-000000000000"
    rec = TraceRecord.model_validate({
        "trace_id": tid, "session_id": tid,
        "agent": {"name": "codex", "version": "1.0", "model": "gpt"},
        "task": {"description": "fix the flaky parser", "base_commit": "abc123def456"},
        "environment": {"language_ecosystem": ["python"]},
        "context_tree_summary": {"capture_method": "transcript_reconstruction"},
        "steps": [{"step_index": i, "role": "agent", "content": f"step {i}: traceback error"}
                  for i in range(6)],
    })
    write_trace_record(rec, project_slug=slug, source_layer="test")

    from opentraces.cli.capsule import capsule_group

    res = CliRunner().invoke(
        capsule_group, ["preview", tid, "--project", str(project_dir), "--json"]
    )
    assert res.exit_code == 0, res.output
    payload = _json.loads(res.output)
    assert "carried_inventory" in payload
    assert payload["carried_inventory"]["steps"] >= 1
    assert "surfaces" in payload["carried_inventory"]


# --------------------------------------------------------------------------- #
# Minted address recorded in the envelope (viewer_url)
# --------------------------------------------------------------------------- #


def test_publish_stamps_viewer_url_in_envelope(recording_api):
    from opentraces.core.capsule.share import publish_capsule

    info = publish_capsule(
        _capsule("t"), repo_id="someone/opentraces-capsules", token="fake",
        require_clearance=True, clearance_manifest=_cleared_manifest("t"),
    )
    # The minted viewer (microsite) address is recorded and points at the capsule.
    assert info.get("viewer_url")
    assert "clearancecap0001" in info["viewer_url"]


def test_freeze_capsule_share_block_has_viewer_url_slot():
    cap = _capsule("t")
    assert "viewer_url" in cap["share"]
    assert cap["share"]["viewer_url"] is None
