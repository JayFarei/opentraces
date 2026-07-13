"""Public contract tests for portable Capture orchestration (A3)."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from opentraces.capture import Capture, CapturePlan


def _git_project(root: Path) -> Path:
    root.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "capture-test@opentraces.local"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "capture-test"],
        cwd=root,
        check=True,
    )
    (root / "README.md").write_text("capture fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "seed"],
        cwd=root,
        check=True,
    )
    return root


def test_killed_required_source_is_persisted_as_partial_before_deadline(
    tmp_path: Path,
) -> None:
    """A real killed source can never be reported as a thinner complete."""
    project = _git_project(tmp_path / "project")
    result_dir = tmp_path / "capture-result"
    session = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("telemetry",),
            required_sources=("telemetry",),
            observer_version="0.4.9-observer",
            product_under_test_version="0.4.8-product",
            result_dir=result_dir,
        )
    )

    assert session.bindings.otlp_endpoint.startswith("http://127.0.0.1:")
    assert session.interrupt("telemetry") is True

    started = time.monotonic()
    result = session.finish(deadline=started + 1.0)

    assert time.monotonic() - started < 1.5
    assert result.completeness == "partial"
    assert result.source("telemetry").status == "unavailable"
    assert result.source("telemetry").completeness == "missing"
    assert result.view("model_boundary").completeness == "missing"
    assert "source process exited" in result.source("telemetry").limitations[0]
    assert result.observer_version == "0.4.9-observer"
    assert result.product_under_test_version == "0.4.8-product"

    frozen = json.loads((result_dir / "capture_result.json").read_text())
    assert frozen == result.to_dict()

