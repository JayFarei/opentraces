"""Tests for the doctor runtime-provenance section (issue #93).

``opentraces doctor`` reports semantic versions only, so two installs from
different code roots (pipx, Homebrew, editable ``./otd``) that all report the
same version produce zero drift — an agent cannot tell that hooks/watcher/git
execute a different code root than the foreground command. The new
``runtime_provenance`` section surfaces that split: the current process, the
distinct opentraces installs discovered behind every configured integration
runner, and per-runner ``matches_current`` flags. Detection-only: severity is
``warning``, it never feeds ``exit_code`` (remediation is sibling #99).

Codex review folded two plan-breakers these tests guard:
  * finding #1 — ``_interpreter_health`` discards HEALTHY runners, so a healthy
    pipx/brew runner (the #93 case) was never enumerated. The new shared
    ``_opentraces_hook_runners`` returns EVERY opentraces-owned runner
    regardless of health (``test_hook_runners_lists_healthy_runners``).
  * finding #3 — ``module_file`` cannot be derived from interpreter-path
    substrings; a bounded best-effort subprocess probe reads the REAL
    ``opentraces.__file__`` + dist version per distinct interpreter, falling
    back to ``module_file: null`` / ``source_kind: "unknown"`` on failure
    (``test_discovered_installs_uses_probe``).
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from opentraces.core import doctor


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------
@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME (and Path.home) at an isolated dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _write_codex_hooks(home: Path, command: str) -> None:
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {"hooks": [{"type": "command", "command": command}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def _runner(name: str, integration: str, event: str, interpreter: str) -> dict:
    return {
        "name": name,
        "integration": integration,
        "event": event,
        "command": f'"{interpreter}" -m opentraces.x',
        "interpreter": interpreter,
    }


# --------------------------------------------------------------------------
# path classifier (finding #3 — kind comes from the resolved module_file)
# --------------------------------------------------------------------------
def test_classify_interpreter_kinds() -> None:
    c = doctor._classify_source_kind
    assert c(
        "/Users/x/.local/pipx/venvs/opentraces/lib/python3.12/"
        "site-packages/opentraces/__init__.py"
    ) == "pipx"
    # brew Cellar AND brew opt/libexec both classify as brew.
    assert c(
        "/opt/homebrew/Cellar/opentraces/0.4.6/libexec/lib/python3.12/"
        "site-packages/opentraces/__init__.py"
    ) == "brew"
    assert c(
        "/opt/homebrew/opt/opentraces/libexec/lib/python3.12/"
        "site-packages/opentraces/__init__.py"
    ) == "brew"
    # editable/source: not under site-packages.
    assert c("/Users/x/src/opentraces/src/opentraces/__init__.py") == "source"
    # plain pip into a system/global site-packages.
    assert c("/usr/lib/python3.12/site-packages/opentraces/__init__.py") == "pip"
    # honest unknown when the probe could not resolve a module file.
    assert c(None) == "unknown"


# --------------------------------------------------------------------------
# finding #1 — the new extractor returns HEALTHY runners too
# --------------------------------------------------------------------------
def test_hook_runners_lists_healthy_runners(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stable, on-disk pipx interpreter — exactly the runner _interpreter_health
    # DISCARDS (it only emits unhealthy findings). _opentraces_hook_runners must
    # still enumerate it.
    pipx = "/Users/dev/.local/pipx/venvs/opentraces/bin/python"
    _write_codex_hooks(
        fake_home,
        f"{shlex.quote(pipx)} -m opentraces.capture.codex_cli.hooks.on_tool_use",
    )
    real_exists = doctor.os.path.exists
    monkeypatch.setattr(
        doctor.os.path, "exists", lambda p: True if p == pipx else real_exists(p)
    )

    runners = doctor._opentraces_hook_runners(tmp_path / "repo")
    interps = [r["interpreter"] for r in runners]
    assert pipx in interps, runners
    codex = next(r for r in runners if r["interpreter"] == pipx)
    assert codex["integration"] == "codex"
    assert codex["event"] == "PostToolUse"

    # And the existing health view still treats this healthy runner as ok —
    # the split is real: enumerated for provenance, NOT flagged for health.
    health = doctor._interpreter_health(tmp_path / "repo")
    assert health == {"status": "ok", "findings": []}


# --------------------------------------------------------------------------
# finding #3 — discovered installs come from a bounded probe, honest on failure
# --------------------------------------------------------------------------
def test_discovered_installs_uses_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur_python = "/root/cur/bin/python"
    cur_module = "/root/cur/src/opentraces/__init__.py"
    other = "/fake/pipx/bin/python"
    broken = "/fake/broken/bin/python"

    monkeypatch.setattr(
        doctor,
        "_current_runtime",
        lambda: {
            "argv0": "opentraces",
            "python": cur_python,
            "module_file": cur_module,
            "dist_version": "0.4.6",
            "source_kind": "source",
            "git_root": None,
            "git_commit": None,
        },
    )
    monkeypatch.setattr(
        doctor,
        "_opentraces_hook_runners",
        lambda cwd: [
            _runner("codex-cli", "codex", "PostToolUse", other),
            _runner("git", "git", "post-commit", broken),
        ],
    )
    monkeypatch.setattr(doctor, "_watcher_runner_interpreter", lambda: None)
    monkeypatch.setattr(doctor, "_otlp_runner_interpreter", lambda: None)

    probe_map = {
        other: ("/fake/pipx/venvs/opentraces/lib/python3.12/site-packages/opentraces/__init__.py", "0.4.6"),
        broken: (None, None),
    }

    def fake_probe(python: str):
        return probe_map.get(python, (None, None))

    monkeypatch.setattr(doctor, "_probe_interpreter", fake_probe)

    prov = doctor._runtime_provenance(Path("/tmp/repo"))
    installs = {i.get("module_file"): i for i in prov["discovered_installs"]}

    # The probe's real module_file is used + classified, never fabricated.
    pipx_mod = "/fake/pipx/venvs/opentraces/lib/python3.12/site-packages/opentraces/__init__.py"
    assert pipx_mod in installs
    assert installs[pipx_mod]["source_kind"] == "pipx"
    assert installs[pipx_mod]["dist_version"] == "0.4.6"

    # The broken probe → honest unknown, never a guessed module_file.
    unknowns = [
        i for i in prov["discovered_installs"]
        if i.get("module_file") is None and i.get("source_kind") == "unknown"
    ]
    assert unknowns, prov["discovered_installs"]


# --------------------------------------------------------------------------
# mixed vs single runtime detection
# --------------------------------------------------------------------------
def test_mixed_runtimes_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    cur_python = "/root/cur/bin/python"
    cur_module = "/root/cur/src/opentraces/__init__.py"
    a = "/root/a/bin/python"
    b = "/root/b/bin/python"

    monkeypatch.setattr(
        doctor,
        "_current_runtime",
        lambda: {
            "argv0": "opentraces",
            "python": cur_python,
            "module_file": cur_module,
            "dist_version": "0.4.6",
            "source_kind": "source",
            "git_root": None,
            "git_commit": None,
        },
    )
    monkeypatch.setattr(
        doctor,
        "_opentraces_hook_runners",
        lambda cwd: [
            _runner("codex-cli", "codex", "PostToolUse", a),
            _runner("claude-code", "claude", "Stop", a),
            _runner("git", "git", "post-commit", b),
        ],
    )
    monkeypatch.setattr(doctor, "_watcher_runner_interpreter", lambda: b)
    monkeypatch.setattr(doctor, "_otlp_runner_interpreter", lambda: None)
    monkeypatch.setattr(
        doctor,
        "_probe_interpreter",
        lambda python: {
            a: ("/root/a/.local/pipx/venvs/opentraces/lib/python3.12/site-packages/opentraces/__init__.py", "0.4.6"),
            b: ("/root/b/Cellar/opentraces/0.4.6/libexec/lib/python3.12/site-packages/opentraces/__init__.py", "0.4.6"),
        }.get(python, (None, None)),
    )

    prov = doctor._runtime_provenance(Path("/tmp/repo"))

    assert prov["state"] == "mixed_runtimes"
    assert prov["severity"] == "warning"
    module_files = {i.get("module_file") for i in prov["discovered_installs"]}
    assert len(module_files) >= 2
    # The two non-current installs carry distinct module files.
    assert any("/pipx/" in (m or "") for m in module_files)
    assert any("/Cellar/" in (m or "") for m in module_files)

    runners = {r["name"]: r for r in prov["integration_runners"]}
    for name in ("codex-cli", "claude-code", "git", "watcher"):
        assert name in runners, runners
        assert runners[name]["matches_current"] is False

    # advice is an informational STRING, never a mutation directive.
    assert isinstance(prov["advice"], str) and prov["advice"]
    assert "purge data" not in prov["advice"].lower()


def test_single_runtime_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    cur_python = "/root/cur/bin/python"
    cur_module = "/root/cur/site-packages/opentraces/__init__.py"

    monkeypatch.setattr(
        doctor,
        "_current_runtime",
        lambda: {
            "argv0": "opentraces",
            "python": cur_python,
            "module_file": cur_module,
            "dist_version": "0.4.6",
            "source_kind": "pip",
            "git_root": None,
            "git_commit": None,
        },
    )
    monkeypatch.setattr(
        doctor,
        "_opentraces_hook_runners",
        lambda cwd: [
            _runner("codex-cli", "codex", "PostToolUse", cur_python),
            _runner("git", "git", "post-commit", cur_python),
        ],
    )
    monkeypatch.setattr(doctor, "_watcher_runner_interpreter", lambda: cur_python)
    monkeypatch.setattr(doctor, "_otlp_runner_interpreter", lambda: None)

    # Every runner shares the current interpreter → no probe should be needed.
    def _no_probe(python: str):
        raise AssertionError(f"unexpected probe of {python!r} on the clean path")

    monkeypatch.setattr(doctor, "_probe_interpreter", _no_probe)

    prov = doctor._runtime_provenance(Path("/tmp/repo"))
    assert prov["state"] == "single_runtime"
    assert prov["severity"] == "ok"
    for r in prov["integration_runners"]:
        assert r["matches_current"] is True


# --------------------------------------------------------------------------
# exit_code is unaffected — mixed runtime ALONE stays exit 0 (finding #2)
# --------------------------------------------------------------------------
def test_exit_code_unaffected_by_mixed_runtime() -> None:
    # A report whose other gates (security tools, hook drift, watcher drift,
    # trail log) are all clean, but runtime is mixed → exit_code stays 0.
    report = {
        "security": {"tools": [{"name": "regex", "state": "enabled"}]},
        "hooks": [{"installer": "skill", "installed": True, "drift": []}],
        "watcher": {"provenance": {"drift": []}},
        "trail_event_log": {"state": "ok"},
        "runtime_provenance": {
            "state": "mixed_runtimes",
            "severity": "warning",
            "discovered_installs": [{"module_file": "/a"}, {"module_file": "/b"}],
            "integration_runners": [],
            "advice": "pin your runtimes",
        },
    }
    assert doctor.exit_code(report) == 0


# --------------------------------------------------------------------------
# robustness — never raises (finding: doctor must never crash)
# --------------------------------------------------------------------------
def test_provenance_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("hook config exploded")

    monkeypatch.setattr(doctor, "_opentraces_hook_runners", boom)
    monkeypatch.setattr(doctor, "_watcher_runner_interpreter", boom)
    monkeypatch.setattr(doctor, "_otlp_runner_interpreter", boom)

    prov = doctor._runtime_provenance(Path("/tmp/repo"))
    assert isinstance(prov, dict)
    assert prov.get("state") in ("single_runtime", "mixed_runtimes")
    assert "current" in prov
    assert isinstance(prov.get("discovered_installs"), list)
    assert isinstance(prov.get("integration_runners"), list)


def test_report_includes_runtime_provenance_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.core.config import load_config

    monkeypatch.chdir(tmp_path)
    try:
        cfg = load_config()
    except Exception:
        pytest.skip("global config unavailable in this environment")
    report = doctor.report(cfg, tmp_path)
    assert "runtime_provenance" in report
    rp = report["runtime_provenance"]
    assert set(rp) >= {
        "current",
        "discovered_installs",
        "integration_runners",
        "state",
        "severity",
        "advice",
    }
    # The additive key must not perturb existing consumers or the exit gate.
    assert "interpreter_health" in report
    assert doctor.exit_code(report) in (0, 3)
