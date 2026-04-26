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

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
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
        existing = {(link.revision, link.tier) for link in trace.git_links}
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


def persist_trace_updates(staging: Path, traces: list) -> int:
    """Rewrite each trace's JSONL file from its in-memory TraceRecord.

    `run()` mutates `trace.git_links`, `trace.lifecycle`, and
    `trace.attribution.revision` on successful correlation, but keeps the
    mutation in-memory only. Without this writeback the trace's on-disk
    record never gains its git_links, so inbox cards render empty
    "no commits in blame" chips forever.

    Safe to call even when the staging file is missing — skipped silently.
    Returns the number of files successfully rewritten.
    """
    import json as _json
    written = 0
    for trace in traces:
        path = staging / f"{trace.trace_id}.jsonl"
        if not path.is_file():
            continue
        try:
            payload = trace.model_dump_json()
            _json.loads(payload)  # sanity: round-trip must parse
        except Exception:
            continue
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(payload + "\n", encoding="utf-8")
            os.replace(tmp, path)
            written += 1
        except OSError:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return written


HOOK_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB, then rotate-by-truncate.


def _hook_log_path(repo: Path) -> Path:
    """One log file per repo, kept inside .git so it's repo-local and
    naturally excluded from worktree commits."""
    return repo / ".git" / "opentraces-hook.log"


def _append_hook_log(repo: Path, entry: dict) -> None:
    """Append a structured JSON line to the hook log. Never raises; a
    failed log write must not block a commit."""
    import json as _json
    try:
        path = _hook_log_path(repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Best-effort rotation: if over the cap, truncate to last half.
        try:
            if path.exists() and path.stat().st_size > HOOK_LOG_MAX_BYTES:
                tail = path.read_bytes()[-HOOK_LOG_MAX_BYTES // 2:]
                # Drop partial first line after truncation.
                nl = tail.find(b"\n")
                if nl >= 0:
                    tail = tail[nl + 1:]
                path.write_bytes(tail)
        except OSError:
            pass
        with path.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(entry, default=str) + "\n")
    except Exception:
        # Logging is best-effort — swallow any filesystem failure.
        pass


def _reconcile_trail_anchors(repo: Path, sha: str | None, entry: dict) -> None:
    """Best-effort Trace Trail anchor reconciliation for the current commit."""
    if sha is None or is_merge_commit(repo, sha):
        return
    try:
        from ...core.trails import reconcile_commit_anchors

        anchors = reconcile_commit_anchors(
            repo,
            sha,
            writer="post-commit-correlator",
        )
        entry["trail_anchors_created"] = len(anchors)
    except Exception as e:
        entry["trail_anchor_error"] = f"{type(e).__name__}: {e}"


def run_for_repo(repo: Path, *, window_hours: int = 2) -> None:
    """Entrypoint for the installed post-commit shim.

    Loads recent inbox traces, calls ``run()`` to correlate + write notes,
    and records a structured JSON line to ``.git/opentraces-hook.log`` for
    every invocation so silent failures are never invisible. All failures
    are swallowed — the shell hook must never block a commit.
    """
    entry: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "repo": None,
        "sha": None,
        "candidates": 0,
        "verdicts": [],
        "notes_written": False,
        "trail_anchors_created": 0,
        "trail_anchor_error": None,
        "error": None,
        "reason": None,
    }
    try:
        from ...core.config import get_project_traces_dir
        from ...core.inbox import load_trace_records

        repo = Path(repo).resolve()
        entry["repo"] = str(repo)
        entry["sha"] = head_sha(repo)

        staging = get_project_traces_dir(repo)
        if not staging.exists():
            _reconcile_trail_anchors(repo, entry["sha"], entry)
            entry["reason"] = "no_staging_dir"
            return
        since = (
            datetime.now(timezone.utc) - timedelta(hours=window_hours)
        ).isoformat()
        records = load_trace_records(staging, since_iso=since)
        records = list(records)
        entry["candidates"] = len(records)
        if not records:
            _reconcile_trail_anchors(repo, entry["sha"], entry)
            entry["reason"] = "no_traces_in_inbox_window"
            return
        results = run(repo, records)
        _reconcile_trail_anchors(repo, entry["sha"], entry)
        entry["verdicts"] = [
            {"trace_id": tid, "tier": (links[0].tier if links else "orphan")}
            for tid, links in results
        ]
        entry["notes_written"] = bool(results)
        if results:
            by_tid = {t.trace_id: t for t in records}
            entry["traces_persisted"] = persist_trace_updates(
                staging, [by_tid[tid] for tid, _ in results if tid in by_tid],
            )
        if not results:
            entry["reason"] = "all_candidates_orphan"
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
    finally:
        _append_hook_log(Path(repo), entry)


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


def _commit_timestamp(cwd: Path, sha: str) -> datetime | None:
    """Author/committer date of `sha` as a tz-aware datetime, or None."""
    code, out = _git(["show", "-s", "--format=%cI", sha], cwd)
    if code != 0:
        return None
    return _parse_iso(out.strip())


