from __future__ import annotations

from opentraces.core.datasets import (
    append_rows,
    create_dataset,
    dataset_path,
    read_row_index,
    rebuild_row_index,
)


def test_row_index_rebuild_recovers_dedupe_state_from_public_jsonl():
    create_dataset(
        "rebuild-intents",
        workflow_skill="rebuild-curator",
        workflow_digest="sha256:workflow",
    )
    row = {
        "source_trace_id": "trace-1",
        "source_unit_id": "tu:trace-1:trace",
        "summary": "Recover me from public JSONL.",
    }
    append_rows("rebuild-intents", [row], run_id="run_seed")
    original = read_row_index("rebuild-intents")[0]

    row_index_path = dataset_path("rebuild-intents") / ".opentraces" / "row_index.jsonl"
    row_index_path.unlink()

    rebuilt = rebuild_row_index("rebuild-intents")
    assert rebuilt.rebuilt_count == 1
    assert read_row_index("rebuild-intents")[0].identity_hash == original.identity_hash

    duplicate = append_rows("rebuild-intents", [row], run_id="run_after_rebuild")
    assert duplicate.appended_count == 0
    assert duplicate.duplicate_count == 1
