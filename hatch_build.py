"""Build hooks for optional packaged assets."""

from __future__ import annotations

from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class OptionalViewerBuildHook(BuildHookInterface):
    """Legacy viewer packaging hook.

    The raw-trace React viewer is decommissioned for now, so CLI builds should
    not package a stale ``web/viewer/dist`` bundle even if one exists locally.
    """

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        del build_data
        return


def get_build_hook() -> type[OptionalViewerBuildHook]:
    return OptionalViewerBuildHook
