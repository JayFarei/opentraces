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

# Match-strength thresholds for :func:`_edit_touches_hunks`.
#
# The original rule — "any single novel line is a substring of any
# hunk's added region" — produced many false positives on long
# sessions: common idioms like ``return None``, ``import json``, or
# ``for item in items:`` match any diff that happens to contain the
# same line, regardless of whether the agent actually contributed
# that line to that commit. A user-flagged example: a trace that
# edited ``src/opentraces/clients/tui/app.py`` appeared in the blame
# view of 20+ commits that predated the trace — every commit whose
# diff happened to contain a short common line from the agent's
# edits got credited.
#
# The tighter rule:
#
#   * ``STRONG_SINGLE_LEN`` — a single matching novel line this long
#     (normalized) is enough on its own. A 30-char code line
#     (roughly one substantive statement) is specific enough that
#     coincidental matches across unrelated commits are rare.
#   * ``MULTI_COUNT`` + ``MULTI_MIN_LEN`` — clustered matches in the
#     *same hunk* also count. Two distinct needles (each >= 8 chars
#     to filter out ``pass``, ``}``, etc.) matching inside one hunk
#     is strong evidence the agent's output landed there. Scattered
#     single-line matches across different hunks do **not** count —
#     that pattern is exactly the phantom-match signature.
#
# Calibration note: thresholds were picked against the
# community-traces-hf April 2026 inbox. Combined with the causality
# window, they cut total links 8716 → 2138 without losing the
# already-attributed (line-level) rows.
STRONG_SINGLE_LEN = 30
MULTI_COUNT = 2
MULTI_MIN_LEN = 8


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
    """True iff the Edit's novel lines land in a commit hunk with
    enough specificity to rule out coincidence.

    Evidence rules (OR-combined, evaluated per hunk):

    * One matching novel line whose normalized length >= STRONG_SINGLE_LEN.
    * >= MULTI_COUNT distinct novel lines (each >= MULTI_MIN_LEN chars)
      matching inside the **same** hunk.

    Matches spread across multiple hunks do not combine — a phantom
    "1 line matches hunk A, another matches hunk B" pattern is the
    exact false-positive signature we're trying to suppress.
    """
    novels = _novel_lines(old_string, new_string)
    norm_novels = [_norm(n) for n in novels if _norm(n)]
    if not norm_novels:
        return False
    for hunk in hunks:
        haystack = _norm(hunk.get("added_text") or "")
        if not haystack:
            continue
        longest_match = 0
        multi_match = 0
        for needle in norm_novels:
            if needle in haystack:
                longest_match = max(longest_match, len(needle))
                if len(needle) >= MULTI_MIN_LEN:
                    multi_match += 1
        if longest_match >= STRONG_SINGLE_LEN:
            return True
        if multi_match >= MULTI_COUNT:
            return True
    return False


def patch_file_in_commit(
    file_path: str, hunks: dict[str, list[dict]] | None
) -> bool:
    """True iff ``file_path`` is one of the files the commit actually changed.

    Epic #169 (B-attr) over-attribution cure: ``correlate`` matches a trace to a
    commit at the TRACE level, and the post-commit writer must not fan that one
    match across patches whose own file the commit never touched. The commit's
    real changed-file set is exactly ``hunks.keys()`` (the parsed
    ``git diff-tree`` / ``git show`` output), so a patch is eligible to anchor to
    this commit ONLY when its ``file_path`` is a member here — the same
    ``_path_matches`` rule ``correlate`` already uses to relate edits to hunks
    (abs/rel + foreign-prefix tolerant). No file, or no hunks, is never a match.
    """
    if not file_path or not hunks:
        return False
    for hunk_path in hunks:
        if _path_matches(file_path, hunk_path):
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
