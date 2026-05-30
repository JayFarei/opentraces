"""Share layer: mint URL, write local dir, publish to HF, clipboard, gh issue.

Codex/eng critical: do NOT reuse ``bucket_remote.remote_push`` (whole-bucket
PRIVATE sync). ``publish_capsule`` uploads ONLY ``capsule.json`` + ``capsule.md``
to a public HF dataset repo under ``capsules/v1/<id>/`` and returns a
commit-SHA-pinned URL so the artifact a maintainer resolves next month is
byte-identical to what was shared.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .contract import validate_capsule
from .render import render_capsule_markdown, render_issue_body

HF_HOST = "https://huggingface.co"
CAPSULE_PREFIX = "capsules/v1"


# --------------------------------------------------------------------------- #
# URL minting + local artifact
# --------------------------------------------------------------------------- #


def _hf_repo_id(url_or_id: str) -> str:
    """Accept hf://owner/repo, https://huggingface.co/datasets/owner/repo, owner/repo."""

    s = url_or_id.strip()
    s = re.sub(r"^hf://", "", s)
    m = re.search(r"huggingface\.co/datasets/([^/]+/[^/?#]+)", s)
    if m:
        return m.group(1)
    return s.strip("/")


def mint_capsule_url(repo_id: str, capsule_id: str, *, revision: str = "main") -> str:
    """The agent-consumable URL: a single self-contained capsule.json blob.

    ``/resolve/<revision>/`` serves the raw bytes (content-type application/json).
    Pin ``revision`` to the publish commit sha for immutability.
    """

    rid = _hf_repo_id(repo_id)
    return f"{HF_HOST}/datasets/{rid}/resolve/{revision}/{CAPSULE_PREFIX}/{capsule_id}/capsule.json"


def human_capsule_url(repo_id: str, capsule_id: str, *, revision: str = "main") -> str:
    """The human-clickable mirror: HF renders committed markdown as a page."""

    rid = _hf_repo_id(repo_id)
    return f"{HF_HOST}/datasets/{rid}/blob/{revision}/{CAPSULE_PREFIX}/{capsule_id}/capsule.md"


