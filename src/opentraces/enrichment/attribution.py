"""Attribution block construction from Edit/Write tool calls."""

from __future__ import annotations

from collections import defaultdict

import mmh3

from opentraces_schema.models import (
    Attribution,
    AttributionConversation,
    AttributionFile,
    AttributionRange,
    Step,
)

from .snippets import extract_edited_lines


def _content_hash(text: str) -> str:
    """Compute a content hash as `murmur3:<hex>` for cross-tool comparability.

    128-bit MurmurHash3 rendered as 32 hex chars, prefixed with `murmur3:` so
    consumers can validate the algorithm without guessing. Matches the Agent
    Trace v0.1.0 content-hash convention.
    """
    h = mmh3.hash128(text.encode("utf-8"), signed=False)
    return f"murmur3:{h:032x}"


def _parse_diff_files(patch: str) -> dict[str, list[tuple[int, int]]]:
    """Parse a unified diff and extract (start_line, end_line) hunks per file.

    Thin view used by legacy callers. For richer hunk data (added-line
    content, + ranges) see `_parse_diff_hunks_with_content`.
    """
    rich = _parse_diff_hunks_with_content(patch)
    return {path: [(h["start_line"], h["end_line"]) for h in hunks] for path, hunks in rich.items()}


def _parse_diff_hunks_with_content(patch: str) -> dict[str, list[dict]]:
    """Parse a unified diff into per-file hunks with added-line content.

    Each hunk entry has:
      - start_line, end_line: 1-indexed range of the new-file hunk span
        (from `@@ -X,Y +start,count @@`).
      - added_start, added_end: 1-indexed range covering only the `+`
        lines within the hunk (excludes unchanged context).
      - added_text: the concatenated `+` line content (leading `+` stripped,
        trailing newline included).

    Used by build_attribution to match Edit tool calls against actual
    committed hunks and pin diff-sourced ranges at high confidence.
    """
    files: dict[str, list[dict]] = {}
    current_file: str | None = None
    current_hunk: dict | None = None
    new_line_cursor = 0  # 1-indexed position in the new file, within the current hunk.
    added_lines: list[tuple[int, str]] = []  # (line_number, content)

    def _flush_hunk() -> None:
        nonlocal current_hunk, added_lines
        if current_hunk is None or current_file is None:
            return
        if added_lines:
            current_hunk["added_start"] = added_lines[0][0]
            current_hunk["added_end"] = added_lines[-1][0]
            current_hunk["added_text"] = "\n".join(content for _, content in added_lines)
        else:
            # Pure deletion hunk: no `+` lines. Skip — attribution cares
            # about additions and modifications.
            files[current_file].pop()
        current_hunk = None
        added_lines = []

    for line in patch.split("\n"):
        if line.startswith("+++ b/"):
            _flush_hunk()
            current_file = line[6:]
            files.setdefault(current_file, [])
        elif line.startswith("@@ "):
            _flush_hunk()
            if current_file is None:
                continue
            # Parse new-file range: `@@ -X,Y +A,B @@` or `@@ -X +A @@`.
            plus_token = None
            for part in line.split(" "):
                if part.startswith("+"):
                    plus_token = part[1:]
                    break
            if not plus_token:
                continue
            try:
                if "," in plus_token:
                    start_str, count_str = plus_token.split(",", 1)
                    start = int(start_str)
                    count = int(count_str)
                else:
                    start = int(plus_token)
                    count = 1
            except ValueError:
                continue
            if count <= 0:
                continue
            current_hunk = {
                "start_line": start,
                "end_line": start + count - 1,
                "added_start": None,
                "added_end": None,
                "added_text": "",
            }
            files[current_file].append(current_hunk)
            new_line_cursor = start
        elif current_hunk is not None:
            # Inside a hunk body. `+` = added (belongs in new file),
            # ` ` = context (belongs in new file), `-` = deletion (skip).
            if line.startswith("+") and not line.startswith("+++"):
                added_lines.append((new_line_cursor, line[1:]))
                new_line_cursor += 1
            elif line.startswith(" "):
                new_line_cursor += 1
            elif line.startswith("-") and not line.startswith("---"):
                # Deletion: no new-file line produced.
                pass
            # `\ No newline at end of file` and others: ignore.

    _flush_hunk()
    return files


def _norm(text: str) -> str:
    """Normalize whitespace for content matching: strip trailing newlines
    and collapse runs of whitespace so indent differences don't prevent
    a diff hunk from claiming an Edit's new_string."""
    return " ".join(text.split())


