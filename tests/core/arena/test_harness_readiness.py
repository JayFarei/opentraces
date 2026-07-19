from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from opentraces.core.arena.harness_readiness import (
    HarnessPreferencesInvalid,
    ensure_claude_readiness,
    main,
)


WORKSPACE = "/work/crabbox/cbx_exact/opentraces"
VERSION = "2.1.210"


def test_fresh_preferences_establish_exact_pinned_workspace_readiness(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"

    changed = ensure_claude_readiness(path=path, workspace=WORKSPACE, version=VERSION)

    assert changed is True
    assert json.loads(path.read_text()) == {
        "bypassPermissionsModeAccepted": True,
        "hasCompletedOnboarding": True,
        "lastOnboardingVersion": VERSION,
        "projects": {WORKSPACE: {"hasTrustDialogAccepted": True}},
        "theme": "dark",
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_readiness_is_idempotent_and_preserves_unrelated_custody(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    path.write_text(json.dumps({"oauthAccount": {"opaque": "preserve-me"}}))
    assert ensure_claude_readiness(path=path, workspace=WORKSPACE, version=VERSION) is True
    first = path.read_bytes()
    first_mtime = path.stat().st_mtime_ns

    assert ensure_claude_readiness(path=path, workspace=WORKSPACE, version=VERSION) is False

    assert path.read_bytes() == first
    assert path.stat().st_mtime_ns == first_mtime
    assert json.loads(first)["oauthAccount"] == {"opaque": "preserve-me"}


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b'{"hasCompletedOnboarding": "yes"}',
        b'{"projects": []}',
        (json.dumps({"projects": {WORKSPACE: []}}).encode()),
    ],
)
def test_corrupt_preferences_fail_honestly_without_mutation(
    tmp_path: Path, payload: bytes
) -> None:
    path = tmp_path / ".claude.json"
    path.write_bytes(payload)

    with pytest.raises(HarnessPreferencesInvalid):
        ensure_claude_readiness(path=path, workspace=WORKSPACE, version=VERSION)

    assert path.read_bytes() == payload


def test_existing_ready_file_permission_is_repaired_without_rewrite(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    ensure_claude_readiness(path=path, workspace=WORKSPACE, version=VERSION)
    content = path.read_bytes()
    os.chmod(path, 0o644)

    assert ensure_claude_readiness(path=path, workspace=WORKSPACE, version=VERSION) is False

    assert path.read_bytes() == content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_check_rejects_corrupt_preferences_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".claude.json"
    payload = b'{"projects": []}'
    path.write_bytes(payload)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert main(["--check", "--workspace", WORKSPACE, "--version", VERSION]) == 2
    assert path.read_bytes() == payload


def test_check_accepts_absent_and_valid_preferences_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".claude.json"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert main(["--check", "--workspace", WORKSPACE, "--version", VERSION]) == 0
    assert not path.exists()

    payload = b'{"oauthAccount":{"opaque":"preserve-me"}}'
    path.write_bytes(payload)
    before_mtime = path.stat().st_mtime_ns
    assert main(["--check", "--workspace", WORKSPACE, "--version", VERSION]) == 0
    assert path.read_bytes() == payload
    assert path.stat().st_mtime_ns == before_mtime
