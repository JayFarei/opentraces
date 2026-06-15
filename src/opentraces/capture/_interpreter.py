"""Resolve a stable, upgrade-proof interpreter path for baked hook commands.

When opentraces persists a hook command (e.g. a Git post-commit hook or a
Claude Code settings hook), it bakes the current Python interpreter path into
that command so the hook keeps running the same opentraces install. For most
install shapes (pipx venv, editable venv, system python) the interpreter path
is already stable and survives upgrades unchanged.

Homebrew is the exception. A brew install puts the interpreter under a
version-pinned Cellar directory:

    /opt/homebrew/Cellar/opentraces/0.4.4/libexec/bin/python

On ``brew upgrade`` the old ``Cellar/<formula>/<version>`` tree is deleted, so
any hook command that baked that path now points at a missing binary and the
hook silently breaks. Homebrew also maintains a version-independent symlink:

    /opt/homebrew/opt/opentraces/libexec/bin/python  ->  Cellar/opentraces/<current>/...

That ``opt/<formula>`` path is the stable handle: it always resolves to the
currently-installed version. So when we detect a version-pinned Cellar
interpreter we remap it to the matching ``opt/<formula>`` path, but only if
that symlink actually exists and currently resolves back into the same
formula's Cellar tree (never emit a non-existent or mis-targeted path).

See issue #86 (brew upgrade deletes Cellar/<version>; opt/<formula> is the
stable symlink to bake into persisted hook commands).
"""
from __future__ import annotations

import os
import re
import sys

# Matches a Homebrew Cellar interpreter path on macOS (/opt/homebrew, /usr/local)
# or Linux (/home/linuxbrew/.linuxbrew): <prefix>/Cellar/<formula>/<version>/<rest>.
# Issue #86: the <version> segment is what disappears on upgrade.
_CELLAR_RE = re.compile(
    r"^(?P<prefix>.*)/Cellar/(?P<formula>[^/]+)/(?P<version>[^/]+)/(?P<rest>.+)$"
)


def stable_interpreter(executable: str | None = None) -> str:
    """Return an interpreter path safe to bake into a persisted hook command.

    If ``executable`` is a Homebrew version-pinned Cellar path
    (``.../Cellar/<formula>/<version>/<rest>``) and the version-independent
    ``.../opt/<formula>/<rest>`` symlink exists AND currently resolves back into
    that Cellar formula's tree, return the stable ``opt`` path. Otherwise return
    the input unchanged.

    Non-Homebrew interpreters (pipx venv, editable venv, system python) are
    already stable and pass through untouched. Defaults to ``sys.executable``
    when called with no argument.

    See issue #86.
    """
    exe = executable or sys.executable or "python3"
    m = _CELLAR_RE.match(exe)
    if not m:
        return exe
    prefix, formula, rest = m.group("prefix"), m.group("formula"), m.group("rest")
    opt_dir = f"{prefix}/opt/{formula}"
    opt_path = f"{opt_dir}/{rest}"
    try:
        # Guard on the formula-level ``opt/<formula>`` handle, NOT the fully
        # resolved interpreter. A brew venv's ``libexec/bin/python`` is itself a
        # symlink onward to the ``python@3.x`` formula, so realpath of the whole
        # interpreter path lands in ``Cellar/python@3.x/...`` and never in this
        # formula's tree. The ``opt/<formula>`` directory symlink, however,
        # points straight at ``Cellar/<formula>/<current>``; if it resolves back
        # into this formula's Cellar tree the handle is live, not stale/foreign.
        if os.path.exists(opt_path) and os.path.realpath(opt_dir).startswith(
            f"{prefix}/Cellar/{formula}/"
        ):
            return opt_path
    except OSError:
        pass
    return exe
