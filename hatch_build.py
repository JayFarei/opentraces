"""Build hooks for optional packaged assets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class OptionalViewerBuildHook(BuildHookInterface):
    """Package the built React viewer only when the bundle exists."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        del build_data

        viewer_dist = Path(self.root) / "web" / "viewer" / "dist"
        if not (viewer_dist / "index.html").is_file():
            return

        if self.target_name == "wheel":
            self.build_config.build_force_include[str(viewer_dist)] = "opentraces/static/viewer"
        elif self.target_name == "sdist":
            self.build_config.build_force_include[str(viewer_dist)] = "web/viewer/dist"


def get_build_hook() -> type[OptionalViewerBuildHook]:
    return OptionalViewerBuildHook
