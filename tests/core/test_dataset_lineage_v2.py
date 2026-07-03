"""Row lineage v2 (#191): first-class trace:A-B refs + contract-triple provenance.

RED-first coverage for the seal-family M2 dataset foundation:

* rows carry a parsed, validated span ref resolved through the shared
  ``parse_trail_ref`` oracle (never a re-implemented span parser);
* a row with only a ``ref`` field round-trips lineage completely;
* provenance is bumped to ``row_provenance.v2`` and records the full contract
  triple (workflow digest + bucket digest + answers digest);
* synthetic ``raw:...`` template ids degrade to an opaque trace ref rather than
  crash, and still round-trip their source trace id;
* ``forget_trace_cascade`` resolves rows by the parsed ref (row index /
  provenance), not by scraping public-row fields.
"""

from __future__ import annotations

import json

from opentraces.core.trails.lineage import parse_trail_ref


_UUID = "9f8e7d6c-1234-4abc-8def-0123456789ab"


def _ref_schema() -> dict:
    return {
        "type": "object",
        "required": ["ref", "summary"],
        "properties": {
            "ref": {"type": "string"},
            "summary": {"type": "string"},
        },
        "additionalProperties": False,
    }


def _field_schema() -> dict:
    return {
        "type": "object",
        "required": ["source_trace_id", "source_unit_id", "summary"],
        "properties": {
            "source_trace_id": {"type": "string"},
            "source_unit_id": {"type": "string"},
            "summary": {"type": "string"},
        },
        "additionalProperties": False,
    }


def test_extract_row_source_refs_uses_shared_parse_oracle():
    """The ref resolver delegates span parsing to the pinned oracle."""
    from opentraces.core.datasets import _extract_row_source_refs

    refs = _extract_row_source_refs({"ref": f"{_UUID}:3-9"})

    trace_id, step_index, span, reserved = parse_trail_ref(f"{_UUID}:3-9")
    assert refs["trace_id"] == trace_id == _UUID
    assert refs["step_range"] == list(span) == [3, 9]
    assert refs["ref"] == f"{_UUID}:3-9"
    assert refs["ref_valid"] is True
    assert step_index is None and reserved == "span"


def test_ref_only_row_round_trips_lineage():
    """A row carrying only a ``ref`` reconstructs trace id + step range."""
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        read_row_index,
        read_row_provenance,
    )

    create_dataset(
        "ref-only",
        workflow_skill="ref-curator",
        workflow_digest="sha256:wf",
        row_schema=_ref_schema(),
    )
    row = {"ref": f"{_UUID}:3-9", "summary": "A ref-only row."}
    summary = append_rows("ref-only", [row], run_id="run_1")
    assert summary.appended_count == 1

    entry = read_row_index("ref-only")[0]
    # The parsed trace id is populated into the index — no source_trace_id field
    # existed on the row at all.
    assert entry.source_trace_id == _UUID
    provenance = read_row_provenance("ref-only")[entry.row_id]
    assert provenance["source_refs"]["trace_id"] == _UUID
    assert provenance["source_refs"]["step_range"] == [3, 9]
    assert provenance["source_refs"]["ref"] == f"{_UUID}:3-9"
    assert provenance["source_refs"]["ref_valid"] is True
    # First-class ref lives inside the free provenance dict (no schema field).
    assert provenance["ref"] == f"{_UUID}:3-9"


def test_provenance_is_v2_and_records_contract_triple():
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        digest_payload,
        read_row_provenance,
    )

    create_dataset(
        "triple",
        workflow_skill="triple-curator",
        workflow_digest="sha256:wf-triple",
        row_schema=_field_schema(),
    )
    row = {
        "source_trace_id": _UUID,
        "source_unit_id": "tu:one",
        "summary": "A contract-triple row.",
    }
    append_rows("triple", [row], run_id="run_1")
    provenance = next(iter(read_row_provenance("triple").values()))

    assert provenance["schema_version"] == "opentraces.dataset.row_provenance.v2"
    # Fourth contract input: answers. Absent run packet => empty recorded set,
    # but a well-defined digest (honest, not omitted).
    assert provenance["answers"]["recorded"] == {}
    assert provenance["answers"]["digest"] == digest_payload({})
    triple = provenance["contract_triple"]
    assert triple["workflow_digest"] == "sha256:wf-triple"
    assert triple["answers_digest"] == digest_payload({})
    assert "bucket_digest" in triple


