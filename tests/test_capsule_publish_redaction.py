"""R7 (plan 089): no publish path may emit an un-redacted capsule.

`export_capsule` redacts, but a capsule assembled directly via `freeze_capsule`
does not — that gap leaked a home path on 2026-05-31. These tests prove the
publish-time guard (`ensure_redacted`, called inside `publish_capsule`) scrubs a
planted secret regardless of how the capsule was built, and is idempotent.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

from opentraces.core.capsule.contract import freeze_capsule
from opentraces.core.capsule.redaction import REDACTION_FLOOR, ensure_redacted

# A 48-char hex token — high entropy, reliably caught by the entropy detector.
SENTINEL_SECRET = "deadbeefcafef00d0123456789abcdef0011223344556677"


def _unredacted_capsule_with_secret():
    """A capsule built straight from freeze_capsule (NO redaction pass) with a
    high-entropy secret planted in the published `test.command` field."""

    return freeze_capsule(
        capsule_id="unredactedcap001",
        source={"project_slug": "p", "trace_id": "t", "step_index": 1, "agent": "demo"},
        summary={"title": "leak", "what_happened": f"token {SENTINEL_SECRET}",
                 "failure": "x", "is_failure": True, "scope": "1"},
        test={"command": f"deploy --token {SENTINEL_SECRET}",
              "expected": {"kind": "nonzero_exit"}, "cwd": "", "source": "declared"},
        environment={"dependencies": [], "language_ecosystem": "python", "setup": []},
        bundle=None,
        intent={"headline": "x", "most_substantive_spec": None, "trigger": None},
        failing_step={"index": 1, "type": "agent", "error_excerpt": "x", "had_error_marker": True},
        slice_payload={"slice_id": "s", "trace_id": "t", "steps": [], "limitations": []},
        context_resume_packet={"schema_version": "opentraces.context_resume.v1", "node_id": "n",
                               "system_layer": {"content": {}}},
        trail_anchors=[],
        repo_pin={"remote_url": None, "commit_sha": "abc", "changed_files": []},
        redaction={},  # <-- never redacted; manifest absent (floor NOT satisfied)
        render_state={"redaction": "unredacted", "closure": "closure_full", "replay": "x"},
        limitations=[],
        created_with="r7 test",
    )


def test_ensure_redacted_scrubs_and_is_idempotent():
    cap = _unredacted_capsule_with_secret()
    assert SENTINEL_SECRET in json.dumps(cap)  # planted, present pre-redaction

    red = ensure_redacted(cap)
    assert SENTINEL_SECRET not in json.dumps(red)
    assert red["redaction"]["manifest"]["floor_satisfied"] is True
    assert red["redaction"]["manifest"]["redactions_applied"] >= 1

    # Idempotent: re-running finds nothing new and stays clean.
    red2 = ensure_redacted(red)
    assert SENTINEL_SECRET not in json.dumps(red2)
    assert red2["redaction"]["manifest"]["floor_satisfied"] is True


def test_publish_capsule_cannot_emit_unredacted(monkeypatch):
    """The whole publish path scrubs the secret before any byte is uploaded."""

    captured: dict[str, str] = {}

    class _FakeApi:
        def __init__(self, token=None):
            pass

        def create_repo(self, **kw):
            return None

        def upload_folder(self, *, repo_id, repo_type, folder_path, path_in_repo, commit_message):
            captured["capsule_json"] = (Path(folder_path) / "capsule.json").read_text(encoding="utf-8")
            return types.SimpleNamespace(oid="deadbeefrevision")

        def upload_file(self, *, path_or_fileobj, path_in_repo, repo_id, repo_type=None,
                        commit_message=None, **kw):
            data = path_or_fileobj
            if isinstance(data, (bytes, bytearray)):
                data = bytes(data).decode("utf-8")
            elif not isinstance(data, str):
                data = Path(data).read_text(encoding="utf-8")
            captured["pinned_path"] = path_in_repo
            captured["pinned_json"] = data

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", _FakeApi)

    from opentraces.core.capsule.share import publish_capsule

    info = publish_capsule(
        _unredacted_capsule_with_secret(),
        repo_id="someone/opentraces-capsules", token="fake-token",
    )

    assert "capsule_json" in captured, "upload_folder was not called"
    body = captured["capsule_json"]
    assert SENTINEL_SECRET not in body, "publish emitted an un-redacted secret!"
    manifest = json.loads(body)["redaction"]["manifest"]
    assert manifest["floor_satisfied"] is True
    assert list(REDACTION_FLOOR) == manifest["floor"]
    assert info["revision"] == "deadbeefrevision"


def test_published_capsule_json_is_sha_pinned(monkeypatch):
    """Plan 090 U0: the PUBLISHED capsule.json (re-uploaded after the folder commit)
    carries the immutable commit sha in its share block — not "main"/None — so a
    third party holding only the capsule.json resolves to a stable revision."""

    captured: dict[str, str] = {}

    class _FakeApi:
        def __init__(self, token=None):
            pass

        def create_repo(self, **kw):
            return None

        def upload_folder(self, *, repo_id, repo_type, folder_path, path_in_repo, commit_message):
            captured["folder_json"] = (Path(folder_path) / "capsule.json").read_text(encoding="utf-8")
            return types.SimpleNamespace(oid="deadbeefrevision")

        def upload_file(self, *, path_or_fileobj, path_in_repo, repo_id, repo_type=None,
                        commit_message=None, **kw):
            data = path_or_fileobj
            if isinstance(data, (bytes, bytearray)):
                data = bytes(data).decode("utf-8")
            elif not isinstance(data, str):
                data = Path(data).read_text(encoding="utf-8")
            captured["pinned_path"] = path_in_repo
            captured["pinned_json"] = data

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", _FakeApi)

    from opentraces.core.capsule.share import publish_capsule

    info = publish_capsule(
        _unredacted_capsule_with_secret(),
        repo_id="someone/opentraces-capsules", token="fake-token",
    )

    # The folder upload (oid not yet known) still carries the stale main/None share.
    folder_share = json.loads(captured["folder_json"])["share"]
    assert folder_share["published_revision"] is None

    # The re-uploaded capsule.json IS sha-pinned and is the final on-hub state.
    assert captured.get("pinned_path") == "capsules/v1/unredactedcap001/capsule.json"
    pinned = json.loads(captured["pinned_json"])
    assert pinned["share"]["revision"] == "deadbeefrevision"
    assert pinned["share"]["published_revision"] == "deadbeefrevision"
    assert "deadbeefrevision" in pinned["share"]["capsule_url"]
    assert SENTINEL_SECRET not in captured["pinned_json"]
    assert info["published_revision"] == "deadbeefrevision"