def _first_parent_shas(cwd: Path, *, max_commits: int) -> list[str]:
    code, out = _git(
        ["rev-list", "--first-parent", f"--max-count={max_commits}", "HEAD"],
        cwd,
    )
    if code != 0:
        return []
    return [s for s in out.splitlines() if s.strip()]


@dataclass
class BackfillReport:
    commits_scanned: int = 0
    commits_correlated: int = 0
    commits_skipped: int = 0
    traces_linked: int = 0
    traces_persisted: int = 0
    tiers: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def backfill(
    cwd: Path,
    *,
    max_commits: int = 500,
    window_hours: float = 24.0,
) -> BackfillReport:
    """Walk recent first-parent history and retroactively correlate traces.

    For each commit in the last `max_commits`, gathers inbox traces whose
    `timestamp_end` falls within `window_hours` of the commit's commit
    date, runs the correlator, writes notes on refs/notes/opentraces,
    and persists `git_links` / lifecycle updates back to the trace JSONL
    files. Idempotent on both axes: `notes_store.append` dedupes against
    the existing note, and the in-memory dedup in `run()` avoids adding
    duplicate (revision, tier) pairs before rewrite.

    Window is wider than the live hook's 2h default because commits that
    land long after a session's Stop hook still deserve retro-linking.
    """
    from ...core.config import get_project_traces_dir
    from ...core.inbox import load_trace_records

    cwd = Path(cwd).resolve()
    report = BackfillReport()

    staging = get_project_traces_dir(cwd)
    if not staging.exists():
        report.errors.append("no_staging_dir")
        return report

    # Load the full inbox once — we'll filter per-commit below. Loading
    # all records up front avoids O(C*T) Pydantic validation when walking
    # many commits against a large staging dir.
    records = list(load_trace_records(staging))
    if not records:
        report.errors.append("empty_inbox")
        return report
    trace_times: list[tuple[datetime, object]] = []
    for t in records:
        ts = _parse_iso(t.timestamp_end)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        trace_times.append((ts, t))

    shas = _first_parent_shas(cwd, max_commits=max_commits)
    report.commits_scanned = len(shas)
    if not shas:
        report.errors.append("no_commits")
        return report

    repo_url = remote_url(cwd)
    branch = current_branch(cwd)

    for sha in shas:
        if is_merge_commit(cwd, sha):
            report.commits_skipped += 1
            continue
        ts = _commit_timestamp(cwd, sha)
        if ts is None:
            report.commits_skipped += 1
            continue

        from ...enrichment.attribution import _parse_diff_hunks_with_content
        diff = commit_diff(cwd, sha)
        if diff is None:
            report.commits_skipped += 1
            continue
        hunks = _parse_diff_hunks_with_content(diff)

        # Filter candidates to those whose timestamp_end is within
        # `window_hours` BEFORE the commit. A trace that ended after the
        # commit landed cannot have contributed to it — and the old
        # symmetric `abs(tts - ts) <= window` rule credited future
        # sessions to past commits whenever an edit's novel line
        # coincidentally matched a diff hunk (same common line in
        # Python/TS — ``return False``, ``import json``, etc). A small
        # `look_ahead` tolerance keeps commits that land a few minutes
        # after the Stop hook (normal case for the live path).
        window = timedelta(hours=window_hours)
        look_ahead = timedelta(minutes=5)
        candidates = [
            t for (tts, t) in trace_times
            if (ts - window) <= tts <= (ts + look_ahead)
        ]
        if not candidates:
            continue

        vcs_type = "git"
        revision = sha

        matched: list = []
        lines_to_append: list[str] = []
        for trace in candidates:
            links = correlate(
                trace, revision, diff,
                repo_url=repo_url, branch=branch,
                vcs_type=vcs_type, hunks=hunks,
            )
            if not links or links[0].tier == "orphan":
                continue
            existing = {(link.revision, link.tier) for link in trace.git_links}
            for link in links:
                if (link.revision, link.tier) not in existing:
                    trace.git_links.append(link)
            tier = links[0].tier
            if tier in ("tool_emitted", "tool_emitted_with_divergence"):
                trace.lifecycle = "final"
                if trace.attribution is not None:
                    trace.attribution.revision = {
                        "vcs_type": vcs_type,
                        "revision": revision,
                    }
            if tier == "tool_emitted_with_divergence":
                _stamp_divergence(trace)

            report.tiers[tier] = report.tiers.get(tier, 0) + 1
            report.traces_linked += 1
            matched.append(trace)
            lines_to_append.append(notes_store.format_link(trace.trace_id))

        if lines_to_append:
            try:
                notes_store.append(sha, lines_to_append, cwd)
            except Exception as e:
                report.errors.append(f"{sha[:8]}: notes: {type(e).__name__}: {e}")
            report.commits_correlated += 1
            report.traces_persisted += persist_trace_updates(staging, matched)

    return report