def write_capsule_dir(capsule: dict[str, Any], dest_root: Path) -> dict[str, Path]:
    """Write capsule.json + capsule.md under ``dest_root/capsules/v1/<id>/``."""

    cid = capsule["capsule_id"]
    out_dir = Path(dest_root) / CAPSULE_PREFIX / cid
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "capsule.json"
    md_path = out_dir / "capsule.md"
    # Canonical, stable JSON (sorted keys) so the same capsule is byte-identical
    # across machines.
    json_path.write_text(
        json.dumps(capsule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_capsule_markdown(capsule), encoding="utf-8")
    return {"dir": out_dir, "json": json_path, "md": md_path}


# --------------------------------------------------------------------------- #
# Capsule loading / resolving (the consume side)
# --------------------------------------------------------------------------- #


class CapsuleResolveError(RuntimeError):
    pass


def load_capsule_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CapsuleResolveError(f"capsule file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CapsuleResolveError(f"capsule file is not valid JSON: {path}") from exc
    return validate_capsule(data)


def resolve_capsule(ref: str) -> dict[str, Any]:
    """Resolve a capsule from a local path, an https URL, or an hf:// ref.

    Zero bespoke parsing for the consumer: one call returns the validated
    frozen envelope.
    """

    # Local file
    p = Path(ref)
    if p.exists():
        return load_capsule_file(p)

    if ref.startswith(("http://", "https://", "hf://")):
        return _resolve_remote_capsule(ref)

    raise CapsuleResolveError(
        f"could not resolve capsule ref {ref!r} (not a local file, http(s) URL, "
        "or hf:// ref)."
    )


def _resolve_remote_capsule(ref: str) -> dict[str, Any]:
    # hf://owner/repo/capsules/v1/<id>  OR  a full resolve/ URL
    url = ref
    if ref.startswith("hf://"):
        rid_and_path = ref[len("hf://") :]
        # owner/repo/capsules/v1/<id>[/capsule.json]
        parts = rid_and_path.split("/")
        if len(parts) < 2:
            raise CapsuleResolveError(f"malformed hf:// capsule ref: {ref!r}")
        repo_id = "/".join(parts[:2])
        rest = "/".join(parts[2:]) or ""
        if not rest.endswith("capsule.json"):
            rest = f"{rest.rstrip('/')}/capsule.json" if rest else ""
        url = f"{HF_HOST}/datasets/{repo_id}/resolve/main/{rest}"
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - trusted HF host
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        raise CapsuleResolveError(f"failed to fetch capsule from {url}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CapsuleResolveError(f"remote capsule is not valid JSON: {url}") from exc
    return validate_capsule(data)


# --------------------------------------------------------------------------- #
# HF publish (capsule-only, NOT bucket_remote)
# --------------------------------------------------------------------------- #


def publish_capsule(
    capsule: dict[str, Any],
    *,
    repo_id: str,
    token: str | None,
    private: bool = False,
) -> dict[str, Any]:
    """Upload ONLY capsule.json + capsule.md to ``capsules/v1/<id>/`` on HF.

    Returns ``{repo_id, revision, capsule_url, human_url}`` with the URL pinned
    to the publish commit sha when the hub returns one.
    """

    try:
        from huggingface_hub import HfApi
    except Exception as exc:  # pragma: no cover - dep present in env
        raise RuntimeError("huggingface_hub is required to publish a capsule") from exc

    rid = _hf_repo_id(repo_id)
    cid = capsule["capsule_id"]
    api = HfApi(token=token)
    api.create_repo(repo_id=rid, repo_type="dataset", private=private, exist_ok=True)

    # Self-reference: stamp the capsule with its own (latest-revision) URLs before
    # upload so a capsule.json resolved standalone still knows where it lives. The
    # sha-pinned URL we RETURN below is the immutable handle for the issue body.
    capsule = dict(capsule)
    capsule["share"] = {
        "capsule_url": mint_capsule_url(rid, cid, revision="main"),
        "human_url": human_capsule_url(rid, cid, revision="main"),
        "published_revision": None,
    }

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        artifacts = write_capsule_dir(capsule, Path(tmp))
        base = f"{CAPSULE_PREFIX}/{cid}"
        commit = api.upload_folder(
            repo_id=rid,
            repo_type="dataset",
            folder_path=str(artifacts["dir"]),
            path_in_repo=base,
            commit_message=f"capsule {cid}",
        )

    revision = getattr(commit, "oid", None) or "main"
    return {
        "repo_id": rid,
        "revision": revision,
        "capsule_url": mint_capsule_url(rid, cid, revision=revision),
        "human_url": human_capsule_url(rid, cid, revision=revision),
    }


# --------------------------------------------------------------------------- #
# Clipboard
# --------------------------------------------------------------------------- #


def copy_to_clipboard(text: str) -> tuple[bool, str]:
    """Best-effort clipboard copy. Returns (ok, tool_or_reason). Never raises."""

    candidates = (
        ["pbcopy"],            # macOS
        ["wl-copy"],           # Wayland
        ["xclip", "-selection", "clipboard"],  # X11
        ["xsel", "--clipboard", "--input"],
        ["clip"],              # Windows
    )
    for cmd in candidates:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, check=True)
                return True, cmd[0]
            except Exception as exc:  # pragma: no cover - platform dependent
                return False, f"{cmd[0]} failed: {exc}"
    return False, "no clipboard tool found (pbcopy/wl-copy/xclip/xsel/clip)"


# --------------------------------------------------------------------------- #
# GitHub issue (idempotent on the capsule marker, NOT a branch)
# --------------------------------------------------------------------------- #


class GhError(RuntimeError):
    pass


class GhUnavailableError(RuntimeError):
    pass


def gh_available() -> bool:
    return shutil.which("gh") is not None


def _require_gh() -> str:
    gh = shutil.which("gh")
    if not gh:
        raise GhUnavailableError(
            "GitHub CLI (`gh`) not found. Install via `brew install gh` or see "
            "https://cli.github.com/."
        )
    return gh


def _issue_marker(capsule_id: str) -> str:
    return f"<!-- opentraces-capsule: {capsule_id} -->"


def find_capsule_issue(repo: str, capsule_id: str) -> dict[str, Any] | None:
    """Find an existing issue carrying this capsule's marker (idempotency key)."""

    gh = _require_gh()
    out = subprocess.run(
        [
            gh, "issue", "list", "--repo", repo, "--state", "all",
            "--search", f"opentraces-capsule: {capsule_id}",
            "--json", "number,url,body", "--limit", "20",
        ],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise GhError(out.stderr or out.stdout or "gh issue list failed")
    try:
        rows = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        rows = []
    marker = _issue_marker(capsule_id)
    for row in rows:
        if marker in (row.get("body") or ""):
            return {"number": row.get("number"), "url": row.get("url")}
    return None


def create_or_update_issue(
    *, repo: str, capsule_id: str, title: str, body: str,
) -> dict[str, Any]:
    """Idempotent: update the existing capsule issue if present, else create."""

    gh = _require_gh()
    existing = find_capsule_issue(repo, capsule_id)
    if existing is not None:
        out = subprocess.run(
            [gh, "issue", "edit", str(existing["number"]), "--repo", repo, "--body-file", "-"],
            input=body, capture_output=True, text=True, check=False,
        )
        if out.returncode != 0:
            raise GhError(out.stderr or out.stdout or "gh issue edit failed")
        return {"number": existing["number"], "url": existing["url"], "action": "updated"}

    out = subprocess.run(
        [gh, "issue", "create", "--repo", repo, "--title", title, "--body-file", "-"],
        input=body, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise GhError(out.stderr or out.stdout or "gh issue create failed")
    url = (out.stdout or "").strip().splitlines()[-1] if out.stdout else ""
    return {"number": None, "url": url, "action": "created"}


def parse_issue_ref(ref: str) -> tuple[str | None, int | None]:
    """Parse an issue reference into ``(repo, number)``.

    Accepts: ``https://github.com/owner/repo/issues/8``, ``owner/repo#8``, or a
    bare ``8`` (repo must be supplied separately).
    """

    s = ref.strip()
    m = re.search(r"github\.com/([^/]+/[^/]+)/issues/(\d+)", s)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"([^/\s]+/[^/\s#]+)#(\d+)$", s)
    if m:
        return m.group(1), int(m.group(2))
    if s.isdigit():
        return None, int(s)
    return None, None


def issue_state(repo: str, number: int) -> dict[str, Any]:
    """Return ``{state, title, url, verdict}`` for an issue (verdict from comments)."""

    gh = _require_gh()
    out = subprocess.run(
        [gh, "issue", "view", str(number), "--repo", repo,
         "--json", "state,title,url,body,comments"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise GhError(out.stderr or out.stdout or "gh issue view failed")
    data = json.loads(out.stdout or "{}")
    verdict = None
    for comment in data.get("comments") or []:
        m = re.search(r"opentraces-capsule-verdict:\s*\S+\s+state=(\w+)", comment.get("body") or "")
        if m:
            verdict = m.group(1)  # last verdict wins
    cid = None
    mid = re.search(r"opentraces-capsule:\s*(\S+)\s*-->", data.get("body") or "")
    if mid:
        cid = mid.group(1)
    return {
        "state": data.get("state"),
        "title": data.get("title"),
        "url": data.get("url"),
        "verdict": verdict,
        "capsule_id": cid,
    }


def comment_issue(repo: str, number: int, body: str) -> None:
    gh = _require_gh()
    out = subprocess.run(
        [gh, "issue", "comment", str(number), "--repo", repo, "--body-file", "-"],
        input=body, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise GhError(out.stderr or out.stdout or "gh issue comment failed")


def close_issue(repo: str, number: int, *, reason: str = "completed") -> None:
    gh = _require_gh()
    out = subprocess.run(
        [gh, "issue", "close", str(number), "--repo", repo, "--reason", reason],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise GhError(out.stderr or out.stdout or "gh issue close failed")


__all__ = [
    "CapsuleResolveError",
    "GhError",
    "GhUnavailableError",
    "close_issue",
    "comment_issue",
    "copy_to_clipboard",
    "create_or_update_issue",
    "find_capsule_issue",
    "gh_available",
    "issue_state",
    "parse_issue_ref",
    "human_capsule_url",
    "load_capsule_file",
    "mint_capsule_url",
    "publish_capsule",
    "render_capsule_markdown",
    "render_issue_body",
    "resolve_capsule",
    "write_capsule_dir",
]
