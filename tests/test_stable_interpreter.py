"""Unit tests for the upgrade-proof interpreter resolver (issue #86).

These tests monkeypatch ``os.path.exists`` and ``os.path.realpath`` so they
never depend on a real Homebrew install. The contract: a version-pinned
Cellar interpreter path is remapped to the stable ``opt/<formula>`` symlink
only when that symlink exists and currently resolves back into the same
formula's Cellar tree; every other shape passes through unchanged.
"""
from __future__ import annotations

import os
import sys

import pytest

from opentraces.capture._interpreter import stable_interpreter


def _fake_realpath_into(prefix: str, formula: str):
    """Return a realpath stub that maps any opt path back under the formula Cellar."""

    def _realpath(path: str) -> str:
        return f"{prefix}/Cellar/{formula}/9.9.9/libexec/bin/python"

    return _realpath


@pytest.mark.parametrize(
    ("cellar_exe", "expected_opt"),
    [
        # brew Apple Silicon macOS
        (
            "/opt/homebrew/Cellar/opentraces/0.4.4/libexec/bin/python",
            "/opt/homebrew/opt/opentraces/libexec/bin/python",
        ),
        # brew Intel macOS
        (
            "/usr/local/Cellar/opentraces/0.4.4/libexec/bin/python",
            "/usr/local/opt/opentraces/libexec/bin/python",
        ),
        # brew Linux (linuxbrew)
        (
            "/home/linuxbrew/.linuxbrew/Cellar/opentraces/0.4.4/libexec/bin/python",
            "/home/linuxbrew/.linuxbrew/opt/opentraces/libexec/bin/python",
        ),
    ],
)
def test_cellar_remaps_to_opt(monkeypatch, cellar_exe, expected_opt):
    # opt symlink exists and resolves back into the same formula Cellar tree.
    prefix = cellar_exe.split("/Cellar/", 1)[0]
    monkeypatch.setattr("os.path.exists", lambda p: p == expected_opt)
    monkeypatch.setattr(
        "os.path.realpath", _fake_realpath_into(prefix, "opentraces")
    )
    assert stable_interpreter(cellar_exe) == expected_opt


@pytest.mark.parametrize(
    "exe",
    [
        # pipx venv
        "/Users/x/.local/pipx/venvs/opentraces/bin/python",
        # editable venv
        "/repo/.venv/bin/python",
        # system python
        "/usr/bin/python3",
    ],
)
def test_non_cellar_paths_pass_through(monkeypatch, exe):
    # exists/realpath should never even be consulted; make them explode if used.
    monkeypatch.setattr(
        "os.path.exists", lambda p: pytest.fail("os.path.exists should not be called")
    )
    monkeypatch.setattr(
        "os.path.realpath",
        lambda p: pytest.fail("os.path.realpath should not be called"),
    )
    assert stable_interpreter(exe) == exe


def test_guard_opt_symlink_missing_returns_original(monkeypatch):
    # Cellar path matches, but the opt symlink does not exist: never emit a
    # non-existent opt path; return the original Cellar path.
    cellar_exe = "/opt/homebrew/Cellar/opentraces/0.4.4/libexec/bin/python"
    monkeypatch.setattr("os.path.exists", lambda p: False)
    monkeypatch.setattr(
        "os.path.realpath",
        lambda p: pytest.fail("realpath should not run when opt is absent"),
    )
    assert stable_interpreter(cellar_exe) == cellar_exe


def test_guard_opt_realpath_outside_cellar_returns_original(monkeypatch):
    # opt path exists but resolves OUTSIDE this formula's Cellar tree
    # (stale/foreign symlink): return the original Cellar path unchanged.
    cellar_exe = "/opt/homebrew/Cellar/opentraces/0.4.4/libexec/bin/python"
    opt_path = "/opt/homebrew/opt/opentraces/libexec/bin/python"
    monkeypatch.setattr("os.path.exists", lambda p: p == opt_path)
    monkeypatch.setattr(
        "os.path.realpath",
        lambda p: "/opt/homebrew/Cellar/some-other-formula/1.0.0/libexec/bin/python",
    )
    assert stable_interpreter(cellar_exe) == cellar_exe


def test_default_arg_uses_sys_executable(monkeypatch):
    # With executable=None, fall back to sys.executable and remap it.
    cellar_exe = "/opt/homebrew/Cellar/opentraces/0.4.4/libexec/bin/python"
    expected_opt = "/opt/homebrew/opt/opentraces/libexec/bin/python"
    monkeypatch.setattr(sys, "executable", cellar_exe)
    monkeypatch.setattr("os.path.exists", lambda p: p == expected_opt)
    monkeypatch.setattr(
        "os.path.realpath", _fake_realpath_into("/opt/homebrew", "opentraces")
    )
    assert stable_interpreter() == expected_opt


def test_real_fs_remaps_through_python_formula_symlink_chain(tmp_path):
    """Real-FS regression for the actual #86 defect.

    A brew venv's ``libexec/bin/python`` is itself a symlink onward to the
    ``python@3.x`` formula, so ``realpath`` of the whole interpreter resolves
    into ``Cellar/python@3.x/...`` and never this formula's tree. A guard that
    realpaths the interpreter binary therefore refuses to remap and leaves the
    dead Cellar pin in place. The mocked tests above pass for both the broken
    and the correct guard; this builds the exact chain on disk (no mocks) so it
    only passes when the guard inspects the ``opt/<formula>`` handle.
    """
    base = os.path.realpath(str(tmp_path))  # canonicalize (/var -> /private/var)
    # The python@3.x formula's actual interpreter (what the venv points at).
    py_formula_bin = (
        f"{base}/Cellar/python@3.12/3.12.13/Frameworks/Python.framework/bin"
    )
    os.makedirs(py_formula_bin)
    real_python = f"{py_formula_bin}/python3.12"
    with open(real_python, "w"):
        pass
    # The opentraces formula's current version, whose libexec python is a
    # symlink onward to the python@3.12 binary (mirrors a real brew venv).
    ot_libexec_bin = f"{base}/Cellar/opentraces/0.4.5/libexec/bin"
    os.makedirs(ot_libexec_bin)
    os.symlink(real_python, f"{ot_libexec_bin}/python")
    # opt/<formula> -> Cellar/<formula>/<current>
    os.makedirs(f"{base}/opt")
    os.symlink(f"{base}/Cellar/opentraces/0.4.5", f"{base}/opt/opentraces")

    # The dead pinned path from the bug report: 0.4.4 (its Cellar dir is gone).
    dead = f"{base}/Cellar/opentraces/0.4.4/libexec/bin/python"
    expected = f"{base}/opt/opentraces/libexec/bin/python"
    assert stable_interpreter(dead) == expected
    assert os.path.exists(stable_interpreter(dead))  # the remap actually resolves
