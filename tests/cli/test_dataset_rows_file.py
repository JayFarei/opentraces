"""Cluster D2 — ad-hoc ``dataset new --rows-file --schema`` path.

Today ``opentraces dataset new <name>`` requires a workflow skill. This
is heavy when an agent already has rows ready (e.g. from
``trace map --bursts``). The new mode synthesizes a manual dataset
from a JSONL file + JSON Schema, no workflow required.

Acceptance:
- ``--rows-file`` and ``--schema`` together create a manual dataset.
- Manifest records ``workflow.skill = "manual"`` so subsequent
  ``dataset run`` is a no-op (emits ``manual_dataset_no_run_action``).
- Rows are copied into ``data/train.jsonl`` via the canonical
  ``append_rows`` path so the dataset can be reviewed/published like
  any other.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import dataset_path, load_dataset


def _write_rows(path, rows):
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def _basic_schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "summary"],
        "properties": {
            "id": {"type": "string"},
            "summary": {"type": "string"},
        },
    }


def test_dataset_new_rows_file_creates_manifest(tmp_path):
    rows_path = tmp_path / "rows.jsonl"
    schema_path = tmp_path / "schema.json"
    _write_rows(rows_path, [
        {"id": "r1", "summary": "first"},
        {"id": "r2", "summary": "second"},
        {"id": "r3", "summary": "third"},
    ])
    schema_path.write_text(json.dumps(_basic_schema()), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        dataset_group,
        [
            "new",
            "manual-test",
            "--rows-file", str(rows_path),
            "--schema", str(schema_path),
            "--description", "ad-hoc manual",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["dataset"]["name"] == "manual-test"
    # Manual marker on the dataset payload.
    assert payload["dataset"].get("manual") is True
    assert payload["dataset"]["manifest"]["workflow"]["skill"] == "manual"
    assert payload["dataset"]["row_count"] == 3

    # Schema and rows landed on disk under the canonical layout.
    ds = load_dataset("manual-test")
    schema_on_disk = ds.path / "schemas" / "row.schema.json"
    assert schema_on_disk.exists()
    written_schema = json.loads(schema_on_disk.read_text(encoding="utf-8"))
    assert written_schema["required"] == ["id", "summary"]
    rows_on_disk = (ds.path / "data" / "train.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows_on_disk) == 3


def test_dataset_new_rows_file_appears_in_dataset_list(tmp_path):
    rows_path = tmp_path / "rows.jsonl"
    schema_path = tmp_path / "schema.json"
    _write_rows(rows_path, [{"id": "x", "summary": "y"}])
    schema_path.write_text(json.dumps(_basic_schema()), encoding="utf-8")

    runner = CliRunner()
    create = runner.invoke(
        dataset_group,
        [
            "new",
            "listed-manual",
            "--rows-file", str(rows_path),
            "--schema", str(schema_path),
        ],
    )
    assert create.exit_code == 0, create.output

    listed = runner.invoke(main, ["--json", "dataset", "list"])
    assert listed.exit_code == 0, listed.output
    payload = json.loads(listed.output)
    by_name = {item["name"]: item for item in payload["datasets"]}
    assert "listed-manual" in by_name
    assert by_name["listed-manual"]["manifest"]["workflow"]["skill"] == "manual"
    assert by_name["listed-manual"].get("manual") is True


def test_dataset_new_rows_file_status_returns_correct_row_count(tmp_path):
    rows_path = tmp_path / "rows.jsonl"
    schema_path = tmp_path / "schema.json"
    _write_rows(rows_path, [
        {"id": "a", "summary": "1"},
        {"id": "b", "summary": "2"},
        {"id": "c", "summary": "3"},
        {"id": "d", "summary": "4"},
    ])
    schema_path.write_text(json.dumps(_basic_schema()), encoding="utf-8")

    runner = CliRunner()
    create = runner.invoke(
        dataset_group,
        [
            "new",
            "status-manual",
            "--rows-file", str(rows_path),
            "--schema", str(schema_path),
        ],
    )
    assert create.exit_code == 0, create.output

    status = runner.invoke(main, ["--json", "dataset", "status", "status-manual"])
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["status"] == "ok"
    assert payload["dataset"] == "status-manual"
    assert payload["row_count"] == 4
    assert payload.get("manual") is True


def test_dataset_new_rejects_invalid_jsonl(tmp_path):
    rows_path = tmp_path / "rows.jsonl"
    schema_path = tmp_path / "schema.json"
    rows_path.write_text("not json at all\n", encoding="utf-8")
    schema_path.write_text(json.dumps(_basic_schema()), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        dataset_group,
        [
            "new",
            "bad-jsonl",
            "--rows-file", str(rows_path),
            "--schema", str(schema_path),
        ],
    )
    assert result.exit_code != 0
    # Dataset was not created — ``dataset_path`` should not exist.
    assert not dataset_path("bad-jsonl").exists()


def test_dataset_new_requires_both_rows_file_and_schema(tmp_path):
    rows_path = tmp_path / "rows.jsonl"
    _write_rows(rows_path, [{"id": "x", "summary": "y"}])

    runner = CliRunner()
    # --rows-file without --schema should be rejected.
    result = runner.invoke(
        dataset_group,
        [
            "new",
            "rows-only",
            "--rows-file", str(rows_path),
        ],
    )
    assert result.exit_code != 0
    assert not dataset_path("rows-only").exists()

    # --schema without --rows-file should also be rejected.
    schema_path = rows_path.parent / "schema.json"
    schema_path.write_text(json.dumps(_basic_schema()), encoding="utf-8")
    result = runner.invoke(
        dataset_group,
        [
            "new",
            "schema-only",
            "--schema", str(schema_path),
        ],
    )
    assert result.exit_code != 0
    assert not dataset_path("schema-only").exists()


def test_dataset_run_on_manual_dataset_is_a_noop(tmp_path):
    """Manual datasets have no workflow; ``dataset run`` must no-op cleanly."""
    rows_path = tmp_path / "rows.jsonl"
    schema_path = tmp_path / "schema.json"
    _write_rows(rows_path, [{"id": "x", "summary": "y"}])
    schema_path.write_text(json.dumps(_basic_schema()), encoding="utf-8")

    runner = CliRunner()
    create = runner.invoke(
        dataset_group,
        [
            "new",
            "noop-manual",
            "--rows-file", str(rows_path),
            "--schema", str(schema_path),
        ],
    )
    assert create.exit_code == 0, create.output

    run = runner.invoke(dataset_group, ["run", "noop-manual", "--json"])
    assert run.exit_code == 0, run.output
    payload = json.loads(run.output)
    assert payload["status"] == "manual_dataset_no_run_action"
