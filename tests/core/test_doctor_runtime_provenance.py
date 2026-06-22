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

    # Probing is OPT-IN (security): only `probe=True` reads verified module
    # facts by executing the trusted runner interpreters.
    prov = doctor._runtime_provenance(Path("/tmp/repo"), probe=True)
    assert prov["probed"] is True
    installs = {i.get("module_file"): i for i in prov["discovered_installs"]}

    # The probe's real module_file is used + classified, never fabricated.
    pipx_mod = "/fake/pipx/venvs/opentraces/lib/python3.12/site-packages/opentraces/__init__.py"
    assert pipx_mod in installs
    assert installs[pipx_mod]["source_kind"] == "pipx"
    assert installs[pipx_mod]["dist_version"] == "0.4.6"
    assert installs[pipx_mod]["verified"] is True
    assert installs[pipx_mod]["probe"] == "ok"

    # The broken probe → honest unknown module_file (None), verified False.
    broken_entries = [
        i for i in prov["discovered_installs"]
        if i.get("module_file") is None and i.get("verified") is False
        and i.get("probe") == "error"
    ]
    assert broken_entries, prov["discovered_installs"]


# --------------------------------------------------------------------------
# mixed vs single runtime detection
# --------------------------------------------------------------------------
# pipx- and brew-SHAPED interpreter paths so the execution-free classifier can
# label them WITHOUT running anything (codex round 2 — no default execution).
_PIPX_PY = "/root/a/.local/pipx/venvs/opentraces/bin/python"
_BREW_PY = "/root/b/homebrew/Cellar/opentraces/0.4.6/libexec/bin/python"


def _mixed_runtime_world(monkeypatch: pytest.MonkeyPatch) -> None:
    cur_python = "/root/cur/bin/python"
    cur_module = "/root/cur/src/opentraces/__init__.py"
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
            _runner("codex-cli", "codex", "PostToolUse", _PIPX_PY),
            _runner("claude-code", "claude", "Stop", _PIPX_PY),
            _runner("git", "git", "post-commit", _BREW_PY),
        ],
    )
    monkeypatch.setattr(doctor, "_watcher_runner_interpreter", lambda: _BREW_PY)
    monkeypatch.setattr(doctor, "_otlp_runner_interpreter", lambda: None)


def test_mixed_runtimes_detected_without_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The DEFAULT path executes NOTHING — mixed runtimes are still detected from
    # the distinct interpreter-path source_kinds (source + pipx + brew).
    _mixed_runtime_world(monkeypatch)

    def _no_probe(python: str):
        raise AssertionError(f"default doctor must NOT execute {python!r}")

    monkeypatch.setattr(doctor, "_probe_interpreter", _no_probe)

    prov = doctor._runtime_provenance(Path("/tmp/repo"))  # probe defaults False

    assert prov["probed"] is False
    assert prov["state"] == "mixed_runtimes"
    assert prov["severity"] == "warning"

    kinds = {i.get("source_kind") for i in prov["discovered_installs"]}
    assert {"source", "pipx", "brew"} <= kinds, prov["discovered_installs"]
    # Unverified by default — module_file/version are honest nulls, not guessed.
    for i in prov["discovered_installs"]:
        if i.get("source_kind") in ("pipx", "brew"):
            assert i["verified"] is False
            assert i["module_file"] is None
            assert i["dist_version"] is None
            assert i["probe"] == "not_run"

    runners = {r["name"]: r for r in prov["integration_runners"]}
    for name in ("codex-cli", "claude-code", "git", "watcher"):
        assert name in runners, runners
        assert runners[name]["matches_current"] is False
        assert runners[name]["probe"] == "not_run"
        assert runners[name]["verified"] is False

    # advice is an informational STRING, never a mutation directive.
    assert isinstance(prov["advice"], str) and prov["advice"]
    assert "purge data" not in prov["advice"].lower()


