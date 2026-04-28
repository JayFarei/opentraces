"""Plan 56 acceptance harness proof."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_plan056_trace_query_acceptance_harness_local_json():
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "tests/integration/harness/plan056_trace_query_acceptance.py"),
            "--mode",
            "local",
            "--json",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)

    assert payload["status"] == "ok"
    assert payload["mode"] == "local"
    assert payload["candidate_counts"]["skill"] == 1
    assert payload["candidate_counts"]["signal_gated"] == 1
    assert payload["selected_ids"]["trace_id"] == "trace-plan056-acceptance"
    assert payload["byte_ratios"]["full_to_packet"] > 1
    assert payload["byte_ratios"]["full_to_slice"] > 1
    assert payload["index_rebuild_digests"]["equivalent"] is True
    assert payload["no_transcript_text"] is True
    assert all(command["exit_code"] == 0 for command in payload["command_transcripts"])
