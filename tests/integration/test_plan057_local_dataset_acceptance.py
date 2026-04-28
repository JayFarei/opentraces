from __future__ import annotations

from tests.integration.harness.plan057_local_dataset_acceptance import run_local


def test_plan057_local_dataset_acceptance_harness_reports_machine_readable_summary():
    payload = run_local()

    assert payload["status"] == "ok"
    assert payload["counts"]["dry_run"] == {
        "would_append": 1,
        "deduped": 1,
        "validation_errors": 1,
    }
    assert payload["counts"]["real_run"] == {
        "appended": 1,
        "deduped": 1,
        "validation_errors": 1,
    }
    assert payload["counts"]["repeat_run"] == {
        "appended": 0,
        "deduped": 2,
    }
    assert payload["row_index_rebuild_digests"]["rebuilt_count"] == 1
    assert payload["row_index_rebuild_digests"]["equivalent"] is True
    assert payload["schedule_status"]["enabled"] is True
    assert payload["public_data_file_digests"]["export_matches_train"] is True
    assert all(command["exit_code"] == 0 for command in payload["command_transcripts"])
