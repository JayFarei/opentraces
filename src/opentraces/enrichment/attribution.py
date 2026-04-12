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
    *,
    trace_id: str | None = None,
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

        # Ranges that used fallback line resolution degrade to low regardless.
        ranges: list[AttributionRange] = []
        for edit in edits:
            eff_conf = "low" if edit["used_fallback"] else confidence
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

    return Attribution(
        experimental=experimental,
        files=attribution_files,
    )
