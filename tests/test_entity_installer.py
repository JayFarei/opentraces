"""Unit tests for the entity-parser installer.

Never hits the real network — every download is mocked.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from opentraces.enrichment.entities import installer as _inst
from opentraces.enrichment.entities.version import ENTITY_BINARY_VERSION


def test_current_platform_supported(monkeypatch):
    monkeypatch.setattr(_inst._platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_inst._platform, "machine", lambda: "arm64")
    assert _inst.current_platform() == "darwin-arm64"

    monkeypatch.setattr(_inst._platform, "system", lambda: "Linux")
    monkeypatch.setattr(_inst._platform, "machine", lambda: "x86_64")
    assert _inst.current_platform() == "linux-x86_64"


def test_current_platform_unsupported_raises(monkeypatch):
    monkeypatch.setattr(_inst._platform, "system", lambda: "Plan9")
    monkeypatch.setattr(_inst._platform, "machine", lambda: "mips")
    with pytest.raises(_inst.InstallError):
        _inst.current_platform()


def test_expected_sha256_reads_manifest():
    sha = _inst.expected_sha256("darwin-arm64")
    assert isinstance(sha, str)
    assert len(sha) == 64


def test_env_override_skips_download(tmp_path, monkeypatch):
    """With OPENTRACES_ENTITY_BIN set, we just verify the provided file."""
    fake = tmp_path / "fakebin"
    fake.write_text("#!/bin/sh\necho 'ot-entities 0.3.19-fake'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("OPENTRACES_ENTITY_BIN", str(fake))

    result = _inst.install(dest_dir=tmp_path / "dest")
    assert result.source == "env-override"
    assert result.path == fake


def test_env_override_rejects_broken_binary(tmp_path, monkeypatch):
    fake = tmp_path / "broken"
    fake.write_text("not-executable")
    # no chmod +x
    monkeypatch.setenv("OPENTRACES_ENTITY_BIN", str(fake))
    with pytest.raises(_inst.InstallError):
        _inst.install(dest_dir=tmp_path / "dest")


def _fake_binary_bytes() -> bytes:
    # A tiny shell script that can answer --version.
    return b"#!/bin/sh\necho 'ot-entities 0.3.19-mock'\n"


def test_install_happy_path(tmp_path, monkeypatch):
    """Download succeeds, sha256 matches manifest, binary is installed+verified."""
    monkeypatch.delenv("OPENTRACES_ENTITY_BIN", raising=False)
    payload = _fake_binary_bytes()
    real_sha = hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(_inst, "current_platform", lambda: "linux-x86_64")
    monkeypatch.setattr(_inst, "expected_sha256", lambda p: real_sha)
    monkeypatch.setattr(_inst, "_source_url", lambda p: "https://example/fake")

    def fake_download(url, dest, *, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)

    monkeypatch.setattr(_inst, "_download", fake_download)

    result = _inst.install(dest_dir=tmp_path)
    assert result.source == "download"
    assert result.path.exists()
    assert result.path.name == f"ot-entities-{ENTITY_BINARY_VERSION}"
    # chmod +x applied
    assert os.access(result.path, os.X_OK)


def test_install_rejects_sha256_mismatch(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENTRACES_ENTITY_BIN", raising=False)
    monkeypatch.setattr(_inst, "current_platform", lambda: "linux-x86_64")
    monkeypatch.setattr(
        _inst, "expected_sha256",
        lambda p: "a" * 64,  # will not match
    )
    monkeypatch.setattr(_inst, "_source_url", lambda p: "https://example/fake")

    def fake_download(url, dest, *, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"wrong-payload")

    monkeypatch.setattr(_inst, "_download", fake_download)

    with pytest.raises(_inst.InstallError, match="sha256 mismatch"):
        _inst.install(dest_dir=tmp_path)

    # No artifact left behind
    assert list(tmp_path.glob("ot-entities-*")) == []


def test_install_placeholder_sha_accepted(tmp_path, monkeypatch):
    """Manifest placeholder ('0'*64) is treated as 'skip verification'."""
    monkeypatch.delenv("OPENTRACES_ENTITY_BIN", raising=False)
    payload = _fake_binary_bytes()
    monkeypatch.setattr(_inst, "current_platform", lambda: "linux-x86_64")
    monkeypatch.setattr(_inst, "expected_sha256", lambda p: "0" * 64)
    monkeypatch.setattr(_inst, "_source_url", lambda p: "https://example/fake")

    def fake_download(url, dest, *, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)

    monkeypatch.setattr(_inst, "_download", fake_download)

    result = _inst.install(dest_dir=tmp_path)
    assert result.source == "download"


def test_prune_old_versions(tmp_path):
    # Create 5 "versions", expect the oldest 2 to be pruned.
    for i in range(5):
        p = tmp_path / f"ot-entities-0.3.{i}"
        p.write_text("x")
        # stagger mtimes
        os.utime(p, (i, i))
    pruned = _inst._prune_old_versions(tmp_path, keep=3)
    assert len(pruned) == 2
    remaining = sorted(p.name for p in tmp_path.glob("ot-entities-*"))
    assert remaining == [
        "ot-entities-0.3.2",
        "ot-entities-0.3.3",
        "ot-entities-0.3.4",
    ]
