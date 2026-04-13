"""Shared path constants. No internal imports to avoid circular dependencies.

Layout:
    ~/.opentraces/
        config.json                # global config + project registry
        credentials                # HF token (0600)
        projects/<slug>/           # per-project, machine-local
            traces/*.jsonl         # captured traces
            state.json             # runtime state (statuses, offsets, commits)
            .lock                  # per-project upload lock

Per-project, committable (lives in the repo):
    <repo>/.opentraces.json        # marker: project_id + portable policy
"""

from pathlib import Path

OPENTRACES_DIR = Path.home() / ".opentraces"
CONFIG_PATH = OPENTRACES_DIR / "config.json"
CREDENTIALS_PATH = OPENTRACES_DIR / "credentials"
PROJECTS_DIR = OPENTRACES_DIR / "projects"

MARKER_FILENAME = ".opentraces.json"
