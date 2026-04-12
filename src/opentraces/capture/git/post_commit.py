"""Post-commit hook orchestration.

Called after a git commit lands. Finds candidate traces, correlates
each to the new commit, writes tool_emitted / divergence links to
`refs/notes/opentraces`, and exits 0 unconditionally so no hook
failure ever blocks `git commit`.

Plan 041 phase 3 ships the library layer + a minimal bash entrypoint.
Trace-candidate resolution defaults to "inbox traces whose timestamp_end
falls within `window_hours` of now."
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opentraces_schema import GitLink, TraceRecord

from ...enrichment._shared import run_git
from ...enrichment.git import jj_support, notes_store
from ...enrichment.git.correlator import correlate


def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        code, out, _ = run_git(args, cwd)
        return code, out
    except Exception:
        return (-1, "")


def head_sha(cwd: Path) -> str | None:
    code, out = _git(["rev-parse", "HEAD"], cwd)
    return out.strip() if code == 0 else None


def is_merge_commit(cwd: Path, sha: str) -> bool:
    """True iff `sha` has more than one parent."""
    code, out = _git(["rev-list", "--parents", "-n", "1", sha], cwd)
    if code != 0:
        return False
    parts = out.strip().split()
    return len(parts) > 2


def commit_diff(cwd: Path, sha: str) -> str | None:
    code, out = _git(["show", "--format=", "--no-color", "-U3", sha], cwd)
    return out if code == 0 else None


def remote_url(cwd: Path) -> str | None:
    code, out = _git(["remote", "get-url", "origin"], cwd)
    return out.strip() or None if code == 0 else None


def current_branch(cwd: Path) -> str | None:
    code, out = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    b = out.strip()
    if code != 0 or not b or b == "HEAD":
        return None
    return b


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        # Allow trailing Z.
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_recent(
    traces: Iterable[TraceRecord], window: timedelta, now: datetime | None = None,
) -> list[TraceRecord]:
    """Keep traces whose timestamp_end falls within `window` of `now`."""
    now = now or datetime.now(timezone.utc)
    out = []
    for t in traces:
        ts = _parse_iso(t.timestamp_end)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if now - ts <= window:
            out.append(t)
    return out


def run(
    cwd: Path,
    traces: Iterable[TraceRecord],
    *,
    window_hours: float = 1.0,
    background_share: bool = False,
) -> list[tuple[str, list[GitLink]]]:
    """Run the post-commit correlation + notes append.

    Returns the (trace_id, links) pairs that received a non-orphan tier.
    Never raises — any error path short-circuits to an empty result.
    """
    sha = head_sha(cwd)
    if sha is None:
        return []
    if is_merge_commit(cwd, sha):
        return []
    diff = commit_diff(cwd, sha)
    if diff is None:
        return []
    # Parse the unified diff once and reuse across candidates; inside
    # correlate() this would run N times for an N-candidate window.
    from ...enrichment.attribution import _parse_diff_hunks_with_content
    hunks = _parse_diff_hunks_with_content(diff)
    repo_url = remote_url(cwd)
    branch = current_branch(cwd)

    # Prefer jj change_id when available: it's rebase+amend invariant,
    # so a GitLink pinned to it survives history rewrites that would
    # orphan a sha-based pin.
    vcs_type = "git"
    revision = sha
    if jj_support.is_jj_repo(cwd):
        change_id = jj_support.current_change_id(cwd)
        if change_id:
            vcs_type = "jj"
            revision = change_id

    candidates = filter_recent(traces, timedelta(hours=window_hours))
    results: list[tuple[str, list[GitLink]]] = []
    lines_to_append: list[str] = []
    for trace in candidates:
        links = correlate(
            trace, revision, diff, repo_url=repo_url, branch=branch,
            vcs_type=vcs_type, hunks=hunks,
        )
        if not links or links[0].tier == "orphan":
            continue
        # Attach the link for evidence regardless of tier. Deduplicate
        # against existing git_links by (revision, tier).
        existing = {(l.revision, l.tier) for l in trace.git_links}
        for link in links:
            if (link.revision, link.tier) not in existing:
                trace.git_links.append(link)

        # Tool-emitted and divergence pin the revision + promote
        # lifecycle. Overlapping is only evidence of coincidence and
        # stays provisional.
        tier = links[0].tier
        if tier in ("tool_emitted", "tool_emitted_with_divergence"):
            trace.lifecycle = "final"
            if trace.attribution is not None:
                trace.attribution.revision = {
                    "vcs_type": links[0].vcs_type,
                    "revision": revision,
                }
        if tier == "tool_emitted_with_divergence":
            _stamp_divergence(trace)

        results.append((trace.trace_id, links))
        lines_to_append.append(notes_store.format_link(trace.trace_id))

    if lines_to_append:
        try:
            # Notes storage is always keyed by the git sha — `git notes`
            # attaches to an object, and for jj we still have the
            # underlying git sha of the current commit. The link's
            # revision field may be jj's change_id; the notes anchor
            # remains the git sha.
            notes_store.append(sha, lines_to_append, cwd)
        except Exception:
            pass  # notes append is best-effort; never block
    if background_share and results:
        kick_background_share(cwd)
    return results


def kick_background_share(cwd: Path) -> None:
    """Fire-and-forget `opentraces share` in the background.

    Uses `subprocess.Popen` with detached stdio. Any failure is logged
    by opentraces itself; this call must never raise. Skipped when the
    opentraces CLI isn't on PATH (e.g. during tests).
    """
    import shutil
    import subprocess

    bin_path = shutil.which("opentraces")
    if not bin_path:
        return
    try:
        subprocess.Popen(
            [bin_path, "push", "--auto", "--quiet"],
            cwd=str(cwd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _stamp_divergence(trace) -> None:
    """Stamp every attribution range with contributor=mixed and
    record the agent's pre-divergence state into range.original."""
    if trace.attribution is None:
        return
    for f in trace.attribution.files:
        for conv in f.conversations:
            for rng in conv.ranges:
                # Per-range contributor override to mixed. Preserves
                # the conversation-level ai contributor intact.
                rng.contributor = {"type": "mixed"}
                # Record what the agent wrote before the commit
                # diverged from it.
                if rng.original is None:
                    rng.original = {
                        "start_line": rng.start_line,
                        "end_line": rng.end_line,
                        "content_hash": rng.content_hash,
                    }
