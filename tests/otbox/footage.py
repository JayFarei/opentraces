"""Journey-footage orchestrator: scenario → recorded box → MP4 → gallery.

This is the operator-facing layer over
:func:`tests.otbox.simulated_users.footage_runner.record_simulated_session`.
It mirrors ``cmd_capture_refresh``'s "resolve box → load scenario → expand
turns → run" pattern, but the heavy lifting (PTY drive, video export) lives
in the footage runner so this module stays a thin coordinator.

Public surface:
  * :func:`record_scenario` — record one scenario for one harness.
  * :func:`record_all` — record every catalogue scenario for its native
    agent (SKIPping cleanly where the binary is absent).
  * :func:`build_gallery` — render the self-contained ``gallery.html``.

Footage lands under ``tests/otbox/captures/<scenario>/footage/<agent>/`` so
it sits next to the assertion-grade capture-refresh snapshot without
colliding with it. The aggregate gallery lands under
``tests/otbox/captures/_footage/``.
"""

from __future__ import annotations

import json
from pathlib import Path

from .env import REPO_ROOT
from .footage_gallery import render_gallery_html
from .simulated_users.footage_runner import (
    FootageResult,
    record_simulated_session,
)
from .simulated_users.scenario import (
    Scenario,
    available_scenarios,
    load_scenario,
    scenario_digest,
)

CAPTURES_ROOT = REPO_ROOT / "tests" / "otbox" / "captures"
FOOTAGE_GALLERY_DIR = CAPTURES_ROOT / "_footage"

