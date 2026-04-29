"""Local cache of a remote HF dataset's shape for dedup + lineage checks.

Persists two pieces of information per remote dataset:

- ``content_hashes`` — the set used by the push-time dedup filter (same
  data as the legacy ``fetch_remote_content_hashes`` return value).
- ``session_generations`` — ``{session_id: max(generation_index)}``. Used
  to tell whether a local trace is a newer generation of something the
  remote dataset already has, so the TUI can render a "supersedes
  remote" hint on refresh without re-fetching every shard.

The cache lives at ``~/.opentraces/projects/<slug>/remote_index.json``
(sibling of ``state.json``). The legacy trace-publish path writes it after
fetching and dataset remote work is migrating this responsibility under
``opentraces dataset pull/publish``. Consumers check ``is_stale`` against a
TTL before using it for UX hints; staleness is non-fatal, just means the hint
is suppressed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILENAME = "remote_index.json"


@dataclass
class RemoteIndex:
    """Snapshot of a remote HF dataset's dedup + lineage state."""

    repo_id: str
    fetched_at: str
    content_hashes: set[str] = field(default_factory=set)
    session_generations: dict[str, int] = field(default_factory=dict)

    @classmethod
    def empty(cls, repo_id: str) -> "RemoteIndex":
        return cls(repo_id=repo_id, fetched_at=_utcnow_iso())

    def is_stale(self, ttl_seconds: int) -> bool:
        try:
            fetched = datetime.fromisoformat(self.fetched_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        return age > ttl_seconds

    def supersedes_remote(self, session_id: str, generation_index: int) -> bool:
        """True iff ``generation_index`` is strictly newer than what's cached."""
        prior = self.session_generations.get(session_id)
        if prior is None:
            return False
        return generation_index > prior

    def to_dict(self) -> dict:
        return {
            "repo_id": self.repo_id,
            "fetched_at": self.fetched_at,
            "content_hashes": sorted(self.content_hashes),
            "session_generations": self.session_generations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RemoteIndex":
        return cls(
            repo_id=data["repo_id"],
            fetched_at=data.get("fetched_at", _utcnow_iso()),
            content_hashes=set(data.get("content_hashes", []) or []),
            session_generations=dict(data.get("session_generations", {}) or {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2))
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "RemoteIndex | None":
        if not path.exists():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            logger.warning("remote_index load failed at %s: %s", path, exc)
            return None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cache_path_for(project_dir_home: Path) -> Path:
    """Return the cache path given the per-project home (``~/.opentraces/projects/<slug>/``)."""
    return project_dir_home / CACHE_FILENAME
