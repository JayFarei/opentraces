"""#186 — workflow rc=10 judgment handshake with recorded answers.

Mirrors the slicing rc=10 precedent for workflows:

* a builder that needs judgment writes JudgmentRequests to the sidecar and exits
  rc=10; the runner raises ``WorkflowNeedsJudgmentError`` and the CLI renders the
  ``opentraces.workflow.needs_judgment.v1`` envelope + exits rc=10;
* re-running with ``--answers`` completes rc=0 and persists the answers in the
  versioned run packet (the fourth contract input);
* same answers -> BYTE-IDENTICAL rows (the determinism gate);
* a no-judgment workflow is rc/envelope-UNCHANGED (the regression guard).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli.dataset import dataset_group
from opentraces.core import workflow_judgment
from opentraces.core.datasets import create_dataset, dataset_path
from opentraces.core.slicing.models import (
    JudgmentAnswer as SlicingJudgmentAnswer,
    JudgmentRequest as SlicingJudgmentRequest,
)
from opentraces.core.workflow_runner import (
    WorkflowNeedsJudgmentError,
    run_dataset_workflow,
)
from opentraces.core.workflows import workflows_dir


# A builder that declares a judgment point across the subprocess boundary: with
# no recorded answers it writes its JudgmentRequests to OT_JUDGMENT_SIDECAR and
# exits rc=10; with answers in the packet it emits rows keyed by the decision.
_JUDGMENT_BUILD_ROWS = '''\
import json, os
from pathlib import Path

packet = json.loads(Path(os.environ["OT_RUN_PACKET"]).read_text(encoding="utf-8"))
answers = packet.get("answers") or {}
if not answers:
    requests = [
        {
            "id": "q1",
            "kind": "same_outcome",
            "window": [0, 3],
            "candidate_step": 2,
            "prompt": "Do the clustered successes form a single outcome?",
            "answer_schema": {"decision": ["single", "distinct"]},
        }
    ]
    Path(os.environ["OT_JUDGMENT_SIDECAR"]).write_text(
        json.dumps({"judgment_requests": requests}), encoding="utf-8"
    )
    raise SystemExit(10)

decision = answers.get("q1", {}).get("decision", "unknown")
rows = [{"id": "q1", "decision": decision, "n": 1}]
Path(os.environ["OT_DATASET_OUTPUT"]).write_text(
    "".join(json.dumps(r, sort_keys=True) + "\\n" for r in rows), encoding="utf-8"
)
'''

# A plain deterministic builder that never needs judgment.
_NOJUDGMENT_BUILD_ROWS = '''\
import json, os
from pathlib import Path

rows = [{"id": "static", "n": 1}]
Path(os.environ["OT_DATASET_OUTPUT"]).write_text(
    "".join(json.dumps(r, sort_keys=True) + "\\n" for r in rows), encoding="utf-8"
)
'''

_ANSWERS = {"q1": {"decision": "single", "confidence": 1.0}}


def _install_workflow(name: str, script: str) -> None:
    pkg = workflows_dir() / name
    (pkg / "scripts").mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: judgment fixture\nmode: agent-skill\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (pkg / "scripts" / "build_rows.py").write_text(script, encoding="utf-8")


def _new_dataset(name: str, script: str) -> None:
    create_dataset(
        name,
        workflow_skill=f"{name}-curator",
        workflow_digest="sha256:judgment",
        row_schema={"type": "object", "additionalProperties": True},
    )
    _install_workflow(f"{name}-curator", script)


# --- workflow_judgment module unit surface ----------------------------------


def test_constants_and_rc_mirror_the_slicing_precedent():
    assert workflow_judgment.RC_NEEDS_JUDGMENT == 10
    assert (
        workflow_judgment.WORKFLOW_NEEDS_JUDGMENT_SCHEMA_VERSION
        == "opentraces.workflow.needs_judgment.v1"
    )
    # The judgment answer/request shape is the SHARED slicing schema, referenced
    # (not re-minted) because the models are reused verbatim.
    assert (
        workflow_judgment.JUDGMENT_SCHEMA_VERSION == "opentraces.slicing.judgment.v1"
    )


def test_models_are_reused_from_slicing_not_forked():
    assert workflow_judgment.JudgmentRequest is SlicingJudgmentRequest
    assert workflow_judgment.JudgmentAnswer is SlicingJudgmentAnswer


def test_build_needs_judgment_envelope_shape():
    req = SlicingJudgmentRequest(
        id="q1",
        kind="same_outcome",
        window=[0, 3],
        candidate_step=2,
        prompt="one outcome?",
        answer_schema={"decision": ["single", "distinct"]},
    )
    env = workflow_judgment.build_needs_judgment(
        workflow="curator", judgment_requests=[req]
    )
    assert env["schema_version"] == "opentraces.workflow.needs_judgment.v1"
    assert env["status"] == "needs_judgment"
    assert env["workflow"] == "curator"
    assert env["judgment_schema_version"] == "opentraces.slicing.judgment.v1"
    assert env["judgment_requests"][0]["id"] == "q1"
    assert "re-run" in env["instruction"] and "--answers" in env["instruction"]
    # Accepts already-plain dicts too (the subprocess sidecar form).
    env2 = workflow_judgment.build_needs_judgment(
        workflow="curator", judgment_requests=[{"id": "q2"}]
    )
    assert env2["judgment_requests"][0]["id"] == "q2"


def test_load_answers_round_trip_both_shapes(tmp_path):
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(
        json.dumps({"answers": [{"id": "q1", "decision": "single", "confidence": 0.9}]}),
        encoding="utf-8",
    )
    bare = tmp_path / "bare.json"
    bare.write_text(
        json.dumps([{"id": "q1", "decision": "single"}]), encoding="utf-8"
    )
    assert workflow_judgment.load_answers(wrapped) == {
        "q1": {"decision": "single", "confidence": 0.9}
    }
    assert workflow_judgment.load_answers(bare) == {
        "q1": {"decision": "single", "confidence": 1.0}
    }


def test_read_sidecar_requests_both_shapes(tmp_path):
    wrapped = tmp_path / "w.json"
    wrapped.write_text(json.dumps({"judgment_requests": [{"id": "q1"}]}), encoding="utf-8")
    bare = tmp_path / "b.json"
    bare.write_text(json.dumps([{"id": "q2"}]), encoding="utf-8")
    assert workflow_judgment.read_sidecar_requests(wrapped) == [{"id": "q1"}]
    assert workflow_judgment.read_sidecar_requests(bare) == [{"id": "q2"}]


# --- rc=10 handshake at the runner level ------------------------------------


def test_runner_raises_needs_judgment_when_unanswered():
    _new_dataset("judg-handshake", _JUDGMENT_BUILD_ROWS)
    with pytest.raises(WorkflowNeedsJudgmentError) as excinfo:
        run_dataset_workflow("judg-handshake", executor="script", dry_run=True)
    exc = excinfo.value
    assert exc.workflow_name == "judg-handshake-curator"
    assert exc.judgment_requests[0]["id"] == "q1"
    assert exc.judgment_requests[0]["prompt"]


# --- rc=10 handshake at the CLI level ---------------------------------------


def test_cli_needs_judgment_emits_envelope_and_rc10(tmp_path):
    _new_dataset("judg-cli", _JUDGMENT_BUILD_ROWS)
    runner = CliRunner()

    first = runner.invoke(
        dataset_group, ["run", "judg-cli", "--executor", "script", "--json"]
    )
    assert first.exit_code == 10, first.output
    envelope = json.loads(first.stdout)
    assert envelope["schema_version"] == "opentraces.workflow.needs_judgment.v1"
    assert envelope["status"] == "needs_judgment"
    assert envelope["judgment_requests"][0]["id"] == "q1"

    # Re-run with recorded answers -> rc=0.
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(
        json.dumps({"answers": [{"id": "q1", "decision": "single", "confidence": 1.0}]}),
        encoding="utf-8",
    )
    second = runner.invoke(
        dataset_group,
        [
            "run",
            "judg-cli",
            "--executor",
            "script",
            "--answers",
            str(answers_file),
            "--json",
        ],
    )
    assert second.exit_code == 0, second.output
    payload = json.loads(second.stdout)
    assert payload["status"] == "ok"
    assert payload["run"]["appended_count"] >= 1


# --- answers persisted in the run packet ------------------------------------


def test_answers_are_persisted_in_the_run_packet():
    _new_dataset("judg-persist", _JUDGMENT_BUILD_ROWS)
    result = run_dataset_workflow(
        "judg-persist", executor="script", dry_run=True, answers=_ANSWERS
    )
    packet = json.loads((result.run_dir / "run_packet.json").read_text(encoding="utf-8"))
    # The answers ARE the fourth contract input recorded in the packet.
    assert packet["answers"] == _ANSWERS


# --- determinism gate: same answers -> byte-identical rows ------------------


def test_same_answers_produce_byte_identical_rows():
    _new_dataset("judg-determinism", _JUDGMENT_BUILD_ROWS)
    first = run_dataset_workflow(
        "judg-determinism", executor="script", dry_run=True, answers=_ANSWERS
    )
    second = run_dataset_workflow(
        "judg-determinism", executor="script", dry_run=True, answers=_ANSWERS
    )
    rows_a = (first.run_dir / "output_rows.jsonl").read_text(encoding="utf-8")
    rows_b = (second.run_dir / "output_rows.jsonl").read_text(encoding="utf-8")
    assert rows_a == rows_b
    assert rows_a.strip(), "the determinism gate must compare non-empty rows"


def test_same_answers_produce_byte_identical_appended_rows():
    # Two FRESH datasets (no intra-dataset dedup) appended for real; the canonical
    # row bytes append_rows writes must be identical for identical answers.
    _new_dataset("judg-append-a", _JUDGMENT_BUILD_ROWS)
    _new_dataset("judg-append-b", _JUDGMENT_BUILD_ROWS)
    run_dataset_workflow("judg-append-a", executor="script", answers=_ANSWERS)
    run_dataset_workflow("judg-append-b", executor="script", answers=_ANSWERS)
    train_a = (dataset_path("judg-append-a") / "data" / "train.jsonl").read_text(
        encoding="utf-8"
    )
    train_b = (dataset_path("judg-append-b") / "data" / "train.jsonl").read_text(
        encoding="utf-8"
    )
    assert train_a == train_b
    assert train_a.strip()


# --- regression guard: a no-judgment workflow is unaffected ------------------


def test_no_judgment_workflow_is_unchanged():
    _new_dataset("judg-none", _NOJUDGMENT_BUILD_ROWS)
    # No rc=10, no envelope change: a plain deterministic run just succeeds.
    result = run_dataset_workflow("judg-none", executor="script", dry_run=True)
    assert result.status == "ok"
    packet = json.loads((result.run_dir / "run_packet.json").read_text(encoding="utf-8"))
    # An unanswered no-judgment run carries NO answers key in the packet.
    assert "answers" not in packet
    rows = (result.run_dir / "output_rows.jsonl").read_text(encoding="utf-8")
    assert json.loads(rows.strip()) == {"id": "static", "n": 1}
