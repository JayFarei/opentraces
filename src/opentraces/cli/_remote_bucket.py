"""Helpers for CLI commands that read through the configured bucket remote."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def pull_remote_bucket_for_trace(
    *,
    force: bool = False,
    build_projection: bool = False,
) -> dict[str, Any]:
    """Pull the configured remote bucket and rebuild local trace projections."""

    from ..core.bucket_remote import remote_pull
    from ..core.trace_index import rebuild_index

    remote = remote_pull(force=force)
    index = rebuild_index()
    payload: dict[str, Any] = {
        "state": "pulled",
        "remote": remote,
        "index": {
            "path": str(index.index_path),
            "trace_count": index.trace_count,
            "unit_count": index.unit_count,
            "map_node_count": index.map_node_count,
        },
    }
    if build_projection:
        from ..core.search_projection import build_search_projection

        projection = build_search_projection(index_path=index.index_path)
        payload["search_projection"] = projection.as_dict()
    return payload


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
