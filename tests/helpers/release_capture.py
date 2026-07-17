"""Shared verifier for the ``otbox-captures-v1`` release-derived capture fixture.

Single source of truth for the release-asset provenance chain that A3 (#265)
and A4 (#270) previously duplicated (each carrying an explicit
``TODO(#265,#270)`` to unify after both branches merged). Both call sites now
delegate here so the frozen release-asset coordinates live in exactly one place.

Also derives an authentic, sanitized capture record whose ``Step.context_node_id``
values are non-null (#298). The record is produced by the genuine Context Tree
capture path over the same committed real session, not hand-authored, so the
join key is a real content-addressed node id rather than a synthetic placeholder.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude"
REAL_CAPTURE_FIXTURE = _FIXTURES / "claude-linear-edit-real-session.jsonl"
REAL_CAPTURE_PROVENANCE = REAL_CAPTURE_FIXTURE.with_suffix(".provenance.json")

# Frozen ``otbox-captures-v1`` release-asset coordinates. Drift in any of these
# is a provenance break (the committed fixture stopped tracing back to the
# independently verified real-agent release asset), not a test-maintenance chore.
_EXPECTED_PROVENANCE: dict[str, Any] = {
    "source_release": "otbox-captures-v1",
    "source_snapshot": "claude-linear-edit.snapshot.tar.gz",
    "source_snapshot_size_bytes": 68_916_309,
    "source_snapshot_sha256": (
        "54466705324a1f44d510160fb3fa31213ef8584704afc6b443487441ca1bf03b"
    ),
    "source_session_sha256": (
        "a745ceee16159433d93e8b8cc54c2e2c101c657630644bb1b10902c95b42cde0"
    ),
    "source_metadata_sha256": (
        "2287ae5f223f5b135d33d3b0baad645449d0713d9801a7e0500eec07b3b3a120"
    ),
}


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_release_capture_provenance() -> dict[str, Any]:
    return json.loads(REAL_CAPTURE_PROVENANCE.read_text())


def verify_release_asset_provenance(
    provenance: dict[str, Any] | None = None,
    *,
    fixture: Path = REAL_CAPTURE_FIXTURE,
) -> dict[str, Any]:
    """Assert the committed real-session fixture still carries the independently
    verified ``otbox-captures-v1`` release-asset provenance chain.

    Verifies, in one place: the release name, the source snapshot name / size /
    digest, the source session + metadata digests, and that the committed
    derived fixture's bytes still hash to the recorded ``derived_fixture_sha256``.

    Returns the provenance dict so callers can continue asserting
    derivation-specific detail (sanitization chain, session path, ...).
    """

    if provenance is None:
        provenance = load_release_capture_provenance()
    for key, expected in _EXPECTED_PROVENANCE.items():
        assert provenance.get(key) == expected, (key, provenance.get(key), expected)
    assert provenance.get("derived_fixture_sha256") == sha256_path(fixture), (
        "derived_fixture_sha256"
    )
    return provenance


def context_stamped_capture_record(trace_id: str = "claude-linear-edit-real-session"):
    """Return an authentic capture ``TraceRecord`` with non-null Context joins.

    The committed real session (``otbox-captures-v1``) is parsed and then run
    through the genuine Context Tree capture path (the same
    ``emit_context_tree_events_from_record`` the ingest pipeline calls), and its
    steps are stamped with ``context_node_id`` using the identical
    ``step_node_id_map`` join the ingest pipeline uses. The node ids are real
    content-addressed hashes derived from the captured session content, so this
    is a *derived* authentic fixture, not a synthetic one. Derivation is
    deterministic because it reads the committed transcript's fixed content.
    """

    from opentraces.capture.claude_code.context_tree_capture import (
        emit_context_tree_events_from_record,
    )
    from opentraces.capture.claude_code.parse import ClaudeCodeParser

    record = ClaudeCodeParser().parse_session(REAL_CAPTURE_FIXTURE)
    assert record is not None
    record.trace_id = trace_id

    # ``emit_context_tree_events_from_record`` appends context events to a Git
    # event log under ``project_dir``; that write location does not enter the
    # content-addressed layer/node ids (those derive from the transcript's fixed
    # content), so a throwaway git repo keeps the derived join deterministic.
    with tempfile.TemporaryDirectory() as tmp:
        project_dir = Path(tmp)
        subprocess.run(
            ["git", "init", "--quiet"], cwd=project_dir, check=True
        )
        summary = emit_context_tree_events_from_record(
            project_dir=project_dir,
            final_record=record,
            transcript_path=REAL_CAPTURE_FIXTURE,
        )

    step_map = (summary or {}).get("step_node_id_map") or {}
    for step in record.steps:
        node_id = step_map.get(step.step_index)
        if node_id is not None:
            step.context_node_id = node_id
    record.context_tree_summary = {
        k: v for k, v in (summary or {}).items() if k != "step_node_id_map"
    }
    return record
