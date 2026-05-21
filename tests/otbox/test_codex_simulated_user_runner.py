"""Default-CI-safe Codex simulated-user lane tests (plan 083 SP6).

These tests intentionally do not run a real Codex binary. They pin the
scaffolding contract: scenario TOMLs load as `agent = "codex"` /
`binary_name = "codex"`, capture-refresh can report metadata in dry-run
mode, and the missing-binary path exits cleanly before any box is
resolved.
"""

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

CODEX_SCENARIOS = (
    "codex-linear-edit",
    "codex-bash-debugging",
    "codex-multi-file-patch",
    "codex-subagent-edit",
    "codex-context-compaction",
    "codex-skill-invocation",
    "codex-resume-continue",
    "codex-mcp-tool",
    "codex-permission-request",
    "codex-readonly-search",
    "codex-watcher-backstop",
    "codex-security-redaction",
    "codex-subagent-compaction",
)

CODEX_JOURNEYS = (
    "codex-parity-linear",
    "codex-parity-bash-debugging",
    "codex-parity-multi-file-patch",
    "codex-parity-subagent",
    "codex-parity-compaction",
    "codex-parity-skill-invocation",
    "codex-parity-resume",
    "codex-parity-mcp-permission",
    "codex-parity-security-redaction",
    "mixed-agent-bucket-parity",
    "codex-full-parity-latest",
)

CODEX_SCENARIO_TEMPLATES = {
    "codex-bash-debugging": "failing-python-check-project",
}

CODEX_JOURNEY_CHECKPOINTS = {
    "codex-parity-bash-debugging": ["c-captured-codex-bash-session"],
    "codex-parity-multi-file-patch": ["c-captured-codex-multi-file-session"],
    "codex-parity-subagent": ["c-captured-codex-subagent-session"],
    "codex-parity-compaction": ["c-captured-codex-compacted-session"],
    "codex-parity-skill-invocation": ["c-captured-codex-skill-session"],
    "codex-parity-resume": ["c-captured-codex-resume-session"],
    "codex-parity-mcp-permission": ["c-captured-codex-mcp-session"],
    "codex-parity-security-redaction": ["c-captured-codex-security-session"],
    "mixed-agent-bucket-parity": ["c-mixed-agent-parity-bucket"],
    "codex-full-parity-latest": ["c-codex-full-parity-latest"],
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


def _codex_missing_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    env["PATH"] = str(empty_path)
    return env


def _run_capture_refresh(
    *argv: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_venv_python(), "-m", "tests.otbox", "capture-refresh", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )


@pytest.mark.parametrize("scenario_name", CODEX_SCENARIOS)
def test_codex_scenarios_load(scenario_name: str):
    scenario = load_scenario(scenario_name)

    assert scenario.name == scenario_name
    assert scenario.agent == "codex"
    assert scenario.binary_name == "codex"
    assert scenario.capture.artifact_dir_name == scenario_name
    assert scenario.turns
    assert scenario.initial_state.template_name == CODEX_SCENARIO_TEMPLATES.get(
        scenario_name,
        "single-file-python-project",
    )


def test_codex_linear_checkpoint_registered():
    checkpoint = REGISTRY["c-captured-codex-real-session"]

    assert checkpoint.composed_from == "c-installed-source"
    assert checkpoint.cache is False
    assert "codex-linear-edit" in checkpoint.description


def test_codex_bash_checkpoint_registered():
    checkpoint = REGISTRY["c-captured-codex-bash-session"]

    assert checkpoint.composed_from == "c-installed-source"
    assert checkpoint.cache is False
    assert "codex-bash-debugging" in checkpoint.description


@pytest.mark.parametrize(
    ("checkpoint_name", "capture_name"),
    [
        ("c-captured-codex-multi-file-session", "codex-multi-file-patch"),
        ("c-captured-codex-subagent-session", "codex-subagent-edit"),
        ("c-captured-codex-compacted-session", "codex-context-compaction"),
        ("c-captured-codex-skill-session", "codex-skill-invocation"),
        ("c-captured-codex-resume-session", "codex-resume-continue"),
        ("c-captured-codex-mcp-session", "codex-mcp-tool"),
        ("c-captured-codex-security-session", "codex-security-redaction"),
        ("c-mixed-agent-parity-bucket", "mixed-agent-bucket-parity"),
        ("c-codex-full-parity-latest", "codex-full-parity-latest"),
    ],
)
def test_pending_codex_checkpoints_registered(
    checkpoint_name: str,
    capture_name: str,
):
    checkpoint = REGISTRY[checkpoint_name]

    assert checkpoint.composed_from == "c-installed-source"
    assert checkpoint.cache is False
    assert capture_name in checkpoint.description


@pytest.mark.parametrize("journey_name", CODEX_JOURNEYS)
def test_codex_parity_journeys_parse_and_are_default_ci_safe(journey_name: str):
    path = JOURNEYS_ROOT / f"{journey_name}.toml"
    doc = tomllib.loads(path.read_text())

    assert doc["name"] == journey_name
    assert doc["lane"] == "core"
    assert doc["tier"] == 0
    assert doc["tier_label"] == "gold"
    assert doc["from_checkpoints"] == CODEX_JOURNEY_CHECKPOINTS.get(
        journey_name,
        ["c-captured-codex-real-session"],
    )
    assert doc["preconditions"]["min_captured_traces"] >= 1
    assert doc["status"].startswith(("pending", "artifact-backed"))
    assert doc["steps"]
    assert doc["assertions"]


def test_codex_capture_refresh_dry_run_needs_no_codex_binary(tmp_path: Path):
    before = _artifact_state("codex-linear-edit")

    proc = _run_capture_refresh(
        "--scenario",
        "codex-linear-edit",
        "--dry-run",
        "--json",
        env=_codex_missing_env(tmp_path),
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "dry-run"
    assert payload["scenario"] == "codex-linear-edit"
    assert payload["agent"] == "codex"
    assert payload["binary_name"] == "codex"
    assert payload["binary_found"] is False
    assert payload["turn_count"] >= 1
    assert _artifact_state("codex-linear-edit") == before


def test_codex_capture_refresh_skips_when_codex_binary_missing(tmp_path: Path):
    before = _artifact_state("codex-linear-edit")

    proc = _run_capture_refresh(
        "--scenario",
        "codex-linear-edit",
        "--json",
        env=_codex_missing_env(tmp_path),
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "skipped"
    assert payload["scenario"] == "codex-linear-edit"
    assert payload["agent"] == "codex"
    assert payload["binary_name"] == "codex"
    assert "binary not found" in payload["reason"].lower()
    assert _artifact_state("codex-linear-edit") == before
