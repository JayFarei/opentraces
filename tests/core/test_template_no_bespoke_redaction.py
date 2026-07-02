"""Issue #189 -- the append-path floor is the only sanitization story.

Bundled workflow templates must not hand-roll their own redaction (entropy
scrubbers, home-path rewrites, secret-token regexes). The non-overridable
reader's floor run by ``datasets.append_rows`` (via
``security/dataset_rows.py::sanitize_dataset_row``) already sanitizes every
row a template projects, so a template-local scrubber is a second,
drift-prone redaction path with no benefit. See docs/adr/0008 §1.8.

Three acceptance checks:
  (a) grep-able: no bundled template's ``build_rows.py`` contains bespoke
      redaction logic.
  (b) planted secrets in a synthetic source row survive the template
      untouched, then get caught by the append-path floor.
  (c) the row shape (structure/keys) for skill-command-trajectory-eval-v1 is
      unchanged versus the golden fixture.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TEMPLATES_ROOT = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "opentraces"
    / "workflow_templates"
)
COMMAND_EVAL_TEMPLATE = TEMPLATES_ROOT / "skill-command-trajectory-eval-v1"

# Markers that indicate a template is hand-rolling its own redaction instead
# of deferring to the append-path floor (issue #189's delta).
_BESPOKE_REDACTION_MARKERS = (
    "shannon_entropy",
    "redact_high_entropy_tokens",
    "<TOKEN>",
    "<HOME>",
    "<NUM>",
)


def _template_build_rows_scripts() -> list[Path]:
    return sorted(TEMPLATES_ROOT.glob("*/scripts/build_rows.py"))


def test_no_bundled_template_hand_rolls_redaction():
    """(a) Grep-able assertion: no bundled template's build_rows.py contains
    bespoke redaction logic."""
    scripts = _template_build_rows_scripts()
    # Sanity: we are actually scanning the bundled template set, not an
    # accidentally-empty glob.
    assert len(scripts) >= 7

    offenders: dict[str, list[str]] = {}
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        hits = [marker for marker in _BESPOKE_REDACTION_MARKERS if marker in text]
        if hits:
            offenders[str(script.relative_to(TEMPLATES_ROOT))] = hits

    assert offenders == {}, f"bespoke redaction found in bundled templates: {offenders}"


def _run_command_eval_build_rows(source: Path, output: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(COMMAND_EVAL_TEMPLATE / "scripts" / "build_rows.py"),
            "--source",
            str(source),
            "--output",
            str(output),
        ],
        check=True,
    )


def test_command_eval_template_row_shape_matches_golden(tmp_path):
    """(c) Row shape (structure/keys) is unchanged for
    skill-command-trajectory-eval-v1. The bundled example carries no secret
    or path payload, so with the hand-roll removed the values are unaffected
    too -- a stronger, full-equality check against the existing golden
    fixture."""
    example_input = json.loads(
        (COMMAND_EVAL_TEMPLATE / "examples" / "input-candidate-packet.json").read_text(
            encoding="utf-8"
        )
    )
    expected = json.loads(
        (COMMAND_EVAL_TEMPLATE / "examples" / "expected-row.json").read_text(encoding="utf-8")
    )

    source = tmp_path / "raw-command-trajectories.jsonl"
    source.write_text(json.dumps(example_input, sort_keys=True) + "\n", encoding="utf-8")
    output = tmp_path / "rows.jsonl"
    _run_command_eval_build_rows(source, output)
    actual = json.loads(output.read_text(encoding="utf-8"))

    # Structure: identical key set to the golden fixture.
    assert set(actual.keys()) == set(expected.keys())
    # No sensitive payload in the fixture -> values are unaffected too.
    assert actual == expected


def test_planted_secrets_caught_by_append_path_floor(tmp_path):
    """(b) A planted secret + home path survive the template (it has no
    scrubber of its own), and are then redacted by the append-path floor --
    proving the template does not need mid-flight scrubbing."""
    planted_token = "sk-live-abcdefghijklmnopqrstuvwxyz123456"
    planted_home = "/Users/seededvictim/src/tries/some-project/notes.md"

    raw_row = {
        "row_id": "planted-secret-row",
        "source_trace_id": "planted-trace",
        "source_unit_id": "tu:planted:skill:metadata:0",
        "project_slug": "planted-project",
        "command": {
            "skill_name": "scout-research",
            "command_name": "/scout-research",
            "source": "claude_slash_command",
            "args": f"Use token {planted_token} while reading {planted_home}.",
            "trigger_text": f"Use token {planted_token} while reading {planted_home}.",
        },
        "attribution": {
            "confidence": "high",
            "start_step_index": 1,
            "end_step_index": 2,
            "excluded_step_indices": [],
        },
        "trajectory": {
            "summary": {
                "tools": ["Read"],
                "files_modified": [planted_home],
                "write_operation_count": 0,
                "patch_refs": [],
            }
        },
        "outcome": {"success": True, "committed": False, "commit_sha": ""},
        "quality": {"limitations": []},
    }

    source = tmp_path / "raw-command-trajectories.jsonl"
    source.write_text(json.dumps(raw_row, sort_keys=True) + "\n", encoding="utf-8")
    output = tmp_path / "rows.jsonl"
    _run_command_eval_build_rows(source, output)
    built_row = json.loads(output.read_text(encoding="utf-8"))

    # Red proof: the template itself does not scrub -- the planted secret and
    # home-path username come through the build step verbatim.
    raw_dump = json.dumps(built_row)
    assert planted_token in raw_dump
    assert "seededvictim" in raw_dump

    # Green proof: the append-path floor catches what the template didn't.
    # A permissive local schema is used deliberately -- this test verifies
    # the floor's sanitization behavior, not the packaged schema's field
    # completeness (a separate concern from issue #189).
    from opentraces.core.datasets import append_rows, create_dataset, dataset_path

    row_schema = {
        "type": "object",
        "required": ["source_trace_id", "source_unit_id", "summary"],
        "properties": {
            "source_trace_id": {"type": "string"},
            "source_unit_id": {"type": "string"},
            "summary": {"type": "string"},
        },
    }
    create_dataset(
        "planted-secret-ds",
        workflow_skill="skill-command-trajectory-eval-v1",
        workflow_digest="sha256:test",
        row_schema=row_schema,
    )
    summary = append_rows("planted-secret-ds", [built_row], run_id="run-1")
    assert summary.appended_count == 1
    assert summary.validation_error_count == 0

    stored = (dataset_path("planted-secret-ds") / "data" / "train.jsonl").read_text(
        encoding="utf-8"
    )
    assert planted_token not in stored
    assert "seededvictim" not in stored
    assert "[REDACTED]" in stored
