from __future__ import annotations

from pathlib import Path


def test_workflow_authoring_skill_is_bundled_and_packaged():
    root = Path(__file__).resolve().parents[2]
    skill = root / "skill" / "workflow-authoring" / "SKILL.md"
    example = root / "skill" / "workflow-authoring" / "examples" / "grill-me-intent-curator" / "SKILL.md"
    pyproject = root / "pyproject.toml"

    assert "ot dataset run" in skill.read_text()
    assert "OT_DATASET_OUTPUT" in skill.read_text()
    assert "ot trace query" in example.read_text()
    assert '"skill/workflow-authoring" = "opentraces/skill/workflow-authoring"' in (
        pyproject.read_text()
    )
