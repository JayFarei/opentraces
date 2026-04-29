"""Shared path constants. No internal imports to avoid circular dependencies.

Layout:
    ~/.opentraces/
        config.json                # global config + project registry
        credentials                # HF token (0600)
        projects/<slug>/           # per-project, machine-local (canonical layer)
            traces/*.jsonl         # captured traces
            state.json             # runtime state (statuses, offsets, commits)
            .lock                  # per-project upload lock
        staging/*.jsonl            # default-inbox staging layer (Plan 58)

Per-project, committable (lives in the repo):
    <repo>/.opentraces.json        # marker: project_id + portable policy
"""

from pathlib import Path

OPENTRACES_DIR = Path.home() / ".opentraces"
CONFIG_PATH = OPENTRACES_DIR / "config.json"
CREDENTIALS_PATH = OPENTRACES_DIR / "credentials"
PROJECTS_DIR = OPENTRACES_DIR / "projects"
STAGING_DIR = OPENTRACES_DIR / "staging"

MARKER_FILENAME = ".opentraces.json"
