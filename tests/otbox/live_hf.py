"""Ephemeral live HuggingFace repos for the otbox ``live_hf`` lane.

This module provisions, tracks, and cleans up the real private HF dataset
repos that the live journeys push to. It depends only on ``huggingface_hub``
(never on ``opentraces``) so it can run as the test harness, outside any box
subprocess. The opentraces CLI inside the box reaches the same repos via the
``live_hf`` ``isolated_env`` seam (real token, no fake-remote env vars).

Lifecycle (plan: ephemeral, keep-on-failure):
    provision_live_repos(box_id) -> two private repos under the token owner
    cleanup_live_repos(repos, passed=True)  -> delete both
    cleanup_live_repos(repos, passed=False) -> keep + print URLs for debug
    sweep_orphans()  -> delete stale otbox-live-* repos from earlier failures
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Prefix every ephemeral repo so sweep_orphans can recognise our own litter.
REPO_PREFIX = "otbox-live"


def resolve_token() -> str | None:
    """The live-lane token, mirroring isolated_env / config resolution.

    Returns ``None`` to let ``HfApi`` fall back to the cached login token
    (``~/.cache/huggingface/token``), which is how the developer logs in.
    """
    return os.environ.get("OPENTRACES_LIVE_HF_TOKEN") or os.environ.get("HF_TOKEN")


def _api(token: str | None = None):
    from huggingface_hub import HfApi

    return HfApi(token=token if token is not None else resolve_token())


def _sanitize(box_id: str) -> str:
    """HF repo names allow [A-Za-z0-9._-]; collapse anything else to '-'."""
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in box_id)


@dataclass(frozen=True)
class LiveRepos:
    """The pair of ephemeral private repos provisioned for one box."""

    box_id: str
    owner: str
    bucket_repo: str
    dataset_repo: str
    token: str | None

    @property
    def repo_ids(self) -> tuple[str, str]:
        return (self.bucket_repo, self.dataset_repo)


# Process-level registry: box_id -> LiveRepos. The journey runner's
# ``_context()`` reads this to expand {live_bucket_repo}/{live_dataset_repo}.
_REGISTRY: dict[str, LiveRepos] = {}


def get_live_repos(box_id: str) -> LiveRepos | None:
    """Return the provisioned repos for a box, or None (fake lane)."""
    return _REGISTRY.get(box_id)


def provision_live_repos(box_id: str, *, token: str | None = None) -> LiveRepos:
    """Reserve + register the two ephemeral repo ids for ``box_id``.

    The repos are deliberately NOT created here — the real CLI flows under
    test create them (``bucket remote push`` -> create_repo(exist_ok=True);
    ``dataset remote create`` -> create_repo(exist_ok=False) on a fresh
    name). That keeps repo creation inside the code path being exercised and
    avoids a pre-create colliding with ``dataset remote create``. Owner comes
    from ``whoami()`` so the repos land in the token's namespace (Jayfarei).
    Cleanup deletes by name with ``missing_ok`` so an un-created repo is a
    no-op.
    """
    token = token if token is not None else resolve_token()
    owner = _api(token).whoami()["name"]
    slug = _sanitize(box_id)
    repos = LiveRepos(
        box_id=box_id,
        owner=owner,
        bucket_repo=f"{owner}/{REPO_PREFIX}-bucket-{slug}",
        dataset_repo=f"{owner}/{REPO_PREFIX}-ds-{slug}",
        token=token,
    )
    _REGISTRY[box_id] = repos
    return repos


def cleanup_live_repos(repos: LiveRepos | None, *, passed: bool) -> list[str]:
    """Delete the repos on success; keep + announce them on failure.

    Returns the list of repo_ids that were deleted (empty when kept).
    """
    if repos is None:
        return []
    _REGISTRY.pop(repos.box_id, None)
    if not passed:
        for repo_id in repos.repo_ids:
            print(
                f"[otbox live_hf] KEPT FOR DEBUG: "
                f"https://huggingface.co/datasets/{repo_id}"
            )
        return []
    api = _api(repos.token)
    deleted: list[str] = []
    for repo_id in repos.repo_ids:
        try:
            api.delete_repo(repo_id=repo_id, repo_type="dataset", missing_ok=True)
            deleted.append(repo_id)
        except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
            print(f"[otbox live_hf] WARN: could not delete {repo_id}: {exc}")
    return deleted


def sweep_orphans(*, ttl_hours: int = 24, token: str | None = None) -> list[str]:
    """Delete stale ``otbox-live-*`` repos left by earlier kept-on-failure runs.

    Only repos older than ``ttl_hours`` are removed, so a concurrent live run's
    fresh repos are never touched. Returns the deleted repo_ids.
    """
    api = _api(token)
    owner = api.whoami()["name"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    deleted: list[str] = []
    for ds in api.list_datasets(author=owner):
        name = ds.id.split("/", 1)[-1]
        if not name.startswith(REPO_PREFIX):
            continue
        last_mod = getattr(ds, "last_modified", None)
        if last_mod is not None:
            if last_mod.tzinfo is None:
                last_mod = last_mod.replace(tzinfo=timezone.utc)
            if last_mod > cutoff:
                continue  # too fresh — may belong to a live run in flight
        try:
            api.delete_repo(repo_id=ds.id, repo_type="dataset", missing_ok=True)
            deleted.append(ds.id)
        except Exception as exc:  # noqa: BLE001
            print(f"[otbox live_hf] WARN: orphan sweep could not delete {ds.id}: {exc}")
    return deleted
