"""Characterization of authoritative_trace_materialization_ref (#329).

This locks the resolver's current observable behavior before it is extracted
out of generic label persistence: a registered project rebuilds authoritative
Trail state, an unregistered project keeps record-only materialization, a
broken registration fails closed, and a Trail-reconstruction failure fails
closed with the frozen message. The extraction must preserve every branch
byte-for-byte in behavior.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from opentraces_schema import Agent, Outcome, TraceRecord

from opentraces.core import paths
from opentraces.core.arena.labels import (
    LabelIntegrityError,
    authoritative_trace_materialization_ref,
)
from opentraces.core.trace_slices import TraceMaterializationRef


TRACE_ID = "trace-materialization-329"
PROJECT_SLUG = "project-materialization"


def _record() -> TraceRecord:
    return TraceRecord(
        trace_id=TRACE_ID,
        session_id="session-materialization",
        agent=Agent(name="test-agent"),
        task={"description": "Rebuild the authoritative Trace Map."},
        steps=[],
        outcome=Outcome(success=None),
    )


def _projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setattr(paths, "PROJECTS_DIR", root)
    return root


def _register(root: Path, *, source_path: str | None, slug: str = PROJECT_SLUG) -> Path:
    home = root / slug
    home.mkdir(parents=True)
    payload: dict[str, object] = {"root_commit_sha": "0" * 40}
    if source_path is not None:
        payload["path"] = source_path
    (home / "project.json").write_text(json.dumps(payload), encoding="utf-8")
    return home


def test_unregistered_project_uses_record_only_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _projects_root(tmp_path, monkeypatch)
    record = _record()

    ref = authoritative_trace_materialization_ref(PROJECT_SLUG, record)

    assert isinstance(ref, TraceMaterializationRef)
    expected = TraceMaterializationRef.from_record(record)
    assert ref.trace_map.model_dump(mode="json") == expected.trace_map.model_dump(mode="json")


def test_registration_without_source_repository_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _projects_root(tmp_path, monkeypatch)
    _register(root, source_path=None)

    with pytest.raises(LabelIntegrityError, match="has no source repository"):
        authoritative_trace_materialization_ref(PROJECT_SLUG, _record())


def test_registration_with_unavailable_source_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _projects_root(tmp_path, monkeypatch)
    _register(root, source_path=str(tmp_path / "does-not-exist"))

    with pytest.raises(LabelIntegrityError, match="source repository is unavailable"):
        authoritative_trace_materialization_ref(PROJECT_SLUG, _record())


def test_registered_source_with_failing_trail_reconstruction_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _projects_root(tmp_path, monkeypatch)
    source_repo = tmp_path / "world-repo"
    source_repo.mkdir()
    _register(root, source_path=str(source_repo))

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("trail world-state is corrupt")

    import opentraces.core.trails as trails_module

    monkeypatch.setattr(trails_module, "build_trail_query_projection_for_trace", _boom)

    with pytest.raises(
        LabelIntegrityError,
        match="authoritative current Trace Map could not be rebuilt from Trail world-state",
    ):
        authoritative_trace_materialization_ref(PROJECT_SLUG, _record())


def test_registered_project_rebuilds_from_trail_world_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _projects_root(tmp_path, monkeypatch)
    source_repo = tmp_path / "world-repo"
    source_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source_repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "arena@example.invalid"], cwd=source_repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Arena"], cwd=source_repo, check=True)
    (source_repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=source_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=source_repo, check=True)
    _register(root, source_path=str(source_repo))

    record = _record()
    ref = authoritative_trace_materialization_ref(PROJECT_SLUG, record)

    assert isinstance(ref, TraceMaterializationRef)
    assert ref.record.trace_id == TRACE_ID
