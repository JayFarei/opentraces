"""Textual-based TUI for the OpenTraces repo inbox."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    """Entry point for the TUI, parseable from sys.argv."""
    from ...config import STAGING_DIR

    staging_dir = STAGING_DIR
    fullscreen = False
    serve = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--staging-dir" and i + 1 < len(args):
            staging_dir = Path(args[i + 1])
            i += 2
        elif args[i] == "--fullscreen":
            fullscreen = True
            i += 1
        elif args[i] == "--serve":
            serve = True
            i += 1
        else:
            i += 1

    from .app import OpenTracesApp

    app = OpenTracesApp(staging_dir=staging_dir, fullscreen=fullscreen)
    if serve:
        # Replace the default on_mount to push PipelineScreen instead
        original_on_mount = app.on_mount

        def _serve_on_mount() -> None:
            app._load_project_context()
            app.push_pipeline()
            app.set_interval(0.5, app._check_dirty)

        app.on_mount = _serve_on_mount  # type: ignore[assignment]
    app.run()


# Re-export for convenience
def __getattr__(name: str) -> object:
    if name == "OpenTracesApp":
        from .app import OpenTracesApp

        return OpenTracesApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
