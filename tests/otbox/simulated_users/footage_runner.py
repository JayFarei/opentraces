"""termctrl-backed journey-footage recorder (thin wrapper over ``drive``).

This module is the *visual* entry point onto the single termctrl drive
core in ``drive.py``. Stage 2 of the unify-on-terminal-control refactor
moved the drive loop + box prep out of here:

  * the termctrl drive core lives in :mod:`drive`
    (``drive.drive_session`` records the ``.termctrl`` byproduct and, when
    ``export_mp4=True``, renders the MP4),
  * the box-prep helpers live in the neutral :mod:`prep` module.

``record_simulated_session`` keeps its public signature and
``FootageResult`` shape unchanged (``tests/otbox/footage.py::_card_payload``
and ``to_dict`` depend on it). It calls ``drive.drive_session(...,
record_dir=footage_dir, export_mp4=True, scenario=scenario)`` and projects
the returned :class:`drive.SessionResult` 1:1 into ``FootageResult``,
then persists ``markers.json`` / ``result.json`` exactly as before.

Determinism note: footage is media, not an assertion artifact. The MP4 is
not byte-stable across runs/machines; ``result.json`` is stable-ish (it
records turn outcomes + marker names, not frame timing).
"""

from __future__ import annotations

import json
import shutil  # noqa: F401 - kept importable so tests can monkeypatch footage_runner.shutil.which
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box
from .drive import FootageTurnResult, drive_session
from .scenario import Turn

__all__ = [
    "FootageResult",
    "FootageTurnResult",
    "record_simulated_session",
]


# ---------------------------------------------------------------------------
# result dataclass — parallels ScenarioResult, adds footage-specific fields
# ---------------------------------------------------------------------------
@dataclass
class FootageResult:
    """Outcome of one termctrl-recorded session.

    Mirrors :class:`tests.otbox.simulated_users.runner.ScenarioResult`
    verdict semantics (``PASS`` / ``FAIL`` / ``SKIP``) and adds the
    footage-specific fields the gallery consumes.

    ``mp4_path`` / ``termctrl_path`` are absolute string paths (or ``""``
    when the recording was skipped before those artifacts existed).
    ``markers`` is the ordered list of marker names added during the run.
    """

    verdict: str
    binary_path: str
    binary_version: str
    agent: str | None
    scenario: str
    turn_count: int
    mp4_path: str
    termctrl_path: str
    markers: list[str] = field(default_factory=list)
    turns: list[FootageTurnResult] = field(default_factory=list)
    duration_s: float = 0.0
    cols: int = 110
    rows: int = 32
    fps: int = 20
    error_message: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# public entry point — thin wrapper over drive.drive_session
# ---------------------------------------------------------------------------
def record_simulated_session(
    driver: Driver,
    box: Box,
    binary: str,
    turns: list[Turn],
    *,
    initial_state_dir: Path | None = None,
    output_dir: Path,
    footage_dir: Path,
    env_extra: dict[str, str] | None = None,
    agent: str | None = None,
    scenario: str = "session",
    cols: int = 110,
    rows: int = 32,
    fps: int = 20,
) -> FootageResult:
    """Record a termctrl session of ``binary`` driven through ``turns``.

    The signature is the footage analogue of
    :func:`tests.otbox.simulated_users.runner.run_simulated_session`: same
    ``driver`` / ``box`` / ``binary`` / ``turns`` / ``initial_state_dir`` /
    ``output_dir`` / ``env_extra`` / ``agent`` contract, plus
    footage-specific keyword args.

    Parameters
    ----------
    footage_dir:
        Directory the ``.termctrl`` recording, ``<scenario>.mp4``,
        ``markers.json`` and ``result.json`` land in. Created if missing.
    scenario:
        Logical scenario name used for the recording/MP4 filenames and the
        gallery card title. Defaults to ``"session"``.
    cols / rows:
        PTY geometry for the recording.
    fps:
        Sampled frames-per-second for the exported MP4.

    Returns
    -------
    :class:`FootageResult`. The function never raises on a turn-level
    failure — a miss is encoded as ``verdict="FAIL"`` while the video is
    still produced. Setup failures (no termctrl, no binary, no ffmpeg)
    return cleanly as ``SKIP`` / ``FAIL``.
    """
    footage_dir.mkdir(parents=True, exist_ok=True)

    session_result = drive_session(
        driver,
        box,
        binary,
        turns,
        output_dir=output_dir,
        agent=agent,
        initial_state_dir=initial_state_dir,
        env_extra=env_extra,
        scenario=scenario,
        record_dir=footage_dir,
        export_mp4=True,
        cols=cols,
        rows=rows,
        fps=fps,
    )

    # Project the unified SessionResult 1:1 into the legacy FootageResult.
    result = FootageResult(
        verdict=session_result.verdict,
        binary_path=session_result.binary_path,
        binary_version=session_result.binary_version,
        agent=session_result.agent,
        scenario=session_result.scenario,
        turn_count=session_result.turn_count,
        mp4_path=session_result.mp4_path,
        termctrl_path=session_result.termctrl_path,
        markers=session_result.markers,
        turns=session_result.turns,
        duration_s=session_result.duration_s,
        cols=session_result.cols,
        rows=session_result.rows,
        fps=session_result.fps,
        error_message=session_result.error_message,
    )

    # --- persist markers.json + result.json --------------------------------
    try:
        (footage_dir / "markers.json").write_text(
            json.dumps(result.markers, indent=2) + "\n", encoding="utf-8"
        )
        (footage_dir / "result.json").write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass

    return result
