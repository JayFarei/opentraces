"""Phase-safe registration helper for ``ot watcher``.

Isolated so Phase 5/6's work in ``cli/__init__.py`` doesn't conflict with
Phase 3. If cli/__init__.py imports this module, the group is wired in.
Otherwise the Phase 7 merge can call :func:`register_watcher_commands`
from anywhere.
"""

from __future__ import annotations

import click

from .watcher import watcher_group


def register_watcher_commands(root_group: click.Group) -> None:
    """Attach the ``watcher`` subgroup to ``root_group``.

    Idempotent: re-registering is a no-op.
    """
    if "watcher" in root_group.commands:
        return
    root_group.add_command(watcher_group)


def register(cli: click.Group) -> None:  # alias per phase 3 spec
    register_watcher_commands(cli)
