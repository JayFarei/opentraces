"""Helpers for CLI commands that read through the configured bucket remote."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def pull_remote_bucket_for_trace(*, force: bool = False) -> dict[str, Any]:
    """Pull the configured remote bucket and rebuild the local read model.

    Builds the compact search snapshot (the read model that ``trace
    query/map/get`` serve from), not the deprecated legacy ``index.db`` (issue
    #89). ``trace get --remote-bucket`` itself resolves through the bucket union,
    so this snapshot rebuild is what lets a subsequent local ``trace query`` see
    the freshly pulled traces.
    """

    from ..core.bucket_remote import remote_pull
    from ..core.trace_search_snapshot import build_trace_search_snapshot

    remote = remote_pull(force=force)
    snapshot = build_trace_search_snapshot()
    return {
        "state": "pulled",
        "remote": remote,
        "search_snapshot": {
            "path": str(snapshot.path),
            "trace_count": snapshot.trace_count,
        },
    }


def pull_remote_bucket_for_trail(
    repo: Path,
    *,
    force: bool = False,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """Pull the configured remote bucket and replay exported TrailEvents."""

    from ..core.bucket_remote import remote_pull
    from ..core.bucket_store import restore_trail_events_to_repo

    remote = remote_pull(force=force)
    replay = restore_trail_events_to_repo(repo, repo_id=repo_id, force=force)
    return {
        "state": "pulled",
        "remote": remote,
        "replay": replay,
    }
