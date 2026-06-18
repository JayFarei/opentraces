"""Mechanism unit tests for `setup runtime` (issue #99).

$HOME-isolated, fast, no real package managers. These guard the load-bearing
adversarial findings folded into the plan:

* BLOCKER 1 — the chosen interpreter must reach git (explicit-arg call site)
  AND the watcher shim (bakes sys.executable directly), not just codex/claude.
* BLOCKER 3 — a runtime switch STOPS/re-renders the watcher in place; it never
  removes the unit/shim.
* MINOR — source-kind removal command keyed to the entry's own venv; real-shape
  brew resolution falls back / exits with --probe-runtimes guidance.
* #86 — the Cellar→opt remap survives interpreter selection.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opentraces.capture._interpreter import selected_interpreter, stable_interpreter
from opentraces.core import runtime_select as rs
from opentraces.core.integration_repair import repair_installed_integrations


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # Never let a unit test touch the host's launchd/systemd.
    from opentraces.watcher import installer as winst

    monkeypatch.setattr(winst, "_launchctl", lambda *a, **k: None)
    monkeypatch.setattr(winst, "_systemctl", lambda *a, **k: None)
    return home


def _make_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def _fake_pipx_python(home: Path) -> str:
    py = home / ".local" / "pipx" / "venvs" / "opentraces" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("#!/bin/sh\necho fake\n")
    py.chmod(0o755)
    return str(py)


# --------------------------------------------------------------------------
# module exists (red-before-green sentinel: ModuleNotFoundError pre-fix)
# --------------------------------------------------------------------------
def test_runtime_select_module_importable() -> None:
    assert hasattr(rs, "apply_runtime")
    assert hasattr(rs, "removal_command_for")
    assert hasattr(rs, "discover_runtimes")


# --------------------------------------------------------------------------
# BLOCKER 1 + 3 — re-render reaches git AND the watcher shim
# --------------------------------------------------------------------------
def test_use_repoints_git_and_watcher(fake_home: Path, tmp_path: Path) -> None:
    from opentraces.capture.git import install as git_install
    from opentraces.watcher import installer as winst

    repo = _make_git_repo(tmp_path)
    assert git_install.install(repo) is True  # baked at the running interpreter
    shim = winst._write_shim()  # baked at the running interpreter

    git_hook = repo / ".git" / "hooks" / "opentraces-post-commit"
    pre_git = git_hook.read_text()
    pre_shim = shim.read_text()
    assert "/pipx/venvs/" not in pre_git
    assert "/pipx/venvs/" not in pre_shim

    pipx = _fake_pipx_python(fake_home)
    out = repair_installed_integrations(
        repo, interpreter=pipx, only={"git", "watcher"}
    )
    assert not out["errors"], out

    post_git = git_hook.read_text()
    post_shim = winst.shim_path().read_text()
    # The two runners the rejected no-arg-contextvar design silently missed.
    assert pipx in post_git, post_git
    assert pipx in post_shim, post_shim


def test_watcher_switch_rerenders_in_place_not_removed(
    fake_home: Path, tmp_path: Path
) -> None:
    from opentraces.watcher import installer as winst

    winst._write_shim()
    assert winst.shim_exists()

    pipx = _fake_pipx_python(fake_home)
    repair_installed_integrations(tmp_path, interpreter=pipx, only={"watcher"})

    # The shim is re-rendered IN PLACE (BLOCKER 3 — never uninstall()'d).
    assert winst.shim_exists()
    assert pipx in winst.shim_path().read_text()


# --------------------------------------------------------------------------
# #86 — Cellar→opt remap survives selection
# --------------------------------------------------------------------------
def test_stable_interpreter_under_selection_remaps_brew(tmp_path: Path) -> None:
    cellar = tmp_path / "Cellar" / "opentraces" / "1.2.3"
    libexec_bin = cellar / "libexec" / "bin"
    libexec_bin.mkdir(parents=True)
    (libexec_bin / "python").write_text("#!/bin/sh\n")
    opt = tmp_path / "opt" / "opentraces"
    opt.parent.mkdir(parents=True)
    opt.symlink_to(cellar)

    cellar_python = str(cellar / "libexec" / "bin" / "python")
    expected_opt = str(tmp_path / "opt" / "opentraces" / "libexec" / "bin" / "python")
    with selected_interpreter(cellar_python):
        # Even a caller passing an explicit unrelated interpreter is overridden
        # by the selection, then remapped to the stable opt path.
        assert stable_interpreter("/usr/bin/python3") == expected_opt


def test_stable_interpreter_default_unchanged_without_selection() -> None:
    # No selection active: behaviour is byte-identical to the legacy default.
    assert stable_interpreter("/usr/bin/python3") == "/usr/bin/python3"


# --------------------------------------------------------------------------
# MINOR — source removal command keyed to the entry's own venv
# --------------------------------------------------------------------------
def test_removal_command_for_source_keyed_to_venv() -> None:
    cmd, note = rs.removal_command_for(
        "source", interpreter="/home/x/checkout/.venv/bin/python"
    )
    assert cmd == "/home/x/checkout/.venv/bin/pip uninstall opentraces"
    assert note and "editable checkout" in note


def test_removal_command_for_pipx_brew() -> None:
    assert rs.removal_command_for("pipx")[0] == "pipx uninstall opentraces"
    assert rs.removal_command_for("brew")[0] == (
        "brew uninstall jayfarei/opentraces/opentraces"
    )


def test_remove_duplicates_never_executes(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*a, **k):
        raise AssertionError("remove-duplicates must never shell out")

    monkeypatch.setattr(subprocess, "run", _boom)

    prov = {
        "current": {"source_kind": "source", "python": "/x/.venv/bin/python"},
        "discovered_installs": [
            {"source_kind": "source", "interpreter": "/x/.venv/bin/python"},
            {"source_kind": "pipx", "interpreter": "/h/.local/pipx/venvs/opentraces/bin/python"},
            {"source_kind": "brew", "interpreter": "/opt/homebrew/Cellar/opentraces/1/libexec/bin/python"},
        ],
        "integration_runners": [],
        "state": "mixed_runtimes",
        "probed": False,
    }
    monkeypatch.setattr(rs, "discover_runtimes", lambda *a, **k: prov)
    out = rs.remove_duplicates(Path("/x"), keep="source")
    assert out["executed"] is False
    kinds = {d["source_kind"] for d in out["duplicates"]}
    assert kinds == {"pipx", "brew"}  # source kept


# --------------------------------------------------------------------------
# MINOR — real-shape brew resolution / exit-2 guidance
# --------------------------------------------------------------------------
def test_use_brew_realshape_falls_back_or_guides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A real brew interpreter is a python@3.x symlink with NO /Cellar/opentraces/
    # segment, so path-classification cannot find it. Resolution must then exit
    # with guidance that names --probe-runtimes.
    prov = {
        "current": {"source_kind": "source", "python": "/x/.venv/bin/python"},
        "discovered_installs": [
            {"source_kind": "source", "interpreter": "/x/.venv/bin/python", "verified": False},
        ],
        "integration_runners": [
            {"name": "git", "runner": "/opt/homebrew/opt/python@3.12/bin/python3.12"},
        ],
        "probed": False,
    }
    interp, err = rs.resolve_target_interpreter(prov, "brew")
    assert interp is None
    assert err and "--probe-runtimes" in err


def test_use_pipx_resolves_from_discovered() -> None:
    prov = {
        "discovered_installs": [
            {"source_kind": "pipx", "interpreter": "/h/.local/pipx/venvs/opentraces/bin/python",
             "verified": False},
        ],
        "integration_runners": [],
        "probed": False,
    }
    interp, err = rs.resolve_target_interpreter(prov, "pipx")
    assert err is None
    assert interp and "/pipx/venvs/" in interp


# --------------------------------------------------------------------------
# Mode 2 — dev marker round-trip + doctor reads it
# --------------------------------------------------------------------------
def test_dev_marker_roundtrip(tmp_path: Path) -> None:
    from opentraces.core import doctor

    proj = tmp_path / "proj"
    proj.mkdir()
    interp = "/x/checkout/.venv/bin/python"
    rs.write_dev_marker(proj, interp, proj)

    marker = doctor._read_runtime_selection(proj)
    assert marker and marker["mode"] == "dev"

    assert rs.clear_dev_marker(proj) is True
    assert doctor._read_runtime_selection(proj) is None


def test_doctor_reports_dev_runtime_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.core import doctor

    proj = tmp_path / "proj"
    proj.mkdir()
    dev_interp = "/x/checkout/.venv/bin/python"
    rs.write_dev_marker(proj, dev_interp, proj)

    real = doctor.os.path.realpath(dev_interp)
    # All runners resolve to the dev interpreter → deliberate dev mode.
    monkeypatch.setattr(
        doctor, "_opentraces_hook_runners",
        lambda cwd: [
            {"name": "git", "integration": "git", "event": "post-commit",
             "command": f'"{dev_interp}" -m opentraces', "interpreter": dev_interp},
        ],
    )
    monkeypatch.setattr(doctor, "_watcher_runner_interpreter", lambda: dev_interp)
    monkeypatch.setattr(doctor, "_otlp_runner_interpreter", lambda: None)
    monkeypatch.setattr(
        doctor, "_current_runtime",
        lambda: {"python": "/usr/bin/python3", "module_file": None,
                 "source_kind": "pipx", "dist_version": "1.0"},
    )

    prov = doctor._runtime_provenance(proj)
    assert prov["dev_runtime_active"] is True
    assert prov["severity"] != "warning"
    assert "deliberately active" in prov["advice"]


# --------------------------------------------------------------------------
# dry-run mutation guard
# --------------------------------------------------------------------------
def test_dry_run_changes_nothing_but_lists_plan(
    fake_home: Path, tmp_path: Path
) -> None:
    from opentraces.capture.git import install as git_install
    from opentraces.watcher import installer as winst

    repo = _make_git_repo(tmp_path)
    git_install.install(repo)
    shim = winst._write_shim()
    git_hook = repo / ".git" / "hooks" / "opentraces-post-commit"
    pre_git = git_hook.read_bytes()
    pre_shim = shim.read_bytes()

    pipx = _fake_pipx_python(fake_home)
    out = repair_installed_integrations(
        repo, interpreter=pipx, dry_run=True, only={"git", "watcher"}
    )

    assert out["dry_run"] is True
    names = {row["name"] for row in out["plan"]}
    assert {"git", "watcher"} <= names
    for row in out["plan"]:
        if row["name"] in {"git", "watcher"}:
            assert row["target_interpreter"] == pipx
    # Byte-identical: dry-run mutates nothing.
    assert git_hook.read_bytes() == pre_git
    assert winst.shim_path().read_bytes() == pre_shim
