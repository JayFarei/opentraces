"""Cluster D2 — full review/approve loop on a manual dataset.

End-to-end exercise of the ad-hoc dataset path: create manual dataset
from rows + schema, evaluate publication state, approve a row, verify
the publication state moves to ``publishable``.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import (
    evaluate_publication_state,
    read_publication_state,
    read_row_index,
)


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


def _write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def test_full_review_approve_loop_on_manual_dataset(tmp_path):
    runner = CliRunner()

    rows_path = tmp_path / "rows.jsonl"
    schema_path = tmp_path / "schema.json"
    _write_jsonl(rows_path, [
        {"id": "burst-1", "summary": "first burst"},
        {"id": "burst-2", "summary": "second burst"},
    ])
    schema_path.write_text(json.dumps(_basic_schema()), encoding="utf-8")

    # 1. Create manual dataset.
    create = runner.invoke(
        dataset_group,
        [
            "new",
            "adhoc-loop",
            "--rows-file", str(rows_path),
            "--schema", str(schema_path),
            "--description", "ad-hoc agent-built dataset",
            "--json",
        ],
    )
    assert create.exit_code == 0, create.output
    payload = json.loads(create.output)
    assert payload["status"] == "ok"
    assert payload["dataset"]["name"] == "adhoc-loop"
    assert payload["dataset"]["row_count"] == 2

    # 2. Verify it shows up in `dataset list` with the manual marker.
    listed = runner.invoke(main, ["--json", "dataset", "list"])
    assert listed.exit_code == 0
    listed_payload = json.loads(listed.output)
    by_name = {item["name"]: item for item in listed_payload["datasets"]}
    assert by_name["adhoc-loop"]["manifest"]["workflow"]["skill"] == "manual"

    # 3. Verify status returns correct row count.
    status = runner.invoke(main, ["--json", "dataset", "status", "adhoc-loop"])
    assert status.exit_code == 0
    status_payload = json.loads(status.output)
    assert status_payload["row_count"] == 2

    # 4. Open review: rows should appear with ``needs_review`` (default policy).
    review = runner.invoke(dataset_group, ["review", "adhoc-loop", "--json"])
    assert review.exit_code == 0, review.output
    review_payload = json.loads(review.output)
    assert review_payload["dataset"] == "adhoc-loop"
    rows = review_payload["rows"]
    assert len(rows) == 2
    statuses = {row["status"] for row in rows.values()}
    assert "needs_review" in statuses

    # 5. Approve one specific row.
    row_ids = sorted(rows)
    first_row = row_ids[0]
    approve = runner.invoke(
        dataset_group,
        ["approve", "adhoc-loop", first_row, "--json"],
    )
    assert approve.exit_code == 0, approve.output
    approve_payload = json.loads(approve.output)
    assert approve_payload["status"] == "ok"

    # 6. Re-read publication state — approved row is publishable, the other
    #    is still needs_review.
    state = read_publication_state("adhoc-loop")
    assert state.rows[first_row].status == "publishable"
    other_row = row_ids[1]
    assert state.rows[other_row].status == "needs_review"

    # 7. Run on a manual dataset is a no-op (no workflow to execute).
    run = runner.invoke(dataset_group, ["run", "adhoc-loop", "--json"])
    assert run.exit_code == 0, run.output
    run_payload = json.loads(run.output)
    assert run_payload["status"] == "manual_dataset_no_run_action"

    # 8. Row index entries match the input row count.
    entries = read_row_index("adhoc-loop")
    assert len(entries) == 2
