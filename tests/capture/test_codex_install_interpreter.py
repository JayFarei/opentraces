"""The Codex hook command bakes a stable interpreter path (issue #86).

``_hook_command`` must route ``sys.executable`` through the shared
``stable_interpreter`` resolver so a baked Codex hook command survives a
``brew upgrade`` (which deletes the version-pinned ``Cellar/<version>`` tree).
These tests assert the resolved interpreter is what lands in the command
string, and that a simulated Cellar+opt install never bakes a version-pinned
``/Cellar/<formula>/<version>/`` segment.
"""
from __future__ import annotations

import re
import sys

from opentraces.capture.codex_cli import install


def test_hook_command_uses_resolved_interpreter(monkeypatch):
    sentinel = "/sentinel/interpreter/python"
    monkeypatch.setattr(install, "stable_interpreter", lambda: sentinel)
    cmd = install._hook_command("on_pre_tool_use")
    assert sentinel in cmd
    assert cmd == f"{sentinel} -m opentraces.capture.codex_cli.hooks.on_pre_tool_use"


def test_hook_command_drops_cellar_version_segment(monkeypatch):
    # REGRESSION SENTINEL: sys.executable is a version-pinned Homebrew Cellar
    # path and the opt symlink is simulated-present (resolving back into the
    # same formula's Cellar tree). The produced command must NOT contain a
    # /Cellar/<formula>/<version>/ segment.
    cellar_exe = "/opt/homebrew/Cellar/opentraces/0.4.4/libexec/bin/python"
    opt_path = "/opt/homebrew/opt/opentraces/libexec/bin/python"
    monkeypatch.setattr(sys, "executable", cellar_exe)
    monkeypatch.setattr("os.path.exists", lambda p: p == opt_path)
    monkeypatch.setattr(
        "os.path.realpath",
        lambda p: "/opt/homebrew/Cellar/opentraces/9.9.9/libexec/bin/python",
    )
    cmd = install._hook_command("on_pre_tool_use")
    assert opt_path in cmd
    assert not re.search(r"/Cellar/[^/]+/[0-9][^/]*/", cmd)
