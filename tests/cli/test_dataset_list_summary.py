"""Cluster F D9 — ``dataset list / status`` row_quality summary.

Extends ``--json`` output with a ``row_quality`` block surfacing:

* ``total_rows`` — count of rows in the dataset
* ``rows_with_anchored_patches`` — rows where ≥1 patch has a survival_state
* ``rows_with_lost_patches`` — rows where ≥1 patch has survival_state == lost
* ``rows_fully_alive`` — rows where every patch has alive_on_path /
  alive_transformed
* ``mean_row_retention`` — mean of mean retention across rows (None when
  no row has any defined retention)
* ``survival_distribution`` — ``{state: count}`` across all patches in
  the dataset
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.cli.dataset import dataset_group


def _basic_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": True,
        "required": ["row_id"],
        "properties": {"row_id": {"type": "string"}},
    }


def _seed_via_cli(name: str, rows_path: Path, schema_path: Path) -> None:
    schema_path.write_text(json.dumps(_basic_schema()))
    runner = CliRunner()
    result = runner.invoke(
        dataset_group,
        [
            "new",
            name,
            "--rows-file", str(rows_path),
            "--schema", str(schema_path),
            "--description", "list summary test",
        ],
    )
    assert result.exit_code == 0, result.output


def test_dataset_list_includes_row_quality_for_manual_dataset(tmp_path: Path) -> None:
    """``dataset list --json`` must surface a ``row_quality`` block on
    every dataset, populated from patches_with_survival on each row."""
    rows = [
        {
            "row_id": "ra",
            "patches_with_survival": [
                {"survival_state": "alive_on_path", "retention_fraction": 1.0},
                {"survival_state": "alive_transformed", "retention_fraction": 1.0},
            ],
        },
        {
            "row_id": "rb",
            "patches_with_survival": [
                {"survival_state": "lost", "retention_fraction": None,
                 "lost_kind": "file_deleted",
                 "lost_at_commit_sha": "abc1234"},
                {"survival_state": "alive_transformed", "retention_fraction": 0.5},
            ],
        },
        {
            "row_id": "rc",
            # No survival info on this row.
            "patches_with_survival": [],
        },
    ]
    rows_file = tmp_path / "rows.jsonl"
    schema_file = tmp_path / "schema.json"
    rows_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    _seed_via_cli("rq-list", rows_file, schema_file)

    runner = CliRunner()
    result = runner.invoke(main, ["--json", "dataset", "list"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    by_name = {item["name"]: item for item in payload["datasets"]}
    assert "rq-list" in by_name
    rq = by_name["rq-list"].get("row_quality")
    assert rq is not None, f"row_quality missing on dataset payload: {by_name['rq-list']}"

    assert rq["total_rows"] == 3
    # ra (2 alive) + rb (1 lost + 1 alive) ⇒ 2 rows have anchored patches.
    assert rq["rows_with_anchored_patches"] == 2
    assert rq["rows_with_lost_patches"] == 1
    # ra is fully alive (both alive_on_path/alive_transformed).
    assert rq["rows_fully_alive"] == 1
    # mean retention: ra=1.0, rb mean=(0.5)/1 = 0.5 (lost is None-skipped),
    # rc has no retention. Mean of [1.0, 0.5] = 0.75
    assert rq["mean_row_retention"] is not None
    assert 0.7 < rq["mean_row_retention"] < 0.8
    dist = rq["survival_distribution"]
    assert dist["alive_on_path"] == 1
    assert dist["alive_transformed"] == 2
    assert dist["lost"] == 1
