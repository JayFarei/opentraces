"""The git post-commit hook bakes a stable, upgrade-proof interpreter path.

Issue #86: a brew install puts the interpreter under a version-pinned
``Cellar/<formula>/<version>/...`` tree that ``brew upgrade`` deletes, silently
breaking any hook command that baked it. The owned-hook renderer must route the
interpreter choice through ``stable_interpreter`` so a Cellar path is remapped
to the version-independent ``opt/<formula>`` symlink before it is persisted.
"""

from __future__ import annotations

from opentraces.capture.git import install as git_install


def test_owned_hook_uses_opt_path_when_resolver_remaps(monkeypatch):
    """A brew Cellar interpreter is remapped to the stable opt path in the hook."""
    cellar = "/opt/homebrew/Cellar/opentraces/0.4.4/libexec/bin/python"
    opt = "/opt/homebrew/opt/opentraces/libexec/bin/python"

    monkeypatch.setattr(git_install.sys, "executable", cellar)
    monkeypatch.setattr(
        git_install, "stable_interpreter", lambda exe=None: opt
    )

    content = git_install._owned_hook_content()

    assert opt in content
    assert "/Cellar/opentraces/0.4.4/" not in content
    # The PATH-fallback shape must survive untouched.
    assert "|| true" in content
    assert "|| opentraces _run-post-commit-hook" in content


def test_owned_hook_remaps_real_cellar_path(monkeypatch, tmp_path):
    """End-to-end: a simulated-present opt symlink drives the real resolver."""
    prefix = tmp_path / "brew"
    cellar_bin = prefix / "Cellar" / "opentraces" / "0.4.4" / "libexec" / "bin"
    cellar_bin.mkdir(parents=True)
    cellar_py = cellar_bin / "python"
    cellar_py.write_text("#!/bin/sh\n")

    # opt/<formula>/<rest> resolves back into this formula's Cellar tree.
    opt_root = prefix / "opt"
    opt_root.mkdir(parents=True)
    (opt_root / "opentraces").symlink_to(prefix / "Cellar" / "opentraces" / "0.4.4")
    opt_py = opt_root / "opentraces" / "libexec" / "bin" / "python"

    monkeypatch.setattr(git_install.sys, "executable", str(cellar_py))

    content = git_install._owned_hook_content()

    assert str(opt_py) in content
    assert "/Cellar/opentraces/0.4.4/" not in content
    assert "|| true" in content


def test_owned_hook_passes_through_non_cellar_interpreter(monkeypatch):
    """A venv/system interpreter is already stable and baked unchanged."""
    venv_py = "/Users/dev/project/.venv/bin/python"
    monkeypatch.setattr(git_install.sys, "executable", venv_py)

    content = git_install._owned_hook_content()

    assert venv_py in content
    assert "|| true" in content
