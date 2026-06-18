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

import os
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
# BLOCKER 2 — the watcher shim PINS the selected runtime FIRST
# --------------------------------------------------------------------------
def test_watcher_shim_pins_selected_runtime_first(
    fake_home: Path, tmp_path: Path
) -> None:
    from opentraces.capture._interpreter import selected_interpreter
    from opentraces.watcher import installer as winst

    # A selected interpreter that prints a sentinel and exits.
    selected = tmp_path / "selected" / "python"
    selected.parent.mkdir(parents=True)
    selected.write_text("#!/bin/sh\necho SELECTED-RUNTIME\n")
    selected.chmod(0o755)

    # A fake `opentraces` on the shim's probe list ($HOME/.local/bin) that would
    # win the run-time probe loop if the shim reached it.
    probed = fake_home / ".local" / "bin" / "opentraces"
    probed.parent.mkdir(parents=True, exist_ok=True)
    probed.write_text(
        "#!/bin/sh\n"
        'case "$1 $2 $3" in\n'
        '  "setup watcher sweep --help") exit 0;;\n'
        "  *) echo PROBED-NOT-SELECTED; exit 0;;\n"
        "esac\n"
    )
    probed.chmod(0o755)

    with selected_interpreter(str(selected)):
        shim = winst._write_shim()

    out = subprocess.run(
        ["sh", str(shim), "run"], capture_output=True, text=True,
        env={**os.environ, "HOME": str(fake_home)},
    )
    # The SELECTED runtime is execed first; the probe loop never runs.
    assert "SELECTED-RUNTIME" in out.stdout, out.stdout
    assert "PROBED-NOT-SELECTED" not in out.stdout, out.stdout


def test_watcher_shim_unpinned_when_no_selection(fake_home: Path) -> None:
    # No selection active → no pinned block; the plain shim is byte-identical to
    # the legacy `setup upgrade` shape (the #65 probe loop owns resolution).
    from opentraces.watcher import installer as winst

    shim = winst._write_shim()
    body = shim.read_text()
    assert "Runtime PINNED" not in body


# --------------------------------------------------------------------------
# BLOCKER 3 (data-safety) — claude prune is scoped to OWNED hooks only
# --------------------------------------------------------------------------
def test_user_hook_with_opentraces_substring_not_pruned(tmp_path: Path) -> None:
    from opentraces.capture.claude_code.install import _prune_stale_opentraces_hooks

    hooks_dir = tmp_path / ".claude" / "hooks"
    new_command = f'"/py" "{hooks_dir}/opentraces_on_stop"'
    # A USER hook whose path merely contains "opentraces" — must survive.
    user_cmd = '"/usr/bin/python3" /Users/me/src/opentraces/tools/my_hook.sh'
    # A stale OWNED module-form hook from another runtime — must be pruned.
    owned_cmd = '"/old/pipx/bin/python" -m opentraces.capture.claude_code.hooks.on_stop'
    event_hooks = [
        {"hooks": [{"type": "command", "command": user_cmd}]},
        {"hooks": [{"type": "command", "command": owned_cmd}]},
    ]
    kept = _prune_stale_opentraces_hooks(event_hooks, new_command, hooks_dir)
    surviving = [
        h["command"]
        for e in kept for h in e.get("hooks", [])
    ]
    assert user_cmd in surviving, "user hook must NOT be pruned"
    assert owned_cmd not in surviving, "stale owned module hook must be pruned"


def test_non_opentraces_module_command_not_pruned(tmp_path: Path) -> None:
    # A non-OpenTraces MODULE command that merely contains the substring
    # "opentraces" (a third-party module, or a basename with no hooks-dir path)
    # must NOT be pruned — only the exact opentraces.capture.claude_code.hooks.
    # prefix and owned hooks-dir scripts are ours.
    from opentraces.capture.claude_code.install import _prune_stale_opentraces_hooks

    hooks_dir = tmp_path / ".claude" / "hooks"
    new_command = f'"/py" "{hooks_dir}/opentraces_on_stop"'
    foreign = [
        '"/usr/bin/python3" -m opentraces_other.hook',   # different top module
        '"/usr/bin/python3" -m my.opentraces_thing',     # substring inside module
    ]
    event_hooks = [{"hooks": [{"type": "command", "command": c}]} for c in foreign]
    kept = _prune_stale_opentraces_hooks(event_hooks, new_command, hooks_dir)
    surviving = [h["command"] for e in kept for h in e.get("hooks", [])]
    for c in foreign:
        assert c in surviving, f"non-OT module command wrongly pruned: {c}"


