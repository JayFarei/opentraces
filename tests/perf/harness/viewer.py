from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .models import PerfResult, PerfSample, PerfScenario, REPO_ROOT


_MB = 1024 * 1024
VIEWER_ROOT = REPO_ROOT / "web" / "viewer"


def run_viewer_scenario(scenario: PerfScenario) -> PerfResult:
    if shutil.which("npm") is None:
        pytest.skip("npm is not available")
    if not (VIEWER_ROOT / "node_modules").is_dir():
        pytest.skip("viewer dependencies are not installed; run `npm ci` in web/viewer")

    benchmark = str(scenario.params["benchmark"])
    iterations = int(scenario.params.get("iterations", 12))
    warmup = int(scenario.params.get("warmup", 3))
    cmd = [
        "npm",
        "run",
        "--silent",
        "perf:runner",
        "--",
        "--benchmark",
        benchmark,
        "--iterations",
        str(iterations),
        "--warmup",
        str(warmup),
    ]
    env = os.environ.copy()
    env.setdefault("CI", "1")
    env.setdefault("FORCE_COLOR", "0")
    proc = subprocess.run(
        cmd,
        cwd=str(VIEWER_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"viewer perf runner failed for {scenario.name}\n"
            f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        )

    try:
        payload = json.loads(_extract_json(proc.stdout))
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"viewer perf runner emitted invalid JSON for {scenario.name}\n{proc.stdout}"
        ) from exc

    results = payload.get("results") or []
    if len(results) != 1:
        raise AssertionError(
            f"viewer perf runner returned {len(results)} result(s) for {scenario.name}; expected 1"
        )
    entry = results[0]
    initial = entry["metrics"]["initialMs"]
    update = entry["metrics"]["updateMs"]
    rss = entry["memory"]["rss"]
    heap_used = entry["memory"]["heapUsed"]

    peak_rss_mb = _to_mb(rss.get("peakMax"))
    peak_rss_delta_mb = _to_mb(rss.get("peakDeltaMax"))
    peak_heap_used_mb = _to_mb(heap_used.get("peakMax"))
    retained_heap_used_mb = _to_mb(heap_used.get("retainedMax"))
    extra = {
        "benchmark_id": entry.get("id"),
        "initial_p95_ms": float(initial["p95"]),
        "update_p95_ms": float(update["p95"]),
        "peak_heap_used_mb": peak_heap_used_mb,
        "retained_heap_used_mb": retained_heap_used_mb,
        "node_version": payload.get("nodeVersion"),
        "meta": entry.get("meta", {}),
    }
    return PerfResult(
        samples=[
            PerfSample(
                duration_ms=float(update["mean"]),
                peak_rss_mb=peak_rss_mb,
                metadata={"benchmark": benchmark},
            )
        ],
        cold_ms=float(initial["p95"]),
        p50_ms=float(update["median"]),
        p95_ms=float(update["p95"]),
        mean_ms=float(update["mean"]),
        peak_rss_mb=peak_rss_mb,
        python_heap_peak_mb=None,
        max_rss_delta_mb=peak_rss_delta_mb,
        extra=extra,
    )


def _to_mb(value: int | float | None) -> float | None:
    if value is None:
        return None
    return float(value) / _MB


def _extract_json(stdout: str) -> str:
    start = stdout.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON object found", stdout, 0)
    return stdout[start:].strip()