def test_answers_leg_reads_recorded_run_packet_answers():
    """Recorded judgments in the run packet are the fourth contract input."""
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        dataset_path,
        digest_payload,
        read_row_provenance,
    )

    create_dataset(
        "with-answers",
        workflow_skill="answer-curator",
        workflow_digest="sha256:wf",
        row_schema=_field_schema(),
    )
    run_id = "run_answers"
    run_dir = dataset_path("with-answers") / ".opentraces" / "runs" / run_id
    run_dir.mkdir(parents=True)
    answers = {"q1": {"decision": "keep", "confidence": 0.9}}
    (run_dir / "run_packet.json").write_text(
        json.dumps({"schema_version": "opentraces.workflow.run_packet.v1", "answers": answers}),
        encoding="utf-8",
    )
    row = {
        "source_trace_id": _UUID,
        "source_unit_id": "tu:one",
        "summary": "A judged row.",
    }
    append_rows("with-answers", [row], run_id=run_id)
    provenance = next(iter(read_row_provenance("with-answers").values()))

    assert provenance["answers"]["recorded"] == answers
    assert provenance["answers"]["digest"] == digest_payload(answers)
    assert provenance["contract_triple"]["answers_digest"] == digest_payload(answers)


def test_synthetic_colon_ref_round_trips_opaque():
    """A synthetic ``raw:...`` id cannot form a valid trace:A-B address; it
    degrades to an opaque trace ref and still preserves its source trace id."""
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        read_row_index,
        read_row_provenance,
    )

    create_dataset(
        "synthetic",
        workflow_skill="raw-curator",
        workflow_digest="sha256:wf",
        row_schema=_field_schema(),
    )
    row = {
        "source_trace_id": "raw:raw-row-000001",
        "source_unit_id": "raw-unit:raw-row-000001",
        "summary": "A synthetic template row.",
    }
    append_rows("synthetic", [row], run_id="run_1")

    entry = read_row_index("synthetic")[0]
    assert entry.source_trace_id == "raw:raw-row-000001"
    provenance = read_row_provenance("synthetic")[entry.row_id]
    assert provenance["source_refs"]["trace_id"] == "raw:raw-row-000001"
    assert provenance["source_refs"]["ref_valid"] is False


def test_forget_cascade_resolves_ref_only_row():
    """forget_trace_cascade resolves rows by the parsed ref via the row index."""
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        forget_trace_cascade,
        load_public_rows,
    )

    create_dataset(
        "cascade",
        workflow_skill="cascade-curator",
        workflow_digest="sha256:wf",
        row_schema=_ref_schema(),
    )
    append_rows(
        "cascade",
        [{"ref": f"{_UUID}:0-4", "summary": "A ref-only row to forget."}],
        run_id="run_1",
    )
    assert len(load_public_rows("cascade")) == 1

    result = forget_trace_cascade(_UUID, reason="user-request")
    assert result["withdrawn_count"] == 1
    assert result["affected"][0]["dataset"] == "cascade"
    # The row is now tombstoned in the withdrawal-aware read.
    assert load_public_rows("cascade") == []


def test_explicit_step_range_field_is_preserved_verbatim():
    """An explicit step_range dict on the row is preserved (not overwritten by a
    derived span) — the pre-existing provenance shape stays byte-compatible."""
    from opentraces.core.datasets import _extract_row_source_refs

    refs = _extract_row_source_refs(
        {
            "source_trace_id": _UUID,
            "source_unit_id": "tu:one",
            "source_slice_id": "slice-1",
            "step_range": {"start": 2, "end": 5},
        }
    )
    assert refs["trace_id"] == _UUID
    assert refs["unit_id"] == "tu:one"
    assert refs["slice_id"] == "slice-1"
    assert refs["step_range"] == {"start": 2, "end": 5}
