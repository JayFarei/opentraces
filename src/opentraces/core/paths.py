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


def otel_staging_dir() -> Path:
    """Plan 078 staging area for cross-process OTel session snapshots.

    Lives under the existing ``STAGING_DIR`` convention so the OTel
    capture source reuses the same on-disk layer as the default-inbox
    staging (Plan 58). Each session_id is one ``<id>.json`` file
    refreshed by the foreground daemon; the ``flush`` verb consumes it.
    """

    return STAGING_DIR / "otel"


def raw_bodies_dir() -> Path:
    """Return the OTLP raw API bodies directory (plan 078).

    Claude Code drops ``<request_id>.{request,response}.json`` files
    here when ``OTEL_LOG_RAW_API_BODIES=file:<dir>`` is set. The OTLP
    receiver's raw-body watcher pairs them and feeds the byte-perfect
    Anthropic Messages API content into the Context Tree substrate.
    """

    return OPENTRACES_DIR / "raw-bodies"


def otlp_receiver_pid_path() -> Path:
    """PID file written by ``opentraces capture-otlp start`` (plan 078)."""

    return OPENTRACES_DIR / "otlp-receiver.pid"


def otlp_receiver_status_path() -> Path:
    """Status file refreshed by the OTLP receiver process (plan 078)."""

    return OPENTRACES_DIR / "otlp-receiver.status.json"


def otlp_receiver_log_dir() -> Path:
    """Log directory for the daemonized OTLP receiver (plan 078)."""

    return OPENTRACES_DIR / "logs"
