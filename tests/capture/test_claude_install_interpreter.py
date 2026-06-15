"""Claude Code hook commands bake a stable, upgrade-proof interpreter.

`_hook_command` must route the interpreter through
`opentraces.capture._interpreter.stable_interpreter` so a Homebrew
version-pinned Cellar path never gets persisted into settings.json (issue
#86: the Cellar/<version> tree is deleted on `brew upgrade`, breaking the
hook). For non-Homebrew installs the interpreter passes through unchanged.
"""
from __future__ import annotations

import shlex

from opentraces.capture._interpreter import stable_interpreter
from opentraces.capture.claude_code import install as cc_install


def test_hook_command_uses_stable_interpreter(monkeypatch):
    """The command's interpreter is exactly stable_interpreter()."""
    monkeypatch.setattr(
        cc_install,
        "stable_interpreter",
        lambda: "/stable/bin/python",
    )
    cmd = cc_install._hook_command("/some/hooks/opentraces_on_stop.py")
    parts = shlex.split(cmd)
    assert parts[0] == "/stable/bin/python"
    assert parts[1] == "/some/hooks/opentraces_on_stop.py"


def test_hook_command_default_matches_resolver():
    """With no monkeypatch the baked interpreter equals the resolver output."""
    cmd = cc_install._hook_command("/some/hooks/opentraces_on_stop.py")
    parts = shlex.split(cmd)
    assert parts[0] == stable_interpreter()


def test_hook_command_no_cellar_version_when_opt_present(monkeypatch, tmp_path):
    """Regression sentinel: a Cellar interpreter is remapped to opt (no version).

    Build a fake Homebrew layout where the opt/<formula> symlink resolves
    back into the Cellar formula tree, point sys.executable at the
    version-pinned Cellar binary, and assert the produced command contains
    NO Cellar/<formula>/<version>/ segment.
    """
    prefix = tmp_path / "brew"
    formula = "opentraces"
    version = "0.4.4"
    rest = "libexec/bin/python"

    cellar_bin = prefix / "Cellar" / formula / version / rest
    cellar_bin.parent.mkdir(parents=True)
    cellar_bin.write_text("#!/bin/sh\n")

    # opt/<formula> -> Cellar/<formula>/<version> so opt/<formula>/<rest> resolves
    # into the formula's Cellar tree.
    opt_formula = prefix / "opt" / formula
    opt_formula.parent.mkdir(parents=True)
    opt_formula.symlink_to(prefix / "Cellar" / formula / version)

    cellar_exe = str(cellar_bin)
    # Drive the real resolver via sys.executable.
    import opentraces.capture._interpreter as interp
    monkeypatch.setattr(interp.sys, "executable", cellar_exe)

    cmd = cc_install._hook_command(str(prefix / "hooks" / "opentraces_on_stop.py"))
    parts = shlex.split(cmd)

    # Resolver remapped Cellar -> opt: no version-pinned segment survives.
    assert f"/Cellar/{formula}/{version}/" not in parts[0]
    assert parts[0] == f"{prefix}/opt/{formula}/{rest}"