def test_mixed_runtimes_verified_with_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # WITH the opt-in probe, trusted runners upgrade to verified module facts.
    _mixed_runtime_world(monkeypatch)
    monkeypatch.setattr(
        doctor,
        "_probe_interpreter",
        lambda python: {
            _PIPX_PY: (
                "/root/a/.local/pipx/venvs/opentraces/lib/python3.12/"
                "site-packages/opentraces/__init__.py",
                "0.4.6",
            ),
            _BREW_PY: (
                "/root/b/homebrew/Cellar/opentraces/0.4.6/libexec/lib/"
                "python3.12/site-packages/opentraces/__init__.py",
                "0.4.6",
            ),
        }.get(python, (None, None)),
    )

    prov = doctor._runtime_provenance(Path("/tmp/repo"), probe=True)

    assert prov["probed"] is True
    assert prov["state"] == "mixed_runtimes"
    verified = [i for i in prov["discovered_installs"] if i.get("verified")]
    # current + pipx + brew all verified (current via introspection).
    assert any("/pipx/" in (i.get("module_file") or "") for i in verified)
    assert any("/Cellar/" in (i.get("module_file") or "") for i in verified)
    for r in prov["integration_runners"]:
        assert r["matches_current"] is False
        assert r["probe"] == "ok"


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
# exit_code drift policy — benign stamp-absence (``version-missing`` /
# ``shim-version-missing``) is WARN-severity and exits 0; every genuinely
# broken reason still exits 3. Regression: doctor rc=3 on benign drift in a
# freshly-restored captured world / older-stamp install broke otbox journeys
# (codex-parity-*, budgeted-surfaces) and bites real scripts/CI.
# --------------------------------------------------------------------------
def _report_with_hook_drift(drift, *, installer="claude-code", broken=None):
    h = {"installer": installer, "installed": True, "drift": list(drift)}
    if broken is not None:
        h["broken_harnesses"] = broken
    return {
        "security": {"tools": []},
        "hooks": [h],
        "watcher": {"provenance": {"drift": []}},
        "trail_event_log": {"state": "ok"},
    }


def test_exit_code_benign_version_missing_hook_drift_is_zero() -> None:
    assert doctor.exit_code(_report_with_hook_drift(["version-missing"])) == 0


def test_exit_code_benign_skill_version_missing_is_zero() -> None:
    report = _report_with_hook_drift(["version-missing"], installer="skill", broken=[])
    assert doctor.exit_code(report) == 0


def test_exit_code_benign_watcher_shim_version_missing_is_zero() -> None:
    report = {
        "security": {"tools": []},
        "hooks": [],
        "watcher": {"provenance": {"drift": ["shim-version-missing"]}},
        "trail_event_log": {"state": "ok"},
    }
    assert doctor.exit_code(report) == 0


@pytest.mark.parametrize(
    "hard_reason",
    [
        "version-drift",
        "shim-interpreter-missing",
        "shim-legacy-verb",
        "shim-version-drift",
        "daemon-executable-missing",
        "daemon-version-drift",
    ],
)
def test_exit_code_hard_hook_drift_is_three(hard_reason: str) -> None:
    assert doctor.exit_code(_report_with_hook_drift([hard_reason])) == 3


def test_exit_code_mixed_soft_and_hard_drift_is_three() -> None:
    # A real break alongside a benign reason still fails.
    report = _report_with_hook_drift(["version-missing", "version-drift"])
    assert doctor.exit_code(report) == 3


def test_exit_code_broken_harnesses_still_three_despite_benign_drift() -> None:
    report = _report_with_hook_drift(
        ["version-missing"], installer="skill", broken=["claude"]
    )
    assert doctor.exit_code(report) == 3


def test_exit_code_hard_watcher_provenance_drift_is_three() -> None:
    report = {
        "security": {"tools": []},
        "hooks": [],
        "watcher": {"provenance": {"drift": ["daemon-version-drift"]}},
        "trail_event_log": {"state": "ok"},
    }
    assert doctor.exit_code(report) == 3


def test_exit_code_skill_bool_version_staleness_is_zero() -> None:
    # The skill hook reports drift as a coarse BOOL (version staleness:
    # `drift = installed and inst_ver != __version__`), not a reason list.
    # Staleness alone is a warning, not a break.
    report = {
        "security": {"tools": []},
        "hooks": [
            {"installer": "skill", "installed": True, "drift": True, "broken_harnesses": []}
        ],
        "watcher": {"provenance": {"drift": []}},
        "trail_event_log": {"state": "ok"},
    }
    assert doctor.exit_code(report) == 0


