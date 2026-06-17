"""Mechanism tests for the Click-free staged progress reporter (issue #88).

The reporter is the shared facility behind the ``--progress`` CLI contract:
throttled stage/heartbeat emission, a frozen JSONL event shape, a background
heartbeat daemon that meets the ≤10s SLO even across a single blocking call,
and a per-stage telemetry block for the final payload. Click never enters this
layer — these tests import ``opentraces.core.progress`` directly.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from opentraces.core.progress import (
    NullProgress,
    ProgressReporter,
    render_json,
    render_plain,
)


class _FakeClock:
    """Deterministic monotonic stand-in; the test drives ``t`` by hand."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


_EVENT_KEYS = {"event", "command", "stage", "elapsed_ms", "stage_elapsed_ms", "counters"}


def test_throttle_and_shape():
    clock = _FakeClock()
    events: list[dict] = []
    reporter = ProgressReporter(
        "trace index rebuild",
        emit=events.append,
        clock=clock,
        heartbeat_interval=5.0,
        enable_heartbeat=False,  # isolate throttling from the background beat
    )

    reporter.set_total(traces_total=3)
    reporter.stage("acquiring_lock")  # force-emit on stage entry -> event[0]
    assert len(events) == 1

    reporter.advance(traces_seen=1)  # t=0, < interval -> throttled, no emit
    clock.t = 2.0
    reporter.advance(traces_seen=2)  # 2 < 5 -> throttled
    assert len(events) == 1

    clock.t = 6.0
    reporter.advance(traces_seen=3)  # 6 >= 5 -> emit -> event[1]
    assert len(events) == 2

    reporter.stage("building_snapshot")  # stage change always force-emits -> event[2]
    assert len(events) == 3

    reporter.done()

    # Exact frozen event shape (the agent contract).
    ev = events[1]
    assert set(ev) == _EVENT_KEYS
    assert ev["event"] == "progress"
    assert ev["command"] == "trace index rebuild"
    assert ev["stage"] == "acquiring_lock"
    assert ev["elapsed_ms"] == 6000
    assert ev["stage_elapsed_ms"] == 6000
    assert ev["counters"] == {"traces_total": 3, "traces_seen": 3}

    # plain renders human text mentioning the stage; json round-trips.
    line = render_plain(ev)
    assert isinstance(line, str) and "acquiring_lock" in line
    assert json.loads(render_json(ev)) == ev

    # telemetry() exposes per-stage durations for the final payload.
    tele = reporter.telemetry()
    stages = [s["stage"] for s in tele]
    assert "acquiring_lock" in stages
    assert "building_snapshot" in stages
    assert all("duration_ms" in s for s in tele)
    # The acquiring_lock stage spanned t=0..6 before the change to building_snapshot.
    acq = next(s for s in tele if s["stage"] == "acquiring_lock")
    assert acq["duration_ms"] == 6000


def test_null_progress_is_silent():
    events: list[dict] = []
    null = NullProgress()
    # Every method is a no-op and accepts the same calls as the real reporter.
    null.set_total(traces_total=9)
    null.stage("building_snapshot")
    null.advance(traces_seen=1)
    null.done()
    assert events == []
    assert null.telemetry() == []
    # Context-manager parity.
    with NullProgress() as n:
        n.stage("x")
        n.advance(y=1)
    assert n.telemetry() == []


def test_background_heartbeat_blocking_stage():
    """A stage that blocks WITHOUT calling advance() still beats within the SLO.

    Uses a real thread + tiny interval so a single blocking call (mirroring the
    flock wait / vacuum) emits at least one heartbeat for the active stage, and
    the daemon thread is joined on done().
    """

    events: list[dict] = []
    guard = threading.Lock()

    def emit(ev: dict) -> None:
        with guard:
            events.append(ev)

    reporter = ProgressReporter(
        "trace index rebuild",
        emit=emit,
        heartbeat_interval=0.05,
        enable_heartbeat=True,
    )
    reporter.stage("building_snapshot")
    # Block for several intervals WITHOUT advancing — only the background
    # heartbeat can keep emitting here.
    time.sleep(0.3)
    reporter.done()

    with guard:
        beats = [e for e in events if e["stage"] == "building_snapshot"]
    # stage() force-emits once; the background thread must add >= 1 more.
    assert len(beats) >= 2
    # The daemon thread is stopped + joined by done().
    assert not reporter.heartbeat_alive()


def test_context_manager_stops_heartbeat_on_exception():
    events: list[dict] = []
    guard = threading.Lock()

    def emit(ev: dict) -> None:
        with guard:
            events.append(ev)

    reporter = ProgressReporter(
        "cmd",
        emit=emit,
        heartbeat_interval=0.02,
        enable_heartbeat=True,
    )
    with pytest.raises(RuntimeError):
        with reporter:
            reporter.stage("building_snapshot")
            time.sleep(0.05)
            raise RuntimeError("boom")
    # Even on exception, the heartbeat thread is joined.
    assert not reporter.heartbeat_alive()
