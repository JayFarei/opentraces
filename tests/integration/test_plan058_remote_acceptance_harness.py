from __future__ import annotations

import json
import subprocess
import sys


def test_plan058_fake_acceptance_harness_reports_required_capabilities():
    result = subprocess.run(
        [
            sys.executable,
            "tests/integration/harness/plan058_remote_acceptance.py",
            "--mode",
            "fake",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["status"] == "ok"
    assert payload["published"]["attempts"] == 2
    assert payload["published"]["new_row_count"] == 1
    assert payload["check_only"]["check_only"] is True
    assert payload["no_control_plane_leak"] is True
    assert payload["doctor"]["status"] == "ok"
    assert payload["pull"]["imported_count"] >= 1
    assert payload["no_write_access"]["classified"] is True
    assert payload["withdrawal"]["target"] == "row"
