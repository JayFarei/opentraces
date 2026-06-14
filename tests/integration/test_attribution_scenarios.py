"""Pytest-driven harness over the 45 scenario TOMLs.

Parametrizes one test per scenario file under tests/integration/scenarios/.
Steps that require a live claude REPL (`type = "claude"` entries) are
expensive — they spawn a tmux pane, drive an actual claude session, and
wait for JSONL quiescence. Those scenarios are marked `real_repl` and
skip by default; set `OT_REAL_REPL=1` to run them (see conftest.py).

Scenarios that exercise only deterministic steps (reset, commit,
write_file, delete_file, shell, git, wait, start_watcher, stop_watcher)
run inline against the promoted attribution modules in
`opentraces.enrichment.git.attribution`. They serve as a cheap smoke
layer — they prove the audit-build + blame pipeline handles the
deterministic infrastructure even without a real agent session.

A subset of 16 scenarios earned their place as load-bearing regression
locks (see the plan-043 "no-regression gate" table). Those carry the
`regression_gate` marker so developers can run `pytest -m regression_gate`
for inner-loop iteration. Every one of them contains at least one
`claude` step, so they all also carry `real_repl`.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


SCENARIOS_DIR = Path(__file__).parent / "scenarios"
HARNESS_PATH = Path(__file__).parent / "harness" / "attribution_v2_harness.py"


# Plan-043 "no-regression gate" — 16 scenarios that lock in specific bugs
# fixed during the spike. Every one of these is a P0 to re-break.
REGRESSION_GATE = frozenset({
    "bash_deletes",
    "zero_file_history_session",
    "clear_mid_session",
    "binary_file_added",
    "path_with_spaces",
    "commit_amend_adds_file",
    "file_create_then_delete",
    "bash_rename",
    "multi_edit_tool",
    "bf_printf_write_no_watcher",
    "bf_heredoc_write_no_watcher",
    "bf_append_no_watcher",
    "bf_sed_inplace_no_watcher",
    "bf_cp_no_watcher",
    "rename_with_edit",
    "symlink_added",
})


def _load_harness_module():
    """Import scripts/attribution_v2_harness.py as a module, reusing the
    scenario loader, step executors, and assertion runner without forking
    a subprocess."""
    spec = importlib.util.spec_from_file_location(
        "_ot_harness", HARNESS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_ot_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


def _scenario_has_claude_step(path: Path) -> bool:
    """Peek at a scenario's TOML to see if it drives a real claude REPL."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return any(s.get("type") == "claude" for s in data.get("steps", []))


def _scenario_files() -> list[Path]:
    if not SCENARIOS_DIR.is_dir():
        return []
    return sorted(SCENARIOS_DIR.glob("*.toml"))


def _build_params():
    """One pytest param per scenario, pre-tagged with the right markers."""
    params = []
    for path in _scenario_files():
        name = path.stem
        markers = []
        if _scenario_has_claude_step(path):
            markers.append(pytest.mark.real_repl)
        if name in REGRESSION_GATE:
            markers.append(pytest.mark.regression_gate)
        params.append(pytest.param(path, id=name, marks=markers))
    return params


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """Override the repo-wide HOME-redirect. The harness reads and writes
    under the real ~/.claude/projects/ (Claude Code's own JSONL store),
    and the spike subprocess it invokes inherits the env. Letting HOME
    stay real keeps both views aligned."""
    yield


@pytest.fixture(scope="module")
def harness():
    """Loaded harness module."""
    return _load_harness_module()


def test_invoke_cli_can_isolate_home(tmp_path: Path, monkeypatch, harness):
    """Scenario CLI subprocesses can opt into a scenario-local HOME."""
    project = tmp_path / "project"
    project.mkdir()
    state = harness.RunState(project_dir=project)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    harness._step_invoke_cli(
        state,
        {"args": ["--json", "doctor"], "isolated_home": True},
    )

    expected_home = (project / ".opentraces-test-home").resolve()
    assert captured["env"]["HOME"] == str(expected_home)
    assert expected_home.is_dir()


@pytest.mark.parametrize("scenario_path", _build_params())
def test_scenario(scenario_path: Path, harness):
    """Run one scenario TOML via the harness against the promoted modules.

    For real_repl scenarios this drives tmux + claude + the spike build +
    blame pipeline and asserts the TOML's `[[assertions]]` block against
    the attribution result. For deterministic-only scenarios (no claude
    steps) the same runner still exercises reset/commit/shell/write_file
    plus the build + blame pipeline — which is the cheap smoke layer."""
    s = harness.load_scenario(scenario_path)
    ok, detail = harness.run_scenario(s, verbose=False)
    # Always tear down the project dir so a failing scenario doesn't leave
    # /tmp/ot-h-<name>/ around to pollute the next run.
    try:
        harness.teardown_scenario(s)
    except Exception:
        pass
    if not ok:
        pytest.fail(f"{s.name}: {detail}")