def test_exit_code_skill_bool_drift_with_broken_harness_is_three() -> None:
    report = {
        "security": {"tools": []},
        "hooks": [
            {
                "installer": "skill",
                "installed": True,
                "drift": True,
                "broken_harnesses": ["claude-code"],
            }
        ],
        "watcher": {"provenance": {"drift": []}},
        "trail_event_log": {"state": "ok"},
    }
    assert doctor.exit_code(report) == 3


def test_exit_code_skill_list_version_missing_is_zero() -> None:
    # The skill installer now emits drift as a reason LIST (same shape as every
    # other installer). version-missing (no stamp) is benign → exit 0.
    report = _report_with_hook_drift(["version-missing"], installer="skill", broken=[])
    assert doctor.exit_code(report) == 0


def test_exit_code_skill_list_version_drift_is_three() -> None:
    # The consistency fix: skill version-DRIFT (stamped at a different version)
    # is a real mismatch and exits 3 — exactly like claude-code/codex/git
    # version-drift. No more cross-installer asymmetry from the old bool shape.
    report = _report_with_hook_drift(["version-drift"], installer="skill", broken=[])
    assert doctor.exit_code(report) == 3


def test_exit_code_never_raises_on_mixed_drift_shapes() -> None:
    # Regression: with benign hook drift no longer early-returning, exit_code
    # reached the skill branch whose drift is a bool and raised TypeError
    # ('bool' object is not iterable). It must tolerate every real-world drift
    # shape (list / None / bool) without raising. Mirrors a real captured box:
    # claude-code/git drift=['version-missing'], pi drift=None, skill drift=True.
    report = {
        "security": {"tools": []},
        "hooks": [
            {"installer": "claude-code", "installed": True, "drift": ["version-missing"]},
            {"installer": "git", "installed": True, "drift": ["version-missing"]},
            {"installer": "pi", "installed": True, "drift": None},
            {"installer": "skill", "installed": True, "drift": True, "broken_harnesses": []},
        ],
        "watcher": {"provenance": {"drift": []}},
        "trail_event_log": {"state": "ok"},
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


# --------------------------------------------------------------------------
# codex finding #1 — SECURITY: never execute an arbitrary hook binary.
#
# RED-BEFORE-GREEN: on the pre-fix code `_probe_interpreter` executes the first
# shlex token of ANY opentraces-substring hook command. A malicious
# `"/tmp/payload" opentraces ...` command therefore makes `doctor` run
# /tmp/payload. This test wires a REAL executable payload that drops a sentinel
# file and asserts (a) the probe never runs it (sentinel absent) and (b) the
# runner is recorded as honest `unknown` / `skipped_untrusted`. Pre-fix the
# sentinel WOULD appear; post-fix it must not.
# --------------------------------------------------------------------------
def test_untrusted_hook_command_is_never_executed(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "PWNED"
    payload = tmp_path / "payload"
    # A real executable that, if ever run, proves arbitrary code execution.
    payload.write_text(
        "#!/bin/sh\n"
        f"echo touched > {shlex.quote(str(sentinel))}\n"
        # Also print a perfectly-shaped probe output so output-shape validation
        # alone would NOT save us — only refusing to execute does.
        "echo /spoofed/opentraces/__init__.py\n"
        "echo 9.9.9\n",
        encoding="utf-8",
    )
    payload.chmod(0o755)

    # Command looks opentraces-owned (substring) AND carries `-m opentraces`,
    # but the executable is an arbitrary binary, not a python interpreter.
    malicious = f'"{payload}" -m opentraces.capture.codex_cli.hooks.on_tool_use'
    _write_codex_hooks(fake_home, malicious)

    monkeypatch.setattr(
        doctor,
        "_current_runtime",
        lambda: {
            "argv0": "opentraces",
            "python": "/root/cur/bin/python",
            "module_file": "/root/cur/src/opentraces/__init__.py",
            "dist_version": "0.4.6",
            "source_kind": "source",
            "git_root": None,
            "git_commit": None,
        },
    )
    monkeypatch.setattr(doctor, "_watcher_runner_interpreter", lambda: None)
    monkeypatch.setattr(doctor, "_otlp_runner_interpreter", lambda: None)

    # NOTE: _probe_interpreter is intentionally NOT mocked — the real subprocess
    # path is exercised so the sentinel is the genuine execution proof.

    # (1) DEFAULT path: doctor executes NOTHING config-derived. The runner is
    # reported as `not_run`; the payload is never run.
    prov = doctor._runtime_provenance(tmp_path / "repo")
    assert prov["probed"] is False
    assert not sentinel.exists(), (
        "SECURITY: default doctor executed an arbitrary hook binary"
    )
    runner = next(
        r for r in prov["integration_runners"] if r["name"] == "codex-cli"
    )
    assert runner["probe"] == "not_run"
    assert runner["verified"] is False
    assert runner["matches_current"] is False

    # (2) EVEN WITH the opt-in probe, the basename + `-m opentraces` gate still
    # refuses an arbitrary binary — `/tmp/.../payload opentraces` is rejected as
    # skipped_untrusted and STILL never executed (user-consented probe, but the
    # gate is hygiene against exactly this shape).
    prov2 = doctor._runtime_provenance(tmp_path / "repo", probe=True)
    assert prov2["probed"] is True
    assert not sentinel.exists(), (
        "SECURITY: --probe-runtimes executed an arbitrary hook binary"
    )
    runner2 = next(
        r for r in prov2["integration_runners"] if r["name"] == "codex-cli"
    )
    assert runner2["probe"] == "skipped_untrusted"
    assert runner2["verified"] is False


def test_console_script_runner_is_untrusted_not_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A console-script runner (`opentraces`/`ot`, NOT `python -m`) cannot run
    # the import snippet, so the trust gate refuses it: honest unknown, no exec.
    cur_python = "/root/cur/bin/python"
    cur_module = "/root/cur/src/opentraces/__init__.py"
    console = "/usr/local/bin/opentraces"

    monkeypatch.setattr(
        doctor,
        "_current_runtime",
        lambda: {
            "argv0": "opentraces", "python": cur_python, "module_file": cur_module,
            "dist_version": "0.4.6", "source_kind": "source",
            "git_root": None, "git_commit": None,
        },
    )
    monkeypatch.setattr(
        doctor,
        "_opentraces_hook_runners",
        lambda cwd: [
            {
                "name": "codex-cli", "integration": "codex", "event": "PostToolUse",
                "command": f'"{console}" trace get', "interpreter": console,
            }
        ],
    )
    monkeypatch.setattr(doctor, "_watcher_runner_interpreter", lambda: None)
    monkeypatch.setattr(doctor, "_otlp_runner_interpreter", lambda: None)

    def _no_probe(python: str):
        raise AssertionError(f"console-script runner {python!r} must NOT be probed")

    monkeypatch.setattr(doctor, "_probe_interpreter", _no_probe)

    # Even with the opt-in probe enabled, the console-script shape is refused.
    prov = doctor._runtime_provenance(Path("/tmp/repo"), probe=True)
    runner = prov["integration_runners"][0]
    assert runner["probe"] == "skipped_untrusted"
    assert runner["verified"] is False


# --------------------------------------------------------------------------
# codex finding #1 (layer 2) — validate the probe OUTPUT shape; spoofed stdout
# from a trusted-but-broken interpreter is treated as unknown, never trusted.
# --------------------------------------------------------------------------
def test_probe_rejects_spoofed_output_shape(tmp_path: Path) -> None:
    # A python-basename interpreter (so the trust gate lets it run) whose stdout
    # is NOT the `<.../opentraces/__init__.py>\n<version>` shape → (None, None).
    bad = tmp_path / "python"
    bad.write_text(
        "#!/bin/sh\necho not-a-module-path\necho \n", encoding="utf-8"
    )
    bad.chmod(0o755)
    assert doctor._probe_interpreter(str(bad)) == (None, None)

    # And a well-shaped interpreter probe IS trusted.
    good = tmp_path / "python3"
    good.write_text(
        "#!/bin/sh\n"
        "echo /fake/site-packages/opentraces/__init__.py\n"
        "echo 1.2.3\n",
        encoding="utf-8",
    )
    good.chmod(0o755)
    module_file, version = doctor._probe_interpreter(str(good))
    assert module_file is not None and module_file.endswith("opentraces/__init__.py")
    assert version == "1.2.3"


# --------------------------------------------------------------------------
# codex finding #2 — aggregate probe budget caps distinct interpreters probed.
# --------------------------------------------------------------------------
def test_probe_budget_caps_distinct_interpreters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cur_python = "/root/cur/bin/python"
    cur_module = "/root/cur/src/opentraces/__init__.py"
    n = doctor.MAX_PROBE_INTERPRETERS + 4
    interps = [f"/fake/v{i}/bin/python" for i in range(n)]

    monkeypatch.setattr(
        doctor,
        "_current_runtime",
        lambda: {
            "argv0": "opentraces", "python": cur_python, "module_file": cur_module,
            "dist_version": "0.4.6", "source_kind": "source",
            "git_root": None, "git_commit": None,
        },
    )
    monkeypatch.setattr(
        doctor,
        "_opentraces_hook_runners",
        lambda cwd: [
            _runner(f"codex-cli-{i}", "codex", "PostToolUse", interp)
            for i, interp in enumerate(interps)
        ],
    )
    monkeypatch.setattr(doctor, "_watcher_runner_interpreter", lambda: None)
    monkeypatch.setattr(doctor, "_otlp_runner_interpreter", lambda: None)

    probed: list[str] = []

    def counting_probe(python: str):
        probed.append(python)
        return (
            "/fake/pipx/venvs/opentraces/lib/python3.12/site-packages/"
            "opentraces/__init__.py",
            "0.4.6",
        )

    monkeypatch.setattr(doctor, "_probe_interpreter", counting_probe)

    # Budget only applies to the opt-in probe path.
    prov = doctor._runtime_provenance(Path("/tmp/repo"), probe=True)

    # Never probed more than the cap, regardless of how many distinct runners.
    assert len(probed) <= doctor.MAX_PROBE_INTERPRETERS
    # The excess interpreters are recorded as honest budget-skipped unknowns.
    skipped = [
        r for r in prov["integration_runners"]
        if r["probe"] == "skipped_probe_budget_exceeded"
    ]
    assert len(skipped) == n - doctor.MAX_PROBE_INTERPRETERS
    for r in skipped:
        # Budget-skipped runners are never verified (no execution happened).
        assert r["verified"] is False


# --------------------------------------------------------------------------
# codex finding #3 — classifier no longer false-positives `brew` on any path
# that merely contains a homebrew-looking parent.
# --------------------------------------------------------------------------
def test_classify_does_not_false_positive_brew_for_project_venv() -> None:
    c = doctor._classify_source_kind
    # A project venv whose ABSOLUTE path lives under a /opt/homebrew/-looking
    # parent, but is plainly a `.venv` site-packages install — NOT a brew
    # formula. Pre-fix the bare `/homebrew/` substring mislabeled it `brew`.
    venv_under_brew_parent = (
        "/opt/homebrew/work/myproject/.venv/lib/python3.12/"
        "site-packages/opentraces/__init__.py"
    )
    assert c(venv_under_brew_parent) != "brew"
    assert c(venv_under_brew_parent) in ("pip", "pipx", "source")

    # A `/libexec/` that is not the opentraces formula libexec must not be brew.
    foreign_libexec = (
        "/opt/some-tool/libexec/lib/python3.12/"
        "site-packages/opentraces/__init__.py"
    )
    assert c(foreign_libexec) != "brew"

    # Canonical brew formula layouts STILL classify as brew (no regression).
    assert c(
        "/opt/homebrew/Cellar/opentraces/0.4.6/libexec/lib/python3.12/"
        "site-packages/opentraces/__init__.py"
    ) == "brew"
    assert c(
        "/opt/homebrew/opt/opentraces/libexec/lib/python3.12/"
        "site-packages/opentraces/__init__.py"
    ) == "brew"
    assert c(
        "/opt/opentraces/libexec/lib/python3.12/"
        "site-packages/opentraces/__init__.py"
    ) == "brew"
