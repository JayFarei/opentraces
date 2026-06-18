"""Click-free staged progress reporter (issue #88).

The shared facility behind the ``--progress`` CLI contract. Long-running CLI
operations (today: ``trace index rebuild``) drive a :class:`ProgressReporter`
through named *stages*; the reporter emits a stable, agent-readable event for
every stage change, every throttled ``advance()``, and — crucially — a
background *heartbeat* for the active stage even when the operation is blocked
inside a single C call (the ``fcntl.flock`` wait, an FTS ``optimize``, or a
``vacuum``). That background beat is what lets the contract guarantee an
emission within the ≤10s SLO without sprinkling ``advance()`` calls through
otherwise-opaque blocking work.

Design constraints (so this stays reusable):

* **Click-free.** Nothing in this module imports Click. The CLI layer
  (:mod:`opentraces.cli._progress`) owns the sink (``click.echo(err=True)``),
  the TTY resolution, and the option wiring. Core code can therefore depend on
  this module without dragging in the CLI.
* **No-op default.** Every existing caller that does not pass a reporter gets a
  :class:`NullProgress`, which makes their behaviour byte-identical.
* **Injected clock.** ``clock`` defaults to :func:`time.monotonic` but is
  injectable so the throttling logic is testable with a fake clock.

The emitted event is a frozen contract (changing its keys is a contract break
that downstream agents would feel):

    {"event": "progress",
     "command": <str>,
     "stage": <str>,
     "elapsed_ms": <int>,        # since the reporter was created
     "stage_elapsed_ms": <int>,  # since the current stage began
     "counters": {<str>: <number>, ...}}
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Protocol, runtime_checkable

EmitSink = Callable[[dict[str, Any]], None]
Clock = Callable[[], float]

DEFAULT_HEARTBEAT_INTERVAL = 5.0  # seconds; comfortably inside the 10s SLO

PROGRESS_EVENT = "progress"
_EVENT_KEYS = ("event", "command", "stage", "elapsed_ms", "stage_elapsed_ms", "counters")


@runtime_checkable
class ProgressLike(Protocol):
    """The structural contract every reporter (real or null) satisfies."""

    def set_total(self, **totals: float) -> None: ...
    def stage(self, name: str, **counters: float) -> None: ...
    def advance(self, **counters: float) -> None: ...
    def done(self) -> None: ...
    def telemetry(self) -> list[dict[str, Any]]: ...


def render_json(event: dict[str, Any]) -> str:
    """Serialize one progress event to a single compact JSONL line."""

    return json.dumps(event, separators=(",", ":"), sort_keys=False)


def render_plain(event: dict[str, Any]) -> str:
    """Render one progress event as a human-readable single line."""

    counters = event.get("counters") or {}
    counter_text = " ".join(f"{k}={v}" for k, v in counters.items())
    elapsed = event.get("elapsed_ms", 0) / 1000.0
    head = f"[{elapsed:6.1f}s] {event.get('stage', '?')}"
    return f"{head}  {counter_text}".rstrip()


class NullProgress:
    """A reporter that does nothing — the default for every existing caller."""

    def set_total(self, **totals: float) -> None:  # noqa: D401 - no-op
        return None

    def stage(self, name: str, **counters: float) -> None:
        return None

    def advance(self, **counters: float) -> None:
        return None

    def done(self) -> None:
        return None

    def telemetry(self) -> list[dict[str, Any]]:
        return []

    def __enter__(self) -> "NullProgress":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class ProgressReporter:
    """A staged, throttled, heartbeat-backed progress reporter.

    Thread-safe: ``advance()``/``stage()`` from the main thread and the
    background heartbeat thread are serialized by one lock, so a half-written
    event line can never interleave with another.
    """

    def __init__(
        self,
        command: str,
        *,
        emit: EmitSink,
        clock: Clock = time.monotonic,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        enable_heartbeat: bool = True,
    ) -> None:
        self._command = command
        self._emit = emit
        self._clock = clock
        self._heartbeat_interval = max(0.001, float(heartbeat_interval))
        self._enable_heartbeat = enable_heartbeat

        self._lock = threading.Lock()
        self._start = clock()
        self._counters: dict[str, float] = {}
        self._current_stage: str | None = None
        self._stage_start = self._start
        self._last_emit_at = self._start
        self._closed = False

        # Per-stage telemetry records, finalized as stages end (and on done()).
        self._stage_records: list[dict[str, Any]] = []

        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    # -- internals (call with self._lock held) -----------------------------

    def _build_event_locked(self) -> dict[str, Any]:
        now = self._clock()
        return {
            "event": PROGRESS_EVENT,
            "command": self._command,
            "stage": self._current_stage or "",
            "elapsed_ms": int(round((now - self._start) * 1000)),
            "stage_elapsed_ms": int(round((now - self._stage_start) * 1000)),
            "counters": dict(self._counters),
        }

    def _emit_locked(self) -> None:
        if self._closed or self._current_stage is None:
            return
        event = self._build_event_locked()
        self._last_emit_at = self._clock()
        # The sink is invoked while holding the lock so concurrent emits from
        # the heartbeat thread cannot interleave a partial line.
        self._emit(event)

    def _finalize_stage_locked(self) -> None:
        if self._current_stage is None:
            return
        now = self._clock()
        self._stage_records.append(
            {
                "stage": self._current_stage,
                "duration_ms": int(round((now - self._stage_start) * 1000)),
                "counters": dict(self._counters),
            }
        )

    def _ensure_heartbeat_started(self) -> None:
        if not self._enable_heartbeat or self._heartbeat_thread is not None:
            return
        thread = threading.Thread(
            target=self._heartbeat_loop,
            name="opentraces-progress-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread = thread
        thread.start()

    def _heartbeat_loop(self) -> None:
        # Wakes every interval (real time). On each tick, if a stage is still
        # active and nothing has emitted within the interval (by the clock),
        # beat the current stage. ``Event.wait`` returns True only when stop is
        # set, so the loop exits promptly on done()/__exit__.
        while not self._stop.wait(self._heartbeat_interval):
            with self._lock:
                if self._closed or self._current_stage is None:
                    continue
                if (self._clock() - self._last_emit_at) >= self._heartbeat_interval:
                    self._emit_locked()

    # -- public API ---------------------------------------------------------

    def set_total(self, **totals: float) -> None:
        """Seed known totals (e.g. ``traces_total``) into the counter set.

        Totals are reported CLI-side; core stages only report observed work
        (``traces_seen``). This never emits on its own.
        """

        if not totals:
            return
        with self._lock:
            self._counters.update(totals)

    def stage(self, name: str, **counters: float) -> None:
        """Begin a new stage. Always force-emits (relabels the heartbeat)."""

        with self._lock:
            self._finalize_stage_locked()
            if counters:
                self._counters.update(counters)
            self._current_stage = name
            self._stage_start = self._clock()
            self._emit_locked()
        self._ensure_heartbeat_started()

    def advance(self, **counters: float) -> None:
        """Update counters; emit only if >= interval since the last emission."""

        with self._lock:
            if counters:
                self._counters.update(counters)
            if self._current_stage is None or self._closed:
                return
            if (self._clock() - self._last_emit_at) >= self._heartbeat_interval:
                self._emit_locked()

    def done(self) -> None:
        """Finalize the last stage and stop the heartbeat thread (idempotent)."""

        self._stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.5, self._heartbeat_interval * 2))
        with self._lock:
            if not self._closed:
                self._finalize_stage_locked()
                self._closed = True
                self._current_stage = None

    def telemetry(self) -> list[dict[str, Any]]:
        """Per-stage durations + counters for the final result payload."""

        with self._lock:
            return [dict(record) for record in self._stage_records]

    def heartbeat_alive(self) -> bool:
        """Test helper: True while the background heartbeat thread runs."""

        thread = self._heartbeat_thread
        return bool(thread is not None and thread.is_alive())

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> "ProgressReporter":
        return self

    def __exit__(self, *exc: object) -> None:
        # Stop the heartbeat thread even when the body raised.
        self.done()