# Map a --harness override / scenario agent to the concrete binary lookup.
# echo is the synthetic in-tree binary; the rest resolve on PATH.
_ECHO_BINARY = (
    REPO_ROOT / "tests" / "otbox" / "simulated_users" / "_echo_binary.py"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _agent_for(scenario: Scenario, harness: str | None) -> str:
    """Resolve the agent name to drive: explicit ``harness`` wins."""
    return harness or scenario.agent


def _binary_for(scenario: Scenario, agent: str) -> str | None:
    """Resolve the binary path for ``agent`` (echo → in-tree synthetic)."""
    import shutil

    if agent == "echo":
        return str(_ECHO_BINARY) if _ECHO_BINARY.exists() else None
    # codex scenarios declare binary_name="codex" etc.; when the harness was
    # overridden we still trust the scenario's declared binary_name when the
    # override matches the scenario agent, else fall back to the agent name.
    binary_name = (
        scenario.binary_name if agent == scenario.agent else agent
    )
    return shutil.which(binary_name)


def _footage_dir(
    scenario_name: str,
    agent: str,
    *,
    captures_root: Path | None = None,
) -> Path:
    return (captures_root or CAPTURES_ROOT) / scenario_name / "footage" / agent


# ---------------------------------------------------------------------------
# record one scenario
# ---------------------------------------------------------------------------
def record_scenario(
    scenario_name: str,
    *,
    harness: str | None = None,
    fps: int = 20,
    cols: int = 110,
    rows: int = 32,
) -> FootageResult:
    """Record footage for ``scenario_name`` against one harness.

    Resolves a fresh box from the scenario's base checkpoint, overlays the
    scenario's initial-state template, expands its turns, drives the agent
    through ``record_simulated_session``, and lands the artifacts under
    ``tests/otbox/captures/<scenario>/footage/<agent>/``.

    Returns the :class:`FootageResult`. A missing binary / missing termctrl
    / missing ffmpeg all yield a SKIP verdict (never raises).
    """
    from .checkpoints import resolve_checkpoint
    from .drivers import get_driver

    scenario = load_scenario(scenario_name)
    agent = _agent_for(scenario, harness)
    footage_dir = _footage_dir(scenario.name, agent)
    footage_dir.mkdir(parents=True, exist_ok=True)

    binary_path = _binary_for(scenario, agent)
    if binary_path is None:
        # SKIP before we even fork a box — nothing to record. Still write a
        # result.json so the gallery can show a grey SKIP chip.
        result = FootageResult(
            verdict="SKIP",
            binary_path="",
            binary_version="",
            agent=agent,
            scenario=scenario.name,
            turn_count=0,
            mp4_path="",
            termctrl_path="",
            error_message=(
                f"binary not found for agent {agent!r} "
                f"(scenario {scenario.name!r})"
            ),
            fps=fps,
            cols=cols,
            rows=rows,
        )
        _persist_skip_result(footage_dir, result, scenario)
        return result

    driver = get_driver("local")
    cp_result = resolve_checkpoint(driver, "c-installed-source")
    box = cp_result.box

    # Overlay the scenario's initial-state template into box.project, exactly
    # as cmd_capture_refresh does (the runner stays focused on the drive).
    template_dir = scenario.initial_state.template_dir
    if template_dir is not None and template_dir.exists():
        import shutil as _shutil

        for entry in template_dir.iterdir():
            dest = box.project / entry.name
            if entry.is_dir():
                _shutil.copytree(entry, dest, dirs_exist_ok=True)
            else:
                _shutil.copy2(entry, dest)

    output_dir = box.logs / "footage" / scenario.name
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = record_simulated_session(
            driver,
            box,
            binary_path,
            scenario.turns,
            initial_state_dir=None,
            output_dir=output_dir,
            footage_dir=footage_dir,
            agent=agent,
            scenario=scenario.name,
            cols=cols,
            rows=rows,
            fps=fps,
        )
    finally:
        # The footage is in footage_dir now; the box was scratch.
        try:
            driver.teardown(box)
        except Exception:  # noqa: BLE001 - teardown failures are non-fatal
            pass

    # Stamp scenario provenance alongside the runner-written result.json.
    _stamp_provenance(footage_dir, scenario)
    return result


def _persist_skip_result(
    footage_dir: Path, result: FootageResult, scenario: Scenario
) -> None:
    """Write result.json/markers.json for a pre-fork SKIP."""
    try:
        (footage_dir / "markers.json").write_text("[]\n", encoding="utf-8")
        (footage_dir / "result.json").write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    _stamp_provenance(footage_dir, scenario)


def _stamp_provenance(footage_dir: Path, scenario: Scenario) -> None:
    try:
        (footage_dir / "scenario.json").write_text(
            json.dumps(
                {
                    "name": scenario.name,
                    "description": scenario.description,
                    "agent": scenario.agent,
                    "binary_name": scenario.binary_name,
                    "turn_count": len(scenario.turns),
                    "scenario_digest": scenario_digest(scenario),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# record every scenario
# ---------------------------------------------------------------------------
# Scenarios that need a pre-seeded bucket trace + a {trace_id} template context.
# capture-refresh provides both (it seeds a real trace and expands the template),
# and now produces footage too — so their footage comes from
# `make capture-refresh SCENARIO=<name>`, not the standalone footage sweep, which
# drives a clean c-installed-source box and would fail mid-sequence (e.g. on
# `/ot-trace {trace_id}` with no captured trace).
_NEEDS_SEEDED_BUCKET = frozenset({"pi-tui-slash-commands-bucket"})


def record_all(
    *,
    harnesses: list[str] | None = None,
    fps: int = 20,
) -> list[FootageResult]:
    """Record every catalogue scenario for its native agent.

    When ``harnesses`` is given, only scenarios whose agent is in that set
    are recorded. Otherwise every scenario runs against its declared agent.
    Binaries that are absent yield a clean SKIP — the sweep never fails.
    """
    results: list[FootageResult] = []
    for summary in available_scenarios():
        name = summary.get("name")
        if not name or summary.get("error"):
            continue
        if name in _NEEDS_SEEDED_BUCKET:
            continue  # seeded-bucket scenario; footage comes from capture-refresh
        agent = summary.get("agent")
        if harnesses is not None and agent not in harnesses:
            continue
        try:
            result = record_scenario(name, fps=fps)
        except Exception as exc:  # noqa: BLE001 - one bad scenario never sinks the sweep
            result = FootageResult(
                verdict="FAIL",
                binary_path="",
                binary_version="",
                agent=agent,
                scenario=str(name),
                turn_count=0,
                mp4_path="",
                termctrl_path="",
                error_message=f"record_scenario raised: {exc}",
                fps=fps,
            )
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# gallery
# ---------------------------------------------------------------------------
def build_gallery(
    results: list[FootageResult],
    *,
    captures_root: Path | None = None,
) -> Path:
    """Render ``tests/otbox/captures/_footage/gallery.html`` + ``.json``.

    The gallery is a self-contained single HTML file referencing each MP4
    by a path relative to the gallery directory. Returns the gallery path.
    """
    gallery_dir = (
        (captures_root / "_footage") if captures_root is not None
        else FOOTAGE_GALLERY_DIR
    )
    gallery_dir.mkdir(parents=True, exist_ok=True)
    gallery_html = gallery_dir / "gallery.html"
    gallery_json = gallery_dir / "gallery.json"

    cards = [
        _card_payload(r, captures_root=captures_root, gallery_dir=gallery_dir)
        for r in results
    ]
    gallery_json.write_text(
        json.dumps({"cards": cards}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    gallery_html.write_text(
        render_gallery_html(cards), encoding="utf-8"
    )
    return gallery_html


def _card_payload(
    result: FootageResult,
    *,
    captures_root: Path | None = None,
    gallery_dir: Path | None = None,
) -> dict:
    """Flatten a FootageResult into a gallery card dict.

    ``mp4_rel`` is the MP4 path relative to the gallery dir so the HTML is
    portable: open it anywhere under the repo and the videos resolve.
    """
    scenario = result.scenario
    agent = result.agent or "unknown"
    footage_dir = _footage_dir(
        scenario, agent, captures_root=captures_root
    )

    description = ""
    scenario_meta = footage_dir / "scenario.json"
    if scenario_meta.exists():
        try:
            description = json.loads(scenario_meta.read_text()).get(
                "description", ""
            )
        except (OSError, json.JSONDecodeError):
            description = ""

    mp4_rel = ""
    if result.mp4_path and Path(result.mp4_path).exists():
        try:
            import os as _os

            mp4_rel = _os.path.relpath(
                Path(result.mp4_path).resolve(),
                (gallery_dir or FOOTAGE_GALLERY_DIR).resolve(),
            )
        except ValueError:
            mp4_rel = result.mp4_path

    return {
        "scenario": scenario,
        "agent": agent,
        "description": description,
        "verdict": result.verdict,
        "turn_count": result.turn_count,
        "duration_s": result.duration_s,
        "binary_version": result.binary_version,
        "error_message": result.error_message,
        "mp4_rel": mp4_rel,
        "markers": list(result.markers),
        "turns": [
            {
                "index": t.index,
                "prompt": t.prompt,
                "matched": t.matched,
                "elapsed_s": t.elapsed_s,
            }
            for t in result.turns
        ],
    }


__all__ = [
    "record_scenario",
    "record_all",
    "build_gallery",
    "CAPTURES_ROOT",
    "FOOTAGE_GALLERY_DIR",
]