def test_owned_script_path_hook_is_pruned(tmp_path: Path) -> None:
    from opentraces.capture.claude_code.install import _prune_stale_opentraces_hooks

    hooks_dir = tmp_path / ".claude" / "hooks"
    new_command = f'"/newpy" "{hooks_dir}/opentraces_on_stop"'
    stale = f'"/oldpy" "{hooks_dir}/opentraces_on_stop"'  # same owned script, old interp
    event_hooks = [{"hooks": [{"type": "command", "command": stale}]}]
    kept = _prune_stale_opentraces_hooks(event_hooks, new_command, hooks_dir)
    surviving = [h["command"] for e in kept for h in e.get("hooks", [])]
    assert stale not in surviving


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


def test_use_pipx_resolves_from_discovered(fake_home: Path) -> None:
    pipx = _fake_pipx_python(fake_home)  # a REAL, executable file
    prov = {
        "discovered_installs": [
            {"source_kind": "pipx", "interpreter": pipx, "verified": False},
        ],
        "integration_runners": [],
        "probed": False,
    }
    interp, err = rs.resolve_target_interpreter(prov, "pipx")
    assert err is None
    assert interp and "/pipx/venvs/" in interp


# --------------------------------------------------------------------------
# BLOCKER 1 — never re-bake a DELETED / unusable interpreter (#65/#86)
# --------------------------------------------------------------------------
def test_use_rejects_missing_interpreter() -> None:
    # A discovered pipx install whose interpreter no longer exists on disk must
    # be REJECTED (not silently re-baked into the glue). Pre-fix this returned
    # the dead path → RED.
    prov = {
        "discovered_installs": [
            {"source_kind": "pipx",
             "interpreter": "/nope/.local/pipx/venvs/opentraces/bin/python",
             "verified": False},
        ],
        "integration_runners": [],
        "probed": False,
    }
    interp, err = rs.resolve_target_interpreter(prov, "pipx")
    assert interp is None
    assert err and ("not usable" in err or "does not exist" in err)


def test_use_brew_preserves_opentraces_venv_not_bare_python(tmp_path: Path) -> None:
    # A LIVE Homebrew opentraces venv interpreter
    # (.../Cellar/opentraces/<ver>/libexec/bin/python) is a symlink down to the
    # bare python@3.x. Resolution must hand the UN-collapsed path to
    # stable_interpreter so the Cellar→opt remap preserves the opentraces venv
    # identity (.../opt/opentraces/.../python) — NOT collapse it to the bare
    # python@3.x (that would reintroduce #86 drift + lose opentraces).
    brew = tmp_path / "brew"
    base = brew / "Cellar" / "python@3.14" / "3.14" / "bin" / "python3.14"
    base.parent.mkdir(parents=True)
    base.write_text("#!/bin/sh\n")
    base.chmod(0o755)

    venv_python = brew / "Cellar" / "opentraces" / "1.0" / "libexec" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base)  # venv bin/python → base interpreter

    opt = brew / "opt" / "opentraces"
    opt.parent.mkdir(parents=True)
    opt.symlink_to(brew / "Cellar" / "opentraces" / "1.0")

    prov = {
        "discovered_installs": [
            {"source_kind": "brew", "interpreter": str(venv_python), "verified": False},
        ],
        "integration_runners": [],
        "probed": False,
    }
    interp, err = rs.resolve_target_interpreter(prov, "homebrew")
    assert err is None, err
    # PRESERVED: the stable opt/opentraces venv path, NOT the bare python@3.x.
    assert interp == str(opt / "libexec" / "bin" / "python")
    assert "opt/opentraces" in interp
    assert "python@3.14" not in interp


def test_use_rejects_dead_cellar_interpreter(tmp_path: Path) -> None:
    # A Homebrew Cellar interpreter whose opt symlink is gone cannot remap to a
    # live path → stable_interpreter leaves the (now-deleted) Cellar path, which
    # fails the existence check → rejected (the #86 drift this must prevent).
    dead = str(tmp_path / "Cellar" / "opentraces" / "9.9.9" / "libexec" / "bin" / "python")
    prov = {
        "discovered_installs": [
            {"source_kind": "brew", "interpreter": dead, "verified": False},
        ],
        "integration_runners": [],
        "probed": False,
    }
    interp, err = rs.resolve_target_interpreter(prov, "homebrew")
    assert interp is None
    assert err


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


