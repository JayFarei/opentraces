"""Background attribution watcher (plan-043 phase 3).

Polls enlisted projects on a timer (default 300s) and runs incremental
backfill when either new commits or new Claude Code JSONL activity appears.
Installed as a launchd agent on macOS or a systemd user timer on Linux.

Public API lives in :mod:`opentraces.watcher.daemon` and
:mod:`opentraces.watcher.installer`. The CLI surface is
``ot watcher {status,start,stop,restart,uninstall,tick}``.
"""

from .daemon import TickReport, discover_enlisted_projects, run_forever, run_once

__all__ = [
    "TickReport",
    "discover_enlisted_projects",
    "run_forever",
    "run_once",
]
