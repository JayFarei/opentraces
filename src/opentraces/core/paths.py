"""Shared path constants. No internal imports to avoid circular dependencies.

Layout:
    ~/.opentraces/
        config.json                # global config + project registry
        credentials                # HF token (0600)
        bucket/                    # local bucket-shaped sync substrate
            objects/traces/v1/     # content-addressed normalized TraceRecord envelopes
            objects/raw/v1/        # optional local raw source artifacts
            events/trail/v1/       # portable Trace Trail event exports
            projections/search/v1/ # immutable search projection builds
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


def bucket_dir() -> Path:
    """Return the local bucket-shaped storage root.

    Keep this as a function so tests that monkeypatch ``OPENTRACES_DIR``
    automatically redirect derived paths too.
    """

    return OPENTRACES_DIR / "bucket"


def bucket_projections_dir() -> Path:
    """Return the local projection area derived from canonical trace state."""

    return bucket_dir() / "projections"


def search_projection_root(version: str = "v1") -> Path:
    """Return the immutable build root for the local trace search projection."""

    return bucket_projections_dir() / "search" / version
