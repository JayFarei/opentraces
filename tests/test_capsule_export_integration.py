"""End-to-end export -> open integration, skip-guarded on local bucket data.

Picks the first bucket trace that has a captured context node (so closure can be
full), exports a capsule, and round-trips it through the consume path. Skips
cleanly when no suitable trace is present (CI without a seeded bucket).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opentraces.core.bucket_store import iter_trace_record_objects
from opentraces.core.capsule.contract import validate_capsule
from opentraces.core.capsule.export import export_capsule
from opentraces.core.capsule.share import resolve_capsule, write_capsule_dir


def _find_trace_with_context():
    for obj in iter_trace_record_objects():
        steps = getattr(obj.record, "steps", []) or []
        if steps and any(getattr(s, "context_node_id", None) for s in steps):
            # Resolve the project dir from the slug's marker dir name.
            return obj
    return None


@pytest.fixture(scope="module")
def demo_trace():
    obj = _find_trace_with_context()
    if obj is None:
        pytest.skip("no bucket trace with a context node available")
    return obj


def _project_dir_for(obj) -> Path:
    # The bucket trace records the repository the session ran in.
    repo = getattr(getattr(obj.record, "task", None), "repository", None)
    if repo and Path(repo).exists():
        return Path(repo)
    pytest.skip(f"project dir for {obj.trace_id} not resolvable on this machine")


def test_export_produces_valid_capsule_and_round_trips(demo_trace, tmp_path):
    project_dir = _project_dir_for(demo_trace)
    capsule = export_capsule(project_dir=project_dir, trace_id=demo_trace.trace_id)

    # Frozen contract holds.
    validate_capsule(capsule)
    assert capsule["intent"]["headline"], "capsule must carry captured intent"
    assert capsule["redaction"]["manifest"]["floor_satisfied"] is True

    # Self-sufficient round-trip: write to a clean dir and resolve from the file
    # alone (no access to the originating repo/bucket).
    arts = write_capsule_dir(capsule, tmp_path)
    reopened = resolve_capsule(str(arts["json"]))
    assert reopened["capsule_id"] == capsule["capsule_id"]

    # Secret-leak guard on a real capsule: the manifest must never carry raw matches.
    assert "matched_text" not in json.dumps(capsule["redaction"]["manifest"])


def test_export_refuses_unknown_trace(demo_trace):
    project_dir = _project_dir_for(demo_trace)
    from opentraces.core.capsule.export import CapsuleExportError

    with pytest.raises(CapsuleExportError):
        export_capsule(project_dir=project_dir, trace_id="00000000-0000-0000-0000-000000000000")
