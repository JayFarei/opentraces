from __future__ import annotations

import gc
import json
import os
import statistics
import subprocess
import threading
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .models import PerfResult, PerfSample, PerfScenario, percentile


# 10 ms polling × ~100 ``ps`` subprocess calls per second interacts
# pathologically with ``tracemalloc`` inside ``_measure_python_memory_once``:
# a single TUI scenario's memory measurement took ~22 s at 10 ms, versus
# ~1.4 s at 50 ms (Python 3.14, macOS 15). The 15× slowdown made TUI
# ``repetitions>1`` run beyond a reasonable smoke-lane budget. Peak RSS
# tracking across a sub-second callback does not need finer granularity.
_RSS_POLL_INTERVAL_S = 0.05


@dataclass(frozen=True)
class CommandPlan:
    cmd: list[str]
    cwd: Path
    env: dict[str, str]
    metadata: dict[str, Any]


def measure_python_factory(
    scenario: PerfScenario,
    factory: Callable[[int], tuple[Callable[[], Any], dict[str, Any]]],
) -> PerfResult:
    samples: list[PerfSample] = []
    total_runs = scenario.warmups + scenario.repetitions
    for run_idx in range(total_runs):
        latency_callback, metadata = factory(run_idx * 2)
        duration_ms = _measure_python_duration_once(latency_callback)
        memory_callback, _ = factory(run_idx * 2 + 1)
        sample = _measure_python_memory_once(memory_callback, metadata)
        sample.duration_ms = duration_ms
        if run_idx >= scenario.warmups:
            samples.append(sample)
    return _summarize(samples)


def measure_command_factory(
    scenario: PerfScenario,
    factory: Callable[[int], CommandPlan],
) -> PerfResult:
    samples: list[PerfSample] = []
    total_runs = scenario.warmups + scenario.repetitions
    for run_idx in range(total_runs):
        plan = factory(run_idx)
        sample = _measure_command_once(plan)
        if run_idx >= scenario.warmups:
            samples.append(sample)
    return _summarize(samples)


def write_artifact(
    artifacts_dir: Path,
    scenario: PerfScenario,
    result: PerfResult,
    *,
    budget_updated: bool = False,
) -> None:
    payload = {
        "scenario": {
            "name": scenario.name,
            "domain": scenario.domain,
            "lane": scenario.lane,
            "adapter": scenario.adapter,
            "target": scenario.target,
            "fixture": scenario.fixture,
            "tier": scenario.tier,
            "repetitions": scenario.repetitions,
            "warmups": scenario.warmups,
            "params": scenario.params,
            "budget": scenario.budget.to_mapping(),
        },
        "result": result.to_dict(),
        "budget_updated": budget_updated,
    }
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / f"{scenario.name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )

    summary_path = artifacts_dir / "summary.json"
    existing: dict[str, Any]
    if summary_path.is_file():
        try:
            existing = json.loads(summary_path.read_text())
        except ValueError:
            existing = {}
    else:
        existing = {}
    existing[scenario.name] = {
        "domain": scenario.domain,
        "lane": scenario.lane,
        "target": scenario.target,
        "tier": scenario.tier,
        "cold_ms": result.cold_ms,
        "p50_ms": result.p50_ms,
        "p95_ms": result.p95_ms,
        "peak_rss_mb": result.peak_rss_mb,
        "python_heap_peak_mb": result.python_heap_peak_mb,
        "max_rss_delta_mb": result.max_rss_delta_mb,
        "extra": result.extra,
        "budget_updated": budget_updated,
    }
    summary_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")


def _measure_python_duration_once(callback: Callable[[], Any]) -> float:
    gc.collect()
    start = time.perf_counter_ns()
    callback()
    return (time.perf_counter_ns() - start) / 1_000_000


def _measure_python_memory_once(callback: Callable[[], Any], metadata: dict[str, Any]) -> PerfSample:
    gc.collect()
    rss_before = _rss_mb(os.getpid())
    sampler = _RssSampler(os.getpid())
    tracemalloc.start()
    try:
        with sampler:
            callback()
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    rss_after = _rss_mb(os.getpid())
    return PerfSample(
        duration_ms=0.0,
        rss_before_mb=rss_before,
        rss_after_mb=rss_after,
        peak_rss_mb=sampler.peak_mb,
        python_heap_peak_mb=(peak / (1024 * 1024)),
        metadata=metadata,
    )


def _measure_command_once(plan: CommandPlan) -> PerfSample:
    start = time.perf_counter_ns()
    proc = subprocess.Popen(
        plan.cmd,
        cwd=str(plan.cwd),
        env=plan.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    sampler = _RssSampler(proc.pid)
    with sampler:
        stdout, _ = proc.communicate()
    duration_ms = (time.perf_counter_ns() - start) / 1_000_000
    metadata = dict(plan.metadata)
    metadata["returncode"] = proc.returncode
    if stdout:
        metadata["stdout_tail"] = "\n".join(stdout.strip().splitlines()[-8:])
    if proc.returncode != 0:
        raise AssertionError(
            f"Command failed for {metadata.get('scenario', plan.cmd[0])}: "
            f"exit={proc.returncode}\n{metadata.get('stdout_tail', '')}"
        )
    return PerfSample(
        duration_ms=duration_ms,
        peak_rss_mb=sampler.peak_mb,
        metadata=metadata,
    )


def _summarize(samples: list[PerfSample]) -> PerfResult:
    durations = [sample.duration_ms for sample in samples]
    peak_rss_values = [sample.peak_rss_mb for sample in samples if sample.peak_rss_mb is not None]
    heap_values = [
        sample.python_heap_peak_mb
        for sample in samples
        if sample.python_heap_peak_mb is not None
    ]
    rss_deltas = [sample.rss_delta_mb for sample in samples if sample.rss_delta_mb is not None]
    return PerfResult(
        samples=samples,
        cold_ms=durations[0],
        p50_ms=percentile(durations, 0.50),
        p95_ms=percentile(durations, 0.95),
        mean_ms=float(statistics.mean(durations)),
        peak_rss_mb=max(peak_rss_values) if peak_rss_values else None,
        python_heap_peak_mb=max(heap_values) if heap_values else None,
        max_rss_delta_mb=max(rss_deltas) if rss_deltas else None,
    )


def _rss_mb(pid: int) -> float | None:
    try:
        out = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(pid)],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not out:
        return None
    try:
        return int(out) / 1024.0
    except ValueError:
        return None


class _RssSampler:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.peak_mb = _rss_mb(pid)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_RssSampler":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
        current = _rss_mb(self.pid)
        if current is not None:
            self.peak_mb = max(self.peak_mb or 0.0, current)

    def _run(self) -> None:
        while not self._stop.wait(_RSS_POLL_INTERVAL_S):
            current = _rss_mb(self.pid)
            if current is None:
                break
            self.peak_mb = max(self.peak_mb or 0.0, current)
