"""Default-CI-safe Pi simulated-user lane tests (plan 091)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.otbox.checkpoints import REGISTRY
from tests.otbox.simulated_users.scenario import load_scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURES_ROOT = REPO_ROOT / "tests" / "otbox" / "captures"
JOURNEYS_ROOT = REPO_ROOT / "tests" / "otbox" / "catalogue" / "journeys"

PI_SCENARIOS = (
    "pi-linear-edit",
    "pi-provider-context",
    "pi-compaction",
    "pi-branch-rewind",
    "pi-readonly-search",
    "pi-security-redaction",
    "pi-setup-status",
    "pi-tui-slash-commands",
    "pi-tui-slash-commands-bucket",
)

PI_TUI_SLASH_COMMANDS = (
    "/ot-capture-status",
    "/ot-search",
    "/ot-standup",
    "/ot-capsule",
    "/ot-dataset",
    "/ot-setup",
    "/ot-trace",
)

PI_JOURNEYS = (
    "pi-package-gallery-manifest",
    "pi-setup-dry-run",
    "pi-extension-capture-linear",
    "pi-extension-trail-anchor",
    "pi-context-tree-provider-fidelity",
    "pi-compaction-branch-fidelity",
    "pi-readonly-recursion-guard",
    "pi-security-sanitize-captured-content",
    "pi-incremental-recovery",
    "pi-skill-detection-parity",
    "pi-tool-bash-detection-parity",
    "mixed-agent-bucket-parity-pi",
    "pi-full-parity-latest",
)

PI_JOURNEY_CHECKPOINTS = {
    "pi-package-gallery-manifest": ["c-installed-source"],
    "pi-setup-dry-run": ["c-installed-source"],
    "pi-extension-capture-linear": ["c-captured-pi-real-session"],
    "pi-extension-trail-anchor": ["c-captured-pi-real-session"],
    "pi-context-tree-provider-fidelity": ["c-captured-pi-provider-context"],
    "pi-compaction-branch-fidelity": [
        "c-captured-pi-compacted-session",
        "c-captured-pi-branch-session",
    ],
    "pi-readonly-recursion-guard": ["c-captured-pi-readonly-session"],
    "pi-security-sanitize-captured-content": ["c-captured-pi-security-session"],
    "pi-incremental-recovery": ["c-captured-pi-real-session"],
    "pi-skill-detection-parity": ["c-captured-pi-readonly-session"],
    "pi-tool-bash-detection-parity": ["c-captured-pi-real-session"],
    "mixed-agent-bucket-parity-pi": ["c-mixed-agent-pi-bucket"],
    "pi-full-parity-latest": ["c-pi-full-parity-latest"],
}


def _venv_python() -> str:
    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def _artifact_state(name: str) -> dict[str, tuple[bool, int | None, int | None]]:
    root = CAPTURES_ROOT / name
    state: dict[str, tuple[bool, int | None, int | None]] = {}
    for filename in ("snapshot.tar.gz", "metadata.json"):
        path = root / filename
        if path.exists():
            stat = path.stat()
            state[filename] = (True, stat.st_size, stat.st_mtime_ns)
        else:
            state[filename] = (False, None, None)
    return state


def _pi_offline_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    env["PATH"] = str(empty_path)
    env.pop("OT_REAL_PI", None)
    return env


def _run_capture_refresh(*argv: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_venv_python(), "-m", "tests.otbox", "capture-refresh", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )


@pytest.mark.parametrize("scenario_name", PI_SCENARIOS)
def test_pi_scenarios_load(scenario_name: str) -> None:
    scenario = load_scenario(scenario_name)

    assert scenario.name == scenario_name
    assert scenario.agent == "pi"
    assert scenario.binary_name == "pi"
    assert scenario.capture.artifact_dir_name == scenario_name
    assert scenario.turns
    if scenario_name == "pi-tui-slash-commands-bucket":
        assert scenario.initial_state.template_name is None
    else:
        assert scenario.initial_state.template_name == "single-file-python-project"


@pytest.mark.parametrize(
    ("scenario_name", "sentinel"),
    [
        ("pi-tui-slash-commands", "OPENTRACES-SLASH-DONE"),
        ("pi-tui-slash-commands-bucket", "OPENTRACES-SLASH-BUCKET-DONE"),
    ],
)
def test_pi_tui_slash_command_scenarios_exercise_each_registered_command(
    scenario_name: str,
    sentinel: str,
) -> None:
    scenario = load_scenario(scenario_name)

    prompted_commands = {
        turn.prompt.split(maxsplit=1)[0]
        for turn in scenario.turns
        if turn.prompt.startswith("/ot-")
    }

    assert scenario.agent == "pi"
    assert scenario.binary_name == "pi"
    assert set(PI_TUI_SLASH_COMMANDS) <= prompted_commands
    assert len(scenario.turns) == len(PI_TUI_SLASH_COMMANDS) + 1
    assert scenario.turns[-1].expect_regex == sentinel


def test_capture_refresh_expands_checkpoint_trace_id_in_pty_turns() -> None:
    from tests.otbox.cli import _expand_capture_refresh_turns
    from tests.otbox.simulated_users.runner import Turn

    expanded = _expand_capture_refresh_turns(
        [Turn(prompt="/ot-trace {trace_id}", expect_regex="{trace_id}", timeout_s=5)],
        {"trace_id": "trace-from-bucket"},
    )

    assert expanded[0].prompt == "/ot-trace trace-from-bucket"
    assert expanded[0].expect_regex == "trace-from-bucket"


def test_pi_linear_checkpoint_registered() -> None:
    checkpoint = REGISTRY["c-captured-pi-real-session"]

    assert checkpoint.composed_from == "c-installed-source"
    assert checkpoint.cache is False
    assert checkpoint.provides["captured_traces"] == 1
    assert "pi-linear-edit" in checkpoint.description


@pytest.mark.parametrize(
    ("checkpoint_name", "capture_name"),
    [
        ("c-captured-pi-provider-context", "pi-provider-context"),
        ("c-captured-pi-compacted-session", "pi-compaction"),
        ("c-captured-pi-branch-session", "pi-branch-rewind"),
        ("c-captured-pi-readonly-session", "pi-readonly-search"),
        ("c-captured-pi-security-session", "pi-security-redaction"),
        ("c-mixed-agent-pi-bucket", "Pi bucket"),
        ("c-pi-full-parity-latest", "Pi capture"),
    ],
)
def test_pi_checkpoints_registered(checkpoint_name: str, capture_name: str) -> None:
    checkpoint = REGISTRY[checkpoint_name]

    assert checkpoint.composed_from == "c-installed-source"
    assert checkpoint.cache is False
    assert checkpoint.provides["captured_traces"] >= 1
    assert "pi" in checkpoint.description.lower()


@pytest.mark.parametrize("journey_name", PI_JOURNEYS)
def test_pi_journeys_parse_and_are_default_ci_safe(journey_name: str) -> None:
    path = JOURNEYS_ROOT / f"{journey_name}.toml"
    doc = tomllib.loads(path.read_text())

    assert doc["name"] == journey_name
    assert doc["lane"] == "core"
    assert doc["tier"] == 0
    assert doc["tier_label"] in {"silver", "gold"}
    assert doc["from_checkpoints"] == PI_JOURNEY_CHECKPOINTS[journey_name]
    assert doc["status"].startswith(("synthetic", "package", "setup", "aggregate"))
    assert doc["steps"]
    assert doc["assertions"]


def test_pi_capture_refresh_dry_run_needs_no_pi_binary(tmp_path: Path) -> None:
    before = _artifact_state("pi-linear-edit")

    proc = _run_capture_refresh(
        "--scenario",
        "pi-linear-edit",
        "--dry-run",
        "--json",
        env=_pi_offline_env(tmp_path),
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "dry-run"
    assert payload["scenario"] == "pi-linear-edit"
    assert payload["agent"] == "pi"
    assert payload["binary_name"] == "pi"
    assert payload["binary_found"] is False
    assert payload["real_pi_enabled"] is False
    assert payload["turn_count"] >= 1
    assert _artifact_state("pi-linear-edit") == before


def test_pi_capture_refresh_skips_without_real_pi_opt_in(tmp_path: Path) -> None:
    before = _artifact_state("pi-linear-edit")

    proc = _run_capture_refresh(
        "--scenario",
        "pi-linear-edit",
        "--json",
        env=_pi_offline_env(tmp_path),
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "skipped"
    assert payload["scenario"] == "pi-linear-edit"
    assert payload["agent"] == "pi"
    assert payload["binary_name"] == "pi"
    assert "OT_REAL_PI=1" in payload["reason"]
    assert _artifact_state("pi-linear-edit") == before
