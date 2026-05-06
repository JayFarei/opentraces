from __future__ import annotations

import json


def _row_schema() -> dict:
    return {
        "type": "object",
        "required": ["source_trace_id", "source_unit_id", "summary"],
        "properties": {
            "source_trace_id": {"type": "string"},
            "source_unit_id": {"type": "string"},
            "summary": {"type": "string"},
            "quality_score": {"type": "number"},
        },
        "additionalProperties": False,
    }


def test_dataset_creation_writes_hf_shaped_public_tree_and_private_manifest():
    from opentraces.core.datasets import create_dataset, dataset_path, load_manifest

    created = create_dataset(
        "grill-me-intents",
        description="Intent summaries for traces that invoked the grill-me skill.",
        workflow_skill="grill-me-intent-curator",
        workflow_digest="sha256:workflow",
        row_schema=_row_schema(),
    )

    root = dataset_path("grill-me-intents")
    assert created.path == root
    assert (root / "README.md").exists()
    assert (root / "dataset_infos.json").exists()
    assert json.loads((root / "schemas" / "row.schema.json").read_text()) == _row_schema()
    assert (root / "data" / "train.jsonl").read_text() == ""
    assert (root / ".opentraces" / "row_index.jsonl").read_text() == ""
    assert (root / ".opentraces" / "cursors.yaml").exists()
    assert (root / ".opentraces" / "runs").is_dir()
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "license: null" not in readme

    manifest = load_manifest(root)
    assert manifest.name == "grill-me-intents"
    assert manifest.schema_ref.path == "schemas/row.schema.json"
    assert manifest.workflow.skill == "grill-me-intent-curator"
    assert manifest.workflow.digest == "sha256:workflow"
    assert manifest.description == "Intent summaries for traces that invoked the grill-me skill."


def test_dataset_creation_persists_bucket_query_provenance():
    from opentraces.core.bucket_store import trace_record_snapshot
    from opentraces.core.datasets import create_dataset, load_manifest, read_source_provenance

    created = create_dataset(
        "semantic-intents",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        candidate_query={
            "name": "mongodb-query",
            "scope": "all-projects",
            "args": {"semantic": "mongodb", "source": "projection"},
        },
    )

    manifest = load_manifest(created.path)
    assert not hasattr(manifest, "source_provenance")
    provenance = read_source_provenance(created.path)
    assert provenance is not None
    assert provenance["schema_version"] == "opentraces.dataset.source_provenance.v1"
    assert provenance["query_fingerprint"] is not None
    assert provenance["bucket_snapshot"] == trace_record_snapshot(include_objects=False)


def test_append_rows_validates_schema_dedupes_and_rebuilds_row_index():
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        dataset_path,
        read_row_index,
        rebuild_row_index,
    )

    create_dataset(
        "grill-me-intents",
        workflow_skill="grill-me-intent-curator",
        workflow_digest="sha256:workflow",
        row_schema=_row_schema(),
    )
    row = {
        "source_trace_id": "trace-1",
        "source_unit_id": "tu:trace-1:trace",
        "summary": "The user wanted a stricter design review.",
        "quality_score": 0.9,
    }
    invalid = {
        "source_trace_id": "trace-2",
        "source_unit_id": "tu:trace-2:trace",
        "summary": "Unexpected field should fail.",
        "extra": True,
    }

    first = append_rows("grill-me-intents", [row, row, invalid], run_id="run_1")
    assert first.appended_count == 1
    assert first.duplicate_count == 1
    assert first.validation_error_count == 1
    assert first.validation_errors[0]["line"] == 3

    data_lines = (dataset_path("grill-me-intents") / "data" / "train.jsonl").read_text().splitlines()
    assert len(data_lines) == 1
    assert json.loads(data_lines[0]) == row

    entries = read_row_index("grill-me-intents")
    assert len(entries) == 1
    assert entries[0].data_file == "data/train.jsonl"
    assert entries[0].line == 1
    assert entries[0].run_id == "run_1"

    second = append_rows("grill-me-intents", [row], run_id="run_2")
    assert second.appended_count == 0
    assert second.duplicate_count == 1

    (dataset_path("grill-me-intents") / ".opentraces" / "row_index.jsonl").unlink()
    rebuilt = rebuild_row_index("grill-me-intents")
    assert rebuilt.rebuilt_count == 1
    assert read_row_index("grill-me-intents")[0].payload_hash == entries[0].payload_hash


def test_dry_run_preview_never_appends_or_updates_row_index():
    from opentraces.core.datasets import append_rows, create_dataset, dataset_path, read_row_index

    create_dataset(
        "dry-run-intents",
        workflow_skill="dry-run-curator",
        workflow_digest="sha256:workflow",
        row_schema=_row_schema(),
    )

    summary = append_rows(
        "dry-run-intents",
        [
            {
                "source_trace_id": "trace-1",
                "source_unit_id": "tu:trace-1:trace",
                "summary": "Dry-run only.",
            }
        ],
        run_id="run_dry",
        dry_run=True,
    )

    assert summary.appended_count == 0
    assert summary.would_append_count == 1
    assert (dataset_path("dry-run-intents") / "data" / "train.jsonl").read_text() == ""
    assert read_row_index("dry-run-intents") == []


def test_field_identity_dedupes_distinct_payloads_for_same_declared_identity():
    from opentraces.core.datasets import append_rows, create_dataset

    create_dataset(
        "field-identity",
        workflow_skill="identity-curator",
        workflow_digest="sha256:workflow",
        row_schema=_row_schema(),
        identity={"mode": "fields", "fields": ["source_trace_id", "source_unit_id"]},
    )

    first = {
        "source_trace_id": "trace-1",
        "source_unit_id": "tu:trace-1:trace",
        "summary": "First summary.",
    }
    second = {
        "source_trace_id": "trace-1",
        "source_unit_id": "tu:trace-1:trace",
        "summary": "Updated summary, same declared identity.",
    }
    summary = append_rows("field-identity", [first, second], run_id="run_1")

    assert summary.appended_count == 1
    assert summary.duplicate_count == 1
