"""Publish-flow leaf helpers shared by the CLI push command.

The full push flow in ``cli.py`` is intentionally left inline: it interleaves
click/echo/sys.exit/interactive-prompt concerns with upload logic, and a
mechanical extraction risked behavior drift. Only leaf state mutations are
extracted here.

Remote-resolution helpers (_resolve_push_target, _resolve_push_visibility,
_persist_push_target) were relocated here from cli/publish.py so that
test_publish_flow.py can import them from a stable core location without
depending on the CLI layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from .state import StateManager, TraceStatus


# ---------------------------------------------------------------------------
# Remote-resolution helpers (relocated from cli/publish.py)
# ---------------------------------------------------------------------------

def _normalize_repo_id(repo: str, username_hint: str | None = None) -> str:
    """Normalize a repo reference to ``owner/name``."""
    if "://" in repo:
        repo = repo.split("://", 1)[1]
    if "/" in repo or not username_hint:
        return repo
    return f"{username_hint}/{repo}"


def _remote_repo_id(remote_name: str, remotes: dict) -> str | None:
    """Return the dataset repo_id stored for *remote_name* if available."""
    cfg = remotes.get(remote_name) or {}
    url = cfg.get("url")
    if isinstance(url, str) and url:
        return url.split("://", 1)[1] if "://" in url else url
    if remote_name and remote_name != "origin":
        return remote_name
    return None


def _match_remote_key(remotes: dict, repo_id: str) -> str | None:
    """Find the remote key that already points at *repo_id*."""
    for name, cfg in remotes.items():
        url = cfg.get("url") if isinstance(cfg, dict) else None
        if name == repo_id:
            return name
        if isinstance(url, str):
            normalized = url.split("://", 1)[1] if "://" in url else url
            if normalized == repo_id:
                return name
    return None


def _resolve_push_target(proj_config: dict, username: str, repo: str | None = None) -> tuple[str, str]:
    """Return ``(remote_name, repo_id)`` for the current push/publish run."""
    remotes = proj_config.get("remotes") or {}
    active_remote = proj_config.get("active_remote")

    if repo:
        repo_id = _normalize_repo_id(repo, username)
        remote_name = _match_remote_key(remotes, repo_id) or repo_id
        return remote_name, repo_id

    if active_remote and active_remote in remotes:
        repo_id = _remote_repo_id(active_remote, remotes) or active_remote
        return active_remote, repo_id

    if len(remotes) == 1:
        remote_name = next(iter(remotes))
        repo_id = _remote_repo_id(remote_name, remotes) or remote_name
        return remote_name, repo_id

    from .workflow import DEFAULT_REMOTE_NAME
    fallback_repo = f"{username}/{DEFAULT_REMOTE_NAME}"
    return fallback_repo, fallback_repo


def _resolve_push_visibility(
    proj_config: dict,
    remote_name: str,
    *,
    default_visibility: str,
    private: bool,
    public: bool,
) -> str:
    """Return the visibility that should be used for this push/publish run."""
    if public:
        return "public"
    if private:
        return "private"

    remotes = proj_config.get("remotes") or {}
    remote_cfg = remotes.get(remote_name) or {}
    visibility = remote_cfg.get("visibility")
    if visibility in {"public", "private"}:
        return visibility

    proj_default = proj_config.get("default_visibility")
    if proj_default in {"public", "private"}:
        return proj_default
    return default_visibility


def _persist_push_target(
    project_dir: Path,
    proj_config: dict,
    remote_name: str,
    repo_id: str,
    visibility: str,
) -> None:
    """Persist the chosen push target back into the new remotes shape."""
    from .config import save_project_config
    payload = dict(proj_config)
    payload.pop("remote", None)
    payload.pop("visibility", None)

    remotes = dict(payload.get("remotes") or {})
    remote_cfg = dict(remotes.get(remote_name) or {})
    remote_cfg["url"] = f"hf://{repo_id}"
    remote_cfg["visibility"] = visibility
    remotes[remote_name] = remote_cfg
    payload["remotes"] = remotes
    if not payload.get("active_remote"):
        payload["active_remote"] = remote_name
    save_project_config(project_dir, payload)


def mark_uploaded(
    state: StateManager,
    trace_ids: Iterable[str],
    *,
    remote_name: Optional[str] = None,
) -> None:
    """Mark the given trace_ids as UPLOADED.

    When ``remote_name`` is supplied (the post-Step-3 path), each trace's
    ``uploaded_to[remote_name]`` is written via
    ``StateManager.mark_uploaded_to`` so the per-remote replay flow works.

    When called without ``remote_name`` (legacy / transitional callers),
    fall back to the old behavior of just flipping status to UPLOADED
    without recording per-remote provenance. This keeps in-flight code
    paths working while step 3 is finishing — they simply won't benefit
    from per-remote tracking until they thread the active remote name
    through.
    """
    for trace_id in trace_ids:
        if remote_name is None:
            state.set_trace_status(trace_id, TraceStatus.UPLOADED)
        else:
            state.mark_uploaded_to(trace_id, remote_name)


def mark_failed(
    state: StateManager,
    trace_id: str,
    error: Optional[str],
) -> None:
    """Mark a single trace as FAILED with the given error string."""
    state.set_trace_status(trace_id, TraceStatus.FAILED, error=error)