def _make_executable(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def _patch_runners(monkeypatch, doctor, dev_interp: str) -> None:
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


def test_doctor_reports_dev_runtime_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.core import doctor

    proj = tmp_path / "proj"
    proj.mkdir()
    # The dev interpreter must EXIST + be executable for the downgrade.
    dev_interp = _make_executable(proj / ".venv" / "bin" / "python")
    rs.write_dev_marker(proj, dev_interp, proj)
    _patch_runners(monkeypatch, doctor, dev_interp)

    prov = doctor._runtime_provenance(proj)
    assert prov["dev_runtime_active"] is True
    assert prov["severity"] != "warning"
    assert "deliberately active" in prov["advice"]


def test_doctor_dev_marker_preserves_venv_symlink_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A venv python is commonly a symlink to a base interpreter. Dev-mode intent
    # must match the exact selected venv path, not any other venv that realpaths
    # to the same base Python.
    from opentraces.core import doctor

    base = _make_executable(tmp_path / "base-python")
    proj = tmp_path / "proj"
    other = tmp_path / "other"
    proj.mkdir()
    other.mkdir()
    proj_interp = proj / ".venv" / "bin" / "python"
    other_interp = other / ".venv" / "bin" / "python"
    proj_interp.parent.mkdir(parents=True)
    other_interp.parent.mkdir(parents=True)
    proj_interp.symlink_to(base)
    other_interp.symlink_to(base)

    rs.write_dev_marker(proj, str(proj_interp), proj)
    _patch_runners(monkeypatch, doctor, str(other_interp))

    prov = doctor._runtime_provenance(proj)

    assert prov["dev_runtime_active"] is False
    assert prov["severity"] == "warning"


def test_doctor_stale_dev_marker_keeps_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BLOCKER 4: a marker pointing at a DELETED dev venv must NOT downgrade
    # severity — drift stays visible. Pre-fix this reported dev_runtime_active
    # and severity ok → RED.
    from opentraces.core import doctor

    proj = tmp_path / "proj"
    proj.mkdir()
    dead_interp = str(proj / ".venv" / "bin" / "python")  # never created
    rs.write_dev_marker(proj, dead_interp, proj)
    _patch_runners(monkeypatch, doctor, dead_interp)

    prov = doctor._runtime_provenance(proj)
    assert prov["dev_runtime_active"] is False
    assert prov["severity"] == "warning"
    assert "no longer exists" in prov["advice"]


# --------------------------------------------------------------------------
# dry-run mutation guard
# --------------------------------------------------------------------------
def test_dry_run_changes_nothing_but_lists_plan(
    fake_home: Path, tmp_path: Path
) -> None:
    import hashlib

    from opentraces.capture.claude_code import install as claude_install
    from opentraces.capture.codex_cli import install as codex_install
    from opentraces.capture.git import install as git_install
    from opentraces.watcher import installer as winst

    # Install ALL FOUR glue surfaces, then snapshot their bytes.
    repo = _make_git_repo(tmp_path)
    git_install.install(repo)
    winst._write_shim()
    claude_install.install()
    codex_install.install()

    glue = {
        "git": repo / ".git" / "hooks" / "opentraces-post-commit",
        "watcher": winst.shim_path(),
        "claude": fake_home / ".claude" / "settings.json",
        "codex": fake_home / ".codex" / "hooks.json",
    }

    def _hashes() -> dict:
        return {
            k: hashlib.sha256(p.read_bytes()).hexdigest()
            for k, p in glue.items() if p.is_file()
        }

    before = _hashes()
    assert set(before) == {"git", "watcher", "claude", "codex"}, before

    pipx = _fake_pipx_python(fake_home)
    out = repair_installed_integrations(
        repo, interpreter=pipx, dry_run=True,
        only={"git", "watcher", "claude-code", "codex-cli"},
    )

    assert out["dry_run"] is True
    names = {row["name"] for row in out["plan"]}
    assert {"git", "watcher", "claude-code", "codex-cli"} <= names
    for row in out["plan"]:
        if row["name"] in {"git", "watcher"}:
            assert row["target_interpreter"] == pipx
    # Byte-identical across ALL FOUR glue files: dry-run mutates nothing.
    assert _hashes() == before
