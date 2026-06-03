"""Single source of truth for walking the string fields of a TraceRecord.

Tools wrap their per-text detector in a
``transform(text, path, field_type) -> str`` callable; ``walk_string_fields``
takes care of traversal and bookkeeping. ``walk_dict_strings`` does the same
over plain JSON-ish dicts for workflow scripts.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from opentraces_schema import TraceRecord

from .scanner import (
    FieldType,
    _classify_tool,
    is_base64_blob,
    is_safe_field_path,
)  # noqa: F401

FieldPath = str  # dotted JSON-Pointer-ish path, e.g. "steps[3].observations[0].content"

TransformFn = Callable[[str, FieldPath, FieldType], str]
"""Tool-side transform invoked for each visited string field.

Receives the current text, its field path, and the field-type hint.
Returns the replacement text. Returning the same string is a no-op.
"""


# ---------------------------------------------------------------------------
# Span redaction helper
# ---------------------------------------------------------------------------


def redact_spans(text: str, spans: Iterable[tuple[int, int]], placeholder: str = "[REDACTED]") -> str:
    """Replace each ``(start, end)`` span in ``text`` with ``placeholder``.

    Spans are applied right-to-left so earlier offsets remain valid. Overlapping
    spans are merged on the longest-wins principle (the larger span absorbs the
    smaller).
    """
    pairs = sorted({(s, e) for s, e in spans if e > s}, key=lambda p: (p[0], -p[1]))
    if not pairs:
        return text

    merged: list[tuple[int, int]] = []
    for start, end in pairs:
        if merged and start < merged[-1][1]:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    out = text
    for start, end in reversed(merged):
        out = out[:start] + placeholder + out[end:]
    return out


def locate_substrings(text: str, substrings: Iterable[str]) -> list[tuple[str, int, int]]:
    """Return ``(substring, start, end)`` for every occurrence of each substring.

    Used by detectors (TruffleHog, LLM PII) that get back matched text without
    offsets and need to translate it into spans for ``redact_spans``.
    """
    out: list[tuple[str, int, int]] = []
    for sub in substrings:
        if not sub:
            continue
        idx = 0
        while True:
            j = text.find(sub, idx)
            if j < 0:
                break
            out.append((sub, j, j + len(sub)))
            idx = j + len(sub)
    return out


def ensure_security_metadata(record: TraceRecord) -> dict[str, Any]:
    """Return ``record.metadata["security"]``, creating it (or replacing a
    non-dict value) so callers can write keys without guarding."""
    sec = record.metadata.get("security")
    if not isinstance(sec, dict):
        sec = {}
        record.metadata["security"] = sec
    return sec


# ---------------------------------------------------------------------------
# TraceRecord walker
# ---------------------------------------------------------------------------


def _apply(transform: TransformFn, text: str, path: FieldPath, field_type: FieldType) -> tuple[str, bool]:
    """Run ``transform`` and report whether it changed the text.

    Transforms that don't match return the *same* string object, so an
    identity check is cheaper than a value comparison on large fields.
    """
    new = transform(text, path, field_type)
    return new, new is not text


def walk_string_fields(record: TraceRecord, transform: TransformFn) -> int:
    """Visit every string-bearing field on ``record`` and rewrite in place.

    Returns the number of fields whose contents were changed by ``transform``.
    The field-type hint uses the context-aware classification from
    ``scanner`` — tool-call inputs and tool-call results are flagged
    distinctly from reasoning text and general prose.
    """
    changed = 0

    # System prompts (general).
    for prompt_hash, prompt_text in list(record.system_prompts.items()):
        new, did_change = _apply(transform, prompt_text, f"system_prompts[{prompt_hash}]", FieldType.GENERAL)
        if did_change:
            record.system_prompts[prompt_hash] = new
            changed += 1

    # Task description (general).
    if record.task.description:
        new, did_change = _apply(transform, record.task.description, "task.description", FieldType.GENERAL)
        if did_change:
            record.task.description = new
            changed += 1

    for step_idx, step in enumerate(record.steps):
        step_path = f"steps[{step_idx}]"

        if step.content:
            new, did_change = _apply(transform, step.content, f"{step_path}.content", FieldType.GENERAL)
            if did_change:
                step.content = new
                changed += 1

        if step.reasoning_content:
            new, did_change = _apply(
                transform, step.reasoning_content, f"{step_path}.reasoning_content", FieldType.REASONING,
            )
            if did_change:
                step.reasoning_content = new
                changed += 1

        for tc_idx, tool_call in enumerate(step.tool_calls):
            base = f"{step_path}.tool_calls[{tc_idx}]"
            ft = _classify_tool(tool_call.tool_name)
            for key, value in list(tool_call.input.items()):
                changed += _walk_value(
                    value,
                    transform,
                    base_path=f"{base}.input.{key}",
                    field_type=ft,
                    on_replace=lambda new_val, k=key: tool_call.input.__setitem__(k, new_val),
                )

        for obs_idx, observation in enumerate(step.observations):
            obs_path = f"{step_path}.observations[{obs_idx}]"
            if observation.content:
                new, did_change = _apply(transform, observation.content, f"{obs_path}.content", FieldType.TOOL_RESULT)
                if did_change:
                    observation.content = new
                    changed += 1
            if observation.output_summary:
                new, did_change = _apply(
                    transform, observation.output_summary, f"{obs_path}.output_summary", FieldType.TOOL_RESULT,
                )
                if did_change:
                    observation.output_summary = new
                    changed += 1
            if observation.error:
                new, did_change = _apply(transform, observation.error, f"{obs_path}.error", FieldType.TOOL_RESULT)
                if did_change:
                    observation.error = new
                    changed += 1

        for snip_idx, snippet in enumerate(step.snippets):
            if snippet.text:
                new, did_change = _apply(
                    transform, snippet.text, f"{step_path}.snippets[{snip_idx}].text", FieldType.GENERAL,
                )
                if did_change:
                    snippet.text = new
                    changed += 1

    if record.outcome.description:
        new, did_change = _apply(transform, record.outcome.description, "outcome.description", FieldType.GENERAL)
        if did_change:
            record.outcome.description = new
            changed += 1

    # Plan 080: outcome.patch removed; per-patch redaction lives at
    # patch-creation time in the trail event log (trail.jsonl.gz hunks).

    if record.environment.vcs.diff:
        new, did_change = _apply(transform, record.environment.vcs.diff, "environment.vcs.diff", FieldType.GENERAL)
        if did_change:
            record.environment.vcs.diff = new
            changed += 1

    # Harnesses that capture richer live provider context may carry prompt
    # text in metadata before Context Tree projection. Keep this targeted so
    # generic bookkeeping metadata is not rewritten, but Pi provider/context
    # payloads receive the same security treatment as normal TraceRecord text.
    pi_meta = record.metadata.get("pi") if isinstance(record.metadata, dict) else None
    if isinstance(pi_meta, dict):
        for key in ("provider_contexts", "branch_summaries"):
            if key in pi_meta:
                new_value, n = walk_dict_strings(
                    pi_meta[key],
                    transform,
                    path=f"metadata.pi.{key}",
                    field_type=FieldType.GENERAL,
                )
                if n:
                    pi_meta[key] = new_value
                    changed += n

    return changed


def _walk_value(
    value: Any,
    transform: TransformFn,
    *,
    base_path: FieldPath,
    field_type: FieldType,
    on_replace: Callable[[Any], None],
) -> int:
    """Visit string-bearing leaves inside a tool-call input value.

    Tool-call inputs are Pydantic-validated as ``dict[str, Any]``; the leaves
    may be lists or nested dicts, all of which we recurse into. The closure
    ``on_replace`` writes a mutated container back to its parent — the helper
    rebuilds new lists/dicts only when at least one leaf changed.
    """
    if isinstance(value, str):
        new, did_change = _apply(transform, value, base_path, field_type)
        if did_change:
            on_replace(new)
            return 1
        return 0

    if isinstance(value, list):
        changed = 0
        new_list = list(value)
        mutated = False

        def make_item_setter(idx: int):
            def _set(new_item: Any) -> None:
                nonlocal mutated
                new_list[idx] = new_item
                mutated = True
            return _set

        for idx, item in enumerate(value):
            changed += _walk_value(
                item,
                transform,
                base_path=f"{base_path}[{idx}]",
                field_type=field_type,
                on_replace=make_item_setter(idx),
            )
        if mutated:
            on_replace(new_list)
        return changed

    if isinstance(value, dict):
        changed = 0
        new_dict = dict(value)
        mutated = False

        def make_key_setter(k: str):
            def _set(new_val: Any) -> None:
                nonlocal mutated
                new_dict[k] = new_val
                mutated = True
            return _set

        for k, v in value.items():
            changed += _walk_value(
                v,
                transform,
                base_path=f"{base_path}.{k}",
                field_type=field_type,
                on_replace=make_key_setter(k),
            )
        if mutated:
            on_replace(new_dict)
        return changed

    return 0


# ---------------------------------------------------------------------------
# Dict walker (workflow row path)
# ---------------------------------------------------------------------------


def walk_dict_strings(
    data: Any,
    transform: TransformFn,
    *,
    path: FieldPath = "",
    field_type: FieldType = FieldType.GENERAL,
) -> tuple[Any, int]:
    """Visit string leaves in a plain JSON-ish structure.

    Returns ``(new_data, changed_count)``. Lists and dicts are rebuilt only
    when at least one leaf changed; otherwise the original container is
    returned untouched. Used by the workflow-row sanitiser, where the data
    shape is not known statically.
    """
    if isinstance(data, str):
        new, did_change = _apply(transform, data, path or "$", field_type)
        return (new, 1) if did_change else (data, 0)

    if isinstance(data, list):
        total = 0
        new_list: list[Any] = []
        mutated = False
        for idx, item in enumerate(data):
            new_item, n = walk_dict_strings(
                item,
                transform,
                path=f"{path}[{idx}]",
                field_type=field_type,
            )
            new_list.append(new_item)
            total += n
            if n:
                mutated = True
        return (new_list if mutated else data, total)

    if isinstance(data, dict):
        total = 0
        new_dict: dict[Any, Any] = {}
        mutated = False
        for k, v in data.items():
            sub_path = f"{path}.{k}" if path else str(k)
            new_v, n = walk_dict_strings(v, transform, path=sub_path, field_type=field_type)
            new_dict[k] = new_v
            total += n
            if n:
                mutated = True
        return (new_dict if mutated else data, total)

    return data, 0
