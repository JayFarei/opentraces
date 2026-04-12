"""Correlate a TraceRecord to a git commit and return GitLinks with tiers.

Plan 041 R25 evidence tiers:
- tool_emitted: a session's Edit-derived content appears in the commit's
  staged diff hunks (exact-match via hunk-added-region substring
  comparison after whitespace normalization).
- tool_emitted_with_divergence: files match but bytes don't (phase 4).
- overlapping: file + time-window overlap with no hash match (phase 4).
- orphan: no viable commit link.

Phase 3 covers tool_emitted and orphan only. Divergence + overlapping
arrive in phase 4.
"""

from __future__ import annotations

from opentraces.enrichment.attribution import _norm, _parse_diff_hunks_with_content
from opentraces_schema import GitLink, TraceRecord


def _novel_lines(old_string: str, new_string: str) -> list[str]:
    """Return lines in new_string that are not also in old_string.

    Context lines shared between old and new are preserved in the
    committed file, so they're not what the Edit "contributed" to the
    commit. We want to match only the lines the Edit actually added
    or changed against the hunk's added region.
    """
    old_lines = {ln.strip() for ln in old_string.splitlines() if ln.strip()}
    novel = [ln for ln in new_string.splitlines() if ln.strip() and ln.strip() not in old_lines]
    return novel or new_string.splitlines() or [new_string]


def _edit_touches_hunks(
    old_string: str, new_string: str, hunks: list[dict]
) -> bool:
    """True iff any novel line from the Edit appears in any hunk's
    added region (whitespace-normalized substring match)."""
    novels = _novel_lines(old_string, new_string)
    norm_novels = [_norm(n) for n in novels if _norm(n)]
    if not norm_novels:
        return False
    for hunk in hunks:
        haystack = _norm(hunk.get("added_text") or "")
        if not haystack:
            continue
        for needle in norm_novels:
            if needle in haystack:
                return True
    return False


def _path_matches(edit_path: str, hunk_path: str) -> bool:
    """Return True if an Edit's file_path is the same file as a hunk
    header's path. Edit paths are often absolute; hunk paths are
    repo-relative."""
    if edit_path == hunk_path:
        return True
    if edit_path.endswith("/" + hunk_path):
        return True
    if hunk_path.endswith("/" + edit_path):
        return True
    return False


def correlate(
    trace: TraceRecord,
    revision: str,
    commit_diff: str,
    *,
    repo_url: str | None = None,
    branch: str | None = None,
    vcs_type: str = "git",
) -> list[GitLink]:
    """Return a list of GitLinks for the given (trace, commit) pair.

    Tier precedence (plan 041 R25, strongest first):
        tool_emitted > tool_emitted_with_divergence > overlapping > orphan

    - tool_emitted: some Edit/Write's novel content appears in a hunk
      for the same file.
    - tool_emitted_with_divergence: the agent edited a file the commit
      touched, but committed bytes don't match — a formatter or human
      rewrote the output.
    - overlapping: the agent made edits but none touched files the
      commit changed (weak coincidence during the time window).
    - orphan: the agent made no edits, or the commit has no hunks.
    """
    hunks = _parse_diff_hunks_with_content(commit_diff)
    hunk_paths = list(hunks.keys())

    agent_edited_any = False
    matched = False
    file_overlap = False

    for step in trace.steps:
        if matched:
            break
        for tc in step.tool_calls:
            tool = (tc.tool_name or "").lower()
            if tool not in ("edit", "write"):
                continue
            file_path = tc.input.get("file_path") or ""
            if tool == "edit":
                old_text = tc.input.get("old_string") or ""
                new_text = tc.input.get("new_string") or ""
            else:
                old_text = ""
                new_text = tc.input.get("content") or ""
            if not file_path or not new_text.strip():
                continue
            agent_edited_any = True
            for hunk_path, file_hunks in hunks.items():
                if not _path_matches(file_path, hunk_path):
                    continue
                file_overlap = True
                if _edit_touches_hunks(old_text, new_text, file_hunks):
                    matched = True
                    break
            if matched:
                break

    if matched:
        tier = "tool_emitted"
    elif file_overlap:
        tier = "tool_emitted_with_divergence"
    elif agent_edited_any and hunk_paths:
        tier = "overlapping"
    else:
        tier = "orphan"

    return [GitLink(
        vcs_type=vcs_type,
        revision=revision,
        repo_url=repo_url,
        branch=branch,
        tier=tier,
    )]
