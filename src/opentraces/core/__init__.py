"""Core business operations for opentraces.

This package centralizes state-mutating review and publish-flow helpers so that
the CLI, TUI, and web clients share a single implementation for each operation.

As of phase 4 of the core refactor, this package also owns the glue/domain
modules (config, paths, state, workflow, inbox, pipeline, processors) that
previously lived directly under ``opentraces.``. The old top-level paths remain
as deprecation shims for backwards compatibility.

Future work: state.py and workflow.py may eventually be merged into a single
``core/trace.py`` module, but are kept separate here to minimize risk.
"""

from .config import (
    Config,
    ProjectConfig,
    auth_identity,
    load_config,
    load_project_config,
    save_config,
    save_project_config,
)
from .state import StateManager, TraceStatus
from .workflow import (
    SUPPORTED_AGENTS,
    resolve_visible_stage,
    stage_label,
)

__all__ = [
    "Config",
    "ProjectConfig",
    "StateManager",
    "SUPPORTED_AGENTS",
    "TraceStatus",
    "auth_identity",
    "load_config",
    "load_project_config",
    "resolve_visible_stage",
    "save_config",
    "save_project_config",
    "stage_label",
]
