"""End-to-end test for the ``skill-verifier-candidates-v1`` workflow template.

Runs the bundled template through the real workflow runner (subprocess
``build_rows.py`` reading ``OT_RUN_PACKET`` and writing JSONL) and validates the
emitted rows against the template's ``schemas/row.schema.json``. Hermetic w.r.t.
the bucket: the miner always emits one row per (skill-with-archetype x archetype),
so the test passes whether the local bucket is empty or populated.
"""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.consumers.contract import run_workflow_rows
from opentraces.consumers.verifier_factory import ARCHETYPES

_TEMPLATE = "skill-verifier-candidates-v1"
_ROW_SCHEMA_VERSION = "opentraces.skill_verifier_candidates_row.v1"


def _row_schema_required_keys() -> list[str]:
    import importlib.resources as resources

    pkg = resources.files("opentraces.workflow_templates") / _TEMPLATE / "schemas" / "row.schema.json"
    schema = json.loads(pkg.read_text(encoding="utf-8"))
    return list(schema["required"])


def test_candidates_workflow_emits_schema_valid_rows(tmp_path):
    out = tmp_path / "rows.jsonl"
    result = run_workflow_rows(_TEMPLATE, scope={"min_usable_episodes": 30}, output_path=out)

    required = _row_schema_required_keys()
    assert result.row_count >= len(ARCHETYPES)

    seen_archetypes = set()
    for row in result.rows:
        assert row["schema_version"] == _ROW_SCHEMA_VERSION
        for key in required:
            assert key in row, f"row missing required key {key!r}"
        # the *_json string fields parse back to JSON
        json.loads(row["markers_json"])
        json.loads(row["deficient_marker_frequency_json"])
        json.loads(row["creator_questions_json"])
        json.loads(row["source_refs_json"])
        seen_archetypes.add(row["archetype_id"])

    assert set(ARCHETYPES) <= seen_archetypes


def test_candidates_workflow_scopes_to_one_skill(tmp_path):
    out = tmp_path / "rows.jsonl"
    result = run_workflow_rows(
        _TEMPLATE, scope={"skills": ["review"], "min_usable_episodes": 30}, output_path=out
    )
    assert result.row_count == 1
    assert result.rows[0]["skill_id"] == "review"
    assert result.rows[0]["archetype_id"] == "review_grounded_findings_v1"
