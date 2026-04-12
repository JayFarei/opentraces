"""Correlate a TraceRecord to a git commit and return GitLinks with tiers.

Evidence tiers:
- tool_emitted: a session's Edit-derived content appears in the commit's
  staged diff hunks (whitespace-normalized substring match against a
  hunk's added region).
- tool_emitted_with_divergence: files overlap but content doesn't — a
  formatter or human rewrote the agent's output before commit.
- overlapping: agent edited the repo but touched no files the commit
  changed; time-window coincidence.
- orphan: no viable link.
"""

from __future__ import annotations

from opentraces_schema import GitLink, TraceRecord

from .._shared import path_matches as _path_matches
from ..attribution import _norm, _parse_diff_hunks_with_content


def _novel_lines(old_string: str, new_string: str) -> list[str]:
    """Return lines in new_string that aren't also in old_string.

    Context lines shared between old and new are preserved in the
    committed file, so they're not what the Edit "contributed." Only
    the novel lines should match against the hunk's added region.
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
    hunks: dict[str, list[dict]] | None = None,
) -> list[GitLink]:
    """Return a list of GitLinks for the given (trace, commit) pair.

    Tier precedence (strongest first):
        tool_emitted > tool_emitted_with_divergence > overlapping > orphan

    `hunks` may be pre-parsed and passed in when the caller correlates
    many traces against the same commit (callers paying the parse cost
    once in `post_commit.run`). When None the diff is parsed here.
    """
    if hunks is None:
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
