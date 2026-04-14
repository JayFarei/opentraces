"""Repository identity helpers for plan-043 phase 6.

Root-commit SHA is a stable, repo-intrinsic identifier: it survives a
``git mv`` of the project directory and is identical across clones. We use
it to:

* Match an existing ``~/.opentraces/projects/<slug>/`` dir when a user
  moves a repo (e.g. ``mv /old/path /new/path``) so attribution history
  carries over without needing to re-backfill from scratch.
* Discover Claude Code JSONL corpora that were recorded under the old
  path — ``~/.claude/projects/<encoded>/`` is keyed by the cwd at the
  time of capture, so a move would otherwise orphan every JSONL.

Nothing here writes state on its own. Callers combine ``root_commit_sha``
with ``.opentraces.json`` fields (``root_commit_sha``,
``first_run_backfill_decision``) to drive ``ot init`` behavior.
"""
from __future__ import annotations

import functools
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import PROJECTS_DIR


# --------------------------------------------------------------------------- #
# Claude Code path encoding — shared with enrichment.git.attribution
# --------------------------------------------------------------------------- #

def encode_claude_path(path: Path) -> str:
    """Encode an absolute path the way Claude Code's ``~/.claude/projects/``
    dir does: every character that is not alphanumeric or a hyphen becomes
    a single ``-``. Hyphens are preserved.

    Examples (pinned in tests)::

        /private/tmp/ot-h-small_tweak  -> -private-tmp-ot-h-small-tweak
        /Users/a/b.c                   -> -Users-a-b-c
    """
    s = str(path.resolve())
    return "".join(c if c.isalnum() or c == "-" else "-" for c in s)


# --------------------------------------------------------------------------- #
# Root-commit SHA
# --------------------------------------------------------------------------- #

@functools.lru_cache(maxsize=256)
def _cached_root(repo_str: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", repo_str, "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.strip().splitlines()
    if not out:
        return None
    # A repo can have multiple root commits (orphan branches merged in);
    # we pick the first, which matches `git log --reverse` ordering.
    return out[0].strip() or None


def root_commit_sha(repo: Path) -> str | None:
    """Return the SHA of the first commit in ``repo`` (or None for empty
    repos / non-repos). Cached per-process by repo path."""
    try:
        return _cached_root(str(repo.resolve()))
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Project matching across directory moves
# --------------------------------------------------------------------------- #

@dataclass
class ProjectMatch:
    """An existing ``~/.opentraces/projects/<slug>/`` that matches a given
    root-commit SHA. Returned by :func:`discover_matching_project` when a
    user has moved their project dir and re-runs ``ot init`` under a new
    path."""
    slug: str
    old_path: Path
    traces_dir: Path
    attributions_dir: Path


def _iter_project_jsons(root: Path = PROJECTS_DIR):
    """Yield (slug, dict) for every ``project.json`` stored under the
    per-project dirs. Swallows read/parse errors — a single bad file
    shouldn't break discovery."""
    if not root.is_dir():
        return
    for slug_dir in sorted(root.iterdir()):
        if not slug_dir.is_dir():
            continue
        pj = slug_dir / "project.json"
        if not pj.is_file():
            continue
        try:
            data = json.loads(pj.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        yield slug_dir.name, slug_dir, data


def discover_matching_project(
    root_sha: str, *, projects_root: Path | None = None
) -> ProjectMatch | None:
    """Find the first per-project dir whose ``project.json`` records
    ``root_commit_sha == root_sha``. Used on ``ot init`` to detect that a
    project directory was moved and reuse the old attribution state.

    ``projects_root`` override exists so tests can point at a fixture.
    """
    if not root_sha:
        return None
    root = projects_root if projects_root is not None else PROJECTS_DIR
    for slug, slug_dir, data in _iter_project_jsons(root):
        if data.get("root_commit_sha") != root_sha:
            continue
        old_path_str = data.get("path") or data.get("project_dir")
        old_path = Path(old_path_str) if old_path_str else slug_dir
        return ProjectMatch(
            slug=slug,
            old_path=old_path,
            traces_dir=slug_dir / "traces",
            attributions_dir=slug_dir / "attributions",
        )
    return None


def write_project_identity(
    slug_dir: Path, *, project_dir: Path, root_sha: str | None
) -> None:
    """Persist the ``project.json`` sidecar inside
    ``~/.opentraces/projects/<slug>/`` so :func:`discover_matching_project`
    can find this project again after a move.

    Idempotent: merges with any existing content.
    """
    slug_dir.mkdir(parents=True, exist_ok=True)
    pj = slug_dir / "project.json"
    data: dict = {}
    if pj.is_file():
        try:
            data = json.loads(pj.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
    data["path"] = str(project_dir.resolve())
    if root_sha:
        data["root_commit_sha"] = root_sha
    tmp = pj.with_suffix(pj.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(pj)


# --------------------------------------------------------------------------- #
# Claude JSONL corpus discovery
# --------------------------------------------------------------------------- #

def _claude_projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def discover_claude_jsonl_corpus(repo: Path) -> list[Path]:
    """List JSONL files under ``~/.claude/projects/`` that are associated
    with ``repo`` — either via the current path's encoding or via any
    other encoded path whose root-commit SHA matches ``repo``'s.

    The "or via root-commit SHA" branch is what makes a ``mv /old /new``
    rediscover the old corpus on the next ``ot init`` at ``/new``: we
    walk every sibling dir under ``~/.claude/projects/`` and, for each
    one whose encoded path decodes to a real repo with the same root
    SHA, include its JSONLs.
    """
    out: list[Path] = []
    claude_root = _claude_projects_root()
    if not claude_root.is_dir():
        return out

    # 1) Current path.
    cur = claude_root / encode_claude_path(repo)
    if cur.is_dir():
        out.extend(sorted(cur.glob("*.jsonl")))

    # 2) Other encoded dirs pointing at the same root-commit SHA.
    target = root_commit_sha(repo)
    if not target:
        return out

    cur_name = cur.name if cur else None
    for cand in sorted(claude_root.iterdir()):
        if not cand.is_dir() or cand.name == cur_name:
            continue
        decoded = _decode_candidate(cand.name)
        if decoded is None or not decoded.is_dir():
            continue
        try:
            other_sha = root_commit_sha(decoded)
        except Exception:
            other_sha = None
        if other_sha == target:
            out.extend(sorted(cand.glob("*.jsonl")))

    return out


def _decode_candidate(encoded: str) -> Path | None:
    """Best-effort reverse of ``encode_claude_path``: treat every ``-`` as
    a path separator. Ambiguous (``ot-h-foo`` is indistinguishable from a
    slash-separated path), so we just build ``/ot/h/foo``-style and rely
    on the caller checking ``.is_dir()``. Returns None for empty strings.
    """
    if not encoded or encoded == "-":
        return None
    # Leading '-' corresponds to leading '/'.
    parts = encoded.split("-")
    # Filter empty leading element (from leading '-') but keep the root '/'.
    if parts and parts[0] == "":
        parts = parts[1:]
    if not parts:
        return None
    return Path("/" + "/".join(parts))
