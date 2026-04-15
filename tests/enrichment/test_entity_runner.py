"""Unit tests for EntityRunner against the fixture binary."""
from __future__ import annotations

from pathlib import Path

import pytest

from opentraces.enrichment.entities import EntityRunner
from opentraces.enrichment.entities.runner import resolve_binary_path

FIXTURE = (
    Path(__file__).parent / "fixtures" / "ot-entities" / "ot-entities"
).resolve()


def test_resolve_binary_path_prefers_explicit(tmp_path):
    p = tmp_path / "x"
    assert resolve_binary_path(p) == p


def test_resolve_binary_path_respects_env(monkeypatch, tmp_path):
    p = tmp_path / "envbin"
    monkeypatch.setenv("OPENTRACES_ENTITY_BIN", str(p))
    assert resolve_binary_path() == p


def test_available_false_when_missing(tmp_path):
    r = EntityRunner(binary_path=tmp_path / "nope")
    assert r.available() is False
    assert r.version() is None


def test_available_true_with_fixture():
    if not FIXTURE.is_file():
        pytest.skip("fixture binary not found")
    r = EntityRunner(binary_path=FIXTURE)
    assert r.available() is True
    v = r.version()
    assert v is not None
    assert "0.3.19" in v


def test_diff_commit_known_sha_returns_entities(tmp_path):
    if not FIXTURE.is_file():
        pytest.skip("fixture binary not found")
    r = EntityRunner(binary_path=FIXTURE)
    result = r.diff_commit(tmp_path, "cafebabe")
    assert isinstance(result, dict)
    assert "entities" in result
    assert isinstance(result["entities"], list)


def test_diff_commit_unknown_sha_returns_empty(tmp_path):
    if not FIXTURE.is_file():
        pytest.skip("fixture binary not found")
    r = EntityRunner(binary_path=FIXTURE)
    result = r.diff_commit(tmp_path, "0" * 40)
    assert result == {"entities": []}


def test_diff_commit_missing_binary_returns_empty(tmp_path):
    r = EntityRunner(binary_path=tmp_path / "nope")
    assert r.diff_commit(tmp_path, "abc") == {"entities": []}
