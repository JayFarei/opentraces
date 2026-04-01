"""Attribution block construction from Edit/Write tool calls."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from opentraces_schema.models import (
    Attribution,
    AttributionConversation,
    AttributionFile,
    AttributionRange,
    Step,
)

from .snippets import extract_edited_lines


def _content_hash(text: str) -> str:
    """Compute a short content hash (md5 truncated to 8 hex chars, murmur3 stand-in)."""
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()[:8]


def _parse_diff_files(patch: str) -> dict[str, list[tuple[int, int]]]:
    """Parse a unified diff and extract (start_line, end_line) hunks per file.

    Returns a dict mapping file paths to lists of (start, end) line ranges
    representing added/modified lines.
    """
    files: dict[str, list[tuple[int, int]]] = {}
    current_file = None

    for line in patch.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in files:
                files[current_file] = []
        elif line.startswith("@@ "):
            # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
            parts = line.split(" ")
            for part in parts:
                if part.startswith("+") and "," in part:
                    try:
                        start = int(part.split(",")[0][1:])
                        count = int(part.split(",")[1])
                        if current_file and count > 0:
                            files[current_file].append((start, start + count - 1))
                    except (ValueError, IndexError):
                        pass
                    break
                elif part.startswith("+") and part[1:].isdigit():
                    try:
                        start = int(part[1:])
                        if current_file:
                            files[current_file].append((start, start))
                    except ValueError:
                        pass
                    break

    return files


def build_attribution(
    steps: list[Step],
    outcome_patch: str | None = None,
) -> Attribution | None:
    """Derive attribution from Edit and Write tool calls in the steps.

    Logic:
    1. Each Edit tool call maps to line ranges based on its input parameters.
    2. Write tool calls (new files) attribute the entire file to that step.
    3. If outcome_patch is provided, cross-reference against actual diff.
    4. Multi-edit same file: track cumulative line shifts, later edits take precedence.
    5. Confidence: "high" for single-edit files, "medium" for multi-edit no overlap,
       "low" for overlapping edits.

    Returns None if no Edit/Write tool calls are found.
    """
    # Collect all edit and write operations per file
    # file_path -> list of (step_index, start_line, end_line, content_hash, is_overlap)
    file_edits: dict[str, list[dict]] = defaultdict(list)
    file_contents: dict[str, str] = {}  # Track cumulative file state for line shifts

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

                # Try to determine line range
                current_content = file_contents.get(file_path)
                start_line, end_line = extract_edited_lines(
                    old_string, new_string, current_content
                )

                # Update tracked content if we can
                if current_content and old_string in current_content:
                    file_contents[file_path] = current_content.replace(
                        old_string, new_string, 1
                    )

                # If we couldn't determine lines, use placeholder
                if start_line is None:
                    start_line = 1
                    end_line = max(1, new_string.count("\n") + 1)

                file_edits[file_path].append({
                    "step_index": step.step_index,
                    "start_line": start_line,
                    "end_line": end_line,
                    "content_hash": _content_hash(new_string),
                })

            elif tool_name == "write":
                found_any = True
                file_path = tc.input.get("file_path", "")
                content = tc.input.get("content", "")

                if not file_path:
                    continue

                # Track file content for future edit lookups
                file_contents[file_path] = content

                line_count = max(1, content.count("\n") + (1 if content and not content.endswith("\n") else 0))

                file_edits[file_path].append({
                    "step_index": step.step_index,
                    "start_line": 1,
                    "end_line": line_count,
                    "content_hash": _content_hash(content),
                })

            elif tool_name == "read":
                # Track file content for line range calculation in future edits
                file_path = tc.input.get("file_path", "")
                # Look for content in observations
                for obs in step.observations:
                    if obs.source_call_id == tc.tool_call_id and obs.content:
                        file_contents[file_path] = obs.content

    if not found_any:
        return None

    # Build attribution files with confidence scoring
    attribution_files: list[AttributionFile] = []
    unaccounted_hunks: list[str] = []

    # Parse the outcome patch for cross-referencing
    patch_files: dict[str, list[tuple[int, int]]] = {}
    if outcome_patch:
        patch_files = _parse_diff_files(outcome_patch)

    for file_path, edits in sorted(file_edits.items()):
        # Determine confidence based on edit count and overlap
        has_overlap = False
        if len(edits) > 1:
            # Check for overlapping ranges
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

        # Build ranges, with later edits taking precedence for overlaps
        ranges: list[AttributionRange] = []
        for edit in edits:
            ranges.append(AttributionRange(
                start_line=edit["start_line"],
                end_line=edit["end_line"],
                content_hash=edit["content_hash"],
                confidence=confidence,
            ))

        # Build conversation entries (one per unique step)
        step_indices = sorted(set(e["step_index"] for e in edits))
        conversations: list[AttributionConversation] = []

        for si in step_indices:
            step_ranges = [
                r for r, e in zip(ranges, edits) if e["step_index"] == si
            ]
            conversations.append(AttributionConversation(
                contributor={"type": "ai"},
                url=f"opentraces://trace/step_{si}",
                ranges=step_ranges,
            ))

        attribution_files.append(AttributionFile(
            path=file_path,
            conversations=conversations,
        ))

    # Cross-reference with patch if provided
    if patch_files:
        attributed_paths = {f.path for f in attribution_files}
        for pf in patch_files:
            if pf not in attributed_paths:
                unaccounted_hunks.append(pf)

    return Attribution(
        experimental=True,
        files=attribution_files,
    )
