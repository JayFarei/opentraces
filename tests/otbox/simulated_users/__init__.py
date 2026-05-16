"""Simulated-user runner + scenario pipeline (plan 071).

Declarative TOML scenarios drive a real (or echo meta-test) agent
binary through an interactive prompt sequence via tmux/PTY, then the
resulting box state is snapshotted into a committed capture artifact.

This package is structured around three collaborating surfaces:

  * ``scenario.py`` (Agent B) — TOML schema + parser + validator.
    ``available_scenarios()`` / ``load_scenario(name)`` mirror the
    journey catalogue ergonomic. Templates live under ``templates/``
    and are copied into the box's project dir before scenario start.
  * ``runner.py`` (Agent A) — PTY/tmux ``run_simulated_session``
    primitive + :class:`Turn` + :class:`ScenarioResult` dataclasses.
  * ``cli.py`` integration (Agent C) — the ``capture-refresh`` CLI
    surface that resolves a base checkpoint, applies the scenario's
    template, runs the scenario, and snapshots the result.

------------------------------------------------------------------------
Runner API contract (Agent A — stable surface for Agents B + C)
------------------------------------------------------------------------

::

    @dataclass
    class Turn:
        prompt: str
        expect_regex: str
        timeout_s: float = 60.0

    @dataclass
    class ScenarioResult:
        verdict: str            # "PASS" | "FAIL" | "SKIP"
        binary_path: str        # absolute path to the resolved binary
        binary_version: str     # first non-empty line of `<bin> --version`
        turn_count: int         # how many turns completed before exit
        pane_log_path: str      # always written, even on FAIL / SKIP
        error_message: str = ""
        pane_excerpt: str = ""  # last ~2000 chars of pane content

    def run_simulated_session(
        driver: Driver,
        box: Box,
        binary: str,
        turns: list[Turn],
        *,
        initial_state_dir: Path | None = None,
        output_dir: Path,
        env_extra: dict[str, str] | None = None,
    ) -> ScenarioResult: ...

Verdict semantics
-----------------
  * ``PASS`` — every turn's ``expect_regex`` matched (case-insensitive)
    inside its ``timeout_s`` window.
  * ``FAIL`` — at least one turn failed (timeout, regex miss, runtime
    error, or initial-state copy failure). ``turn_count`` reflects how
    many turns SUCCEEDED before the failure.
  * ``SKIP`` — preconditions unmet (no ``tmux``, binary missing /
    non-executable). The caller decides whether SKIP is fatal.

Re-exports
----------
The :class:`Turn` dataclass is owned by ``runner.py``; ``scenario.py``
imports it from here so scenario consumers can pull both ``Turn`` and
the Scenario types from one module.
"""

from __future__ import annotations

from .runner import ScenarioResult, Turn, run_simulated_session

__all__ = [
    "Turn",
    "ScenarioResult",
    "run_simulated_session",
]