def _match_edit_to_hunk(
    new_string: str, hunks: list[dict]
) -> dict | None:
    """Find the hunk whose added-line content contains the Edit's new_string.

    Returns the hunk dict (with added_start/added_end) or None.
    Matching is whitespace-normalized; a hunk's `added_text` must contain
    the Edit's whitespace-normalized `new_string` as a substring.
    """
    if not new_string.strip():
        return None
    needle = _norm(new_string)
    if not needle:
        return None
    for hunk in hunks:
        haystack = _norm(hunk.get("added_text") or "")
        if not haystack:
            continue
        if needle in haystack:
            return hunk
    return None


def build_attribution(
    steps: list[Step],
    outcome_patch: str | None = None,
    *,
    trace_id: str | None = None,
    end_state_changed_files: list[str] | None = None,
) -> Attribution | None:
    """Derive attribution from Edit and Write tool calls in the steps.

    Logic:
    1. Each Edit maps to a line range. Priority order for resolution:
       - cumulative in-memory `str.find()` against prior Reads/Writes/Edits
       - fallback to (1, new_string line count) when no prior content known
    2. Write calls attribute the entire file to that step.
    3. Confidence: "high" for single-edit files with known content,
       "medium" for multi-edit no overlap, "low" for overlapping edits or
       fallback-resolved ranges.
    4. `experimental` is True iff any range is low-confidence or any edit
       used the fallback resolution (R4).
    5. `trace_id` threads into the attribution URL as
       `opentraces://<trace_id>/step_<N>` (R19); defaults to "trace" when
       omitted so imported data from older pipelines still validates.

    Returns None if no Edit/Write tool calls are found.
    """
    trace_slug = trace_id or "trace"

    # step_index -> model string (from Step.model). Used to stamp
    # contributor.model_id per conversation (R2).
    step_models: dict[int, str | None] = {s.step_index: s.model for s in steps}

    # Diff hunks indexed by file path. The patch's +++ headers use
    # repo-relative paths; Edit tool calls often pass absolute paths,
    # so we index by both the full path and its basename for matching.
    patch_hunks: dict[str, list[dict]] = {}
    if outcome_patch:
        patch_hunks = _parse_diff_hunks_with_content(outcome_patch)

    def _hunks_for(file_path: str) -> list[dict]:
        if not patch_hunks:
            return []
        if file_path in patch_hunks:
            return patch_hunks[file_path]
        # Try suffix match: Edit may pass /abs/path/src/app.py while
        # the patch header is src/app.py.
        for path, hunks in patch_hunks.items():
            if file_path.endswith("/" + path) or path.endswith("/" + file_path):
                return hunks
        return []

    file_edits: dict[str, list[dict]] = defaultdict(list)
    file_contents: dict[str, str] = {}
    fallback_used = False
    found_any = False

    for step in steps:
        for tc in step.tool_calls:
            tool_name = tc.tool_name.lower()

            if tool_name == "edit":
                found_any = True
                file_path = tc.input.get("file_path", "")
                old_string = tc.input.get("old_string", "")
                new_string = tc.input.get("new_string", "")

                if not file_path or not new_string:
                    continue

                current_content = file_contents.get(file_path)
                start_line, end_line = extract_edited_lines(
                    old_string, new_string, current_content
                )

                if current_content and old_string in current_content:
                    file_contents[file_path] = current_content.replace(
                        old_string, new_string, 1
                    )

                used_fallback = False
                diff_matched = False

                # R5: diff hunk is the primary line-range source for
                # committed changes. If the Edit's new_string appears
                # in a hunk's + region for this file, use the hunk's
                # actual lines and stamp diff-sourced (high confidence).
                hunk = _match_edit_to_hunk(new_string, _hunks_for(file_path))
                if hunk is not None and hunk.get("added_start") is not None:
                    start_line = hunk["added_start"]
                    end_line = hunk["added_end"]
                    diff_matched = True

                if start_line is None:
                    start_line = 1
                    end_line = max(1, new_string.count("\n") + 1)
                    used_fallback = True
                    fallback_used = True

                file_edits[file_path].append({
                    "step_index": step.step_index,
                    "start_line": start_line,
                    "end_line": end_line,
                    "content_hash": _content_hash(new_string),
                    "used_fallback": used_fallback,
                    "diff_matched": diff_matched,
                })

            elif tool_name == "write":
                found_any = True
                file_path = tc.input.get("file_path", "")
                content = tc.input.get("content", "")

                if not file_path:
                    continue

                file_contents[file_path] = content
                line_count = max(
                    1,
                    content.count("\n") + (1 if content and not content.endswith("\n") else 0),
                )
                file_edits[file_path].append({
                    "step_index": step.step_index,
                    "start_line": 1,
                    "end_line": line_count,
                    "content_hash": _content_hash(content),
                    "used_fallback": False,
                })

            elif tool_name == "read":
                file_path = tc.input.get("file_path", "")
                for obs in step.observations:
                    if obs.source_call_id == tc.tool_call_id and obs.content:
                        file_contents[file_path] = obs.content

    if not found_any:
        return None

    attribution_files: list[AttributionFile] = []
    any_low_confidence = False

    for file_path, edits in sorted(file_edits.items()):
        # Overlap + multi-edit detection drives confidence.
        has_overlap = False
        if len(edits) > 1:
            sorted_edits = sorted(edits, key=lambda e: e["start_line"])
            for i in range(1, len(sorted_edits)):
                if sorted_edits[i]["start_line"] <= sorted_edits[i - 1]["end_line"]:
                    has_overlap = True
                    break

        if len(edits) == 1:
            confidence = "high"
        elif has_overlap:
            confidence = "low"
        else:
            confidence = "medium"

        # Confidence precedence:
        #   diff_matched → high (diff is authoritative post-commit)
        #   used_fallback → low (we don't actually know where the edit landed)
        #   else → the overlap/multi-edit bucket computed above
        ranges: list[AttributionRange] = []
        for edit in edits:
            if edit.get("diff_matched"):
                eff_conf = "high"
            elif edit["used_fallback"]:
                eff_conf = "low"
            else:
                eff_conf = confidence
            if eff_conf == "low":
                any_low_confidence = True
            ranges.append(AttributionRange(
                start_line=edit["start_line"],
                end_line=edit["end_line"],
                content_hash=edit["content_hash"],
                confidence=eff_conf,
            ))

        step_indices = sorted(set(e["step_index"] for e in edits))
        conversations: list[AttributionConversation] = []

        for si in step_indices:
            step_ranges = [
                r for r, e in zip(ranges, edits) if e["step_index"] == si
            ]
            contributor: dict[str, str] = {"type": "ai"}
            model_id = step_models.get(si)
            if model_id:
                contributor["model_id"] = model_id
            conversations.append(AttributionConversation(
                contributor=contributor,
                url=f"opentraces://{trace_slug}/step_{si}",
                ranges=step_ranges,
            ))

        attribution_files.append(AttributionFile(
            path=file_path,
            conversations=conversations,
        ))

    # R4: experimental iff any low-confidence range or any fallback.
    experimental = any_low_confidence or fallback_used

    # R8 / R15: surface files changed on disk or in the commit that no
    # Edit/Write tool call attributed — typically Bash-applied edits
    # (sed -i, codemods). Inputs:
    #   - outcome_patch: per-file paths from the commit diff.
    #   - end_state_changed_files: paths from a Stop hook `git diff HEAD`.
    # Matching against attributed paths is suffix-aware because Edit
    # tool calls frequently pass absolute paths while patch/hook
    # output is repo-relative.
    attributed_paths = [f.path for f in attribution_files]

    def _is_attributed(path: str) -> bool:
        for ap in attributed_paths:
            if ap == path:
                return True
            if ap.endswith("/" + path) or path.endswith("/" + ap):
                return True
        return False

    candidate_paths: list[str] = []
    if outcome_patch:
        for pf in _parse_diff_hunks_with_content(outcome_patch).keys():
            if pf not in candidate_paths:
                candidate_paths.append(pf)
    if end_state_changed_files:
        for pf in end_state_changed_files:
            if pf and pf not in candidate_paths:
                candidate_paths.append(pf)

    unaccounted = [p for p in candidate_paths if not _is_attributed(p)]
    unaccounted_files: list[str] | None
    if outcome_patch is None and not end_state_changed_files:
        # No signal at all → leave the field unset so consumers don't
        # misread "empty list" as "we checked and nothing was
        # unaccounted."
        unaccounted_files = None
    else:
        unaccounted_files = unaccounted or None

    return Attribution(
        experimental=experimental,
        files=attribution_files,
        unaccounted_files=unaccounted_files,
    )
