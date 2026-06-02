"""Field-path exclusion transformer (plan 090).

The redaction floor is detection-bounded — it only scrubs what a detector
matches. EXCLUSION is unbounded: it zeroes whole prompt-bearing subtrees to a
``[EXCLUDED:<path>]`` marker so they NEVER leave the machine, regardless of what a
detector would or would not find. This is the only true "this never leaves"
guarantee for ``context_resume_packet.system_layer`` and per-step
``reasoning_content`` — EXCLUDED by default; the developer opts back IN with
``--include-prompts``.

On the capsule path the pure :func:`apply_field_exclusion` runs over the assembled
envelope dict (see ``core/capsule/redaction.py``). The :class:`CapsuleScopeTransformer`
makes the same capability a first-class, default-disabled registry tool so the
canonical order + ``security tools list`` + ``SECURITY_VERSION`` provenance stay honest.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from opentraces_schema import TraceRecord

from . import ToolContext, ToolInfo, ToolResult, cfg_block
from ..walker import FieldType, walk_string_fields

# Prompt-bearing fields excluded by default. ``*`` matches any ONE path segment
# (e.g. a list index), so ``slice.steps.*.reasoning_content`` covers every step.
DEFAULT_PROMPT_EXCLUDE: tuple[str, ...] = (
    "context_resume_packet.system_layer",
    "slice.steps.*.reasoning_content",
)

EXCLUSION_MARKER_RE = re.compile(r"^\[EXCLUDED:(.+)\]$")


def _segs_match(path_segs: list[str], pat_segs: list[str]) -> bool:
    if len(path_segs) != len(pat_segs):
        return False
    return all(p == "*" or p == s for s, p in zip(path_segs, pat_segs))


def apply_field_exclusion(data: Any, patterns: Sequence[str]) -> tuple[Any, list[str]]:
    """Return ``(data_with_subtrees_zeroed, excluded_paths)``.

    A value whose dotted path matches a pattern (``*`` = any one segment) is
    replaced WHOLESALE by the string ``[EXCLUDED:<path>]`` and not recursed into.
    Pure: does not mutate ``data``.
    """

    pats = [p.split(".") for p in patterns if p]
    excluded: list[str] = []

    def _walk(obj: Any, segs: list[str]) -> Any:
        if segs:
            for pat in pats:
                if _segs_match(segs, pat):
                    path = ".".join(segs)
                    excluded.append(path)
                    return f"[EXCLUDED:{path}]"
        if isinstance(obj, dict):
            return {k: _walk(v, segs + [str(k)]) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(v, segs + [str(i)]) for i, v in enumerate(obj)]
        return obj

    return _walk(data, []), excluded


def scan_excluded_paths(obj: Any) -> list[str]:
    """Collect the paths recorded in ``[EXCLUDED:<path>]`` markers anywhere in a
    (possibly already-redacted) payload — so the manifest count survives a
    re-redaction pass that does not re-apply exclusion."""

    out: list[str] = []
    if isinstance(obj, str):
        m = EXCLUSION_MARKER_RE.match(obj)
        if m:
            out.append(m.group(1))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(scan_excluded_paths(v))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(scan_excluded_paths(v))
    return out


class CapsuleScopeTransformer:
    """Applies configured field-path exclusion to a record's string fields.

    Default-disabled. The capsule envelope path uses :func:`apply_field_exclusion`
    directly; this record-level ``apply`` makes the tool a valid, self-sufficient
    registry citizen for canonical order + provenance.
    """

    name = "capsule_scope"
    display_name = "Capsule scope (field exclusion)"
    kind = "transformer"

    def enabled(self, cfg: Any) -> bool:
        block = cfg_block(cfg, self.name)
        if block is None:
            return False
        return bool(getattr(block, "enabled", False))

    def apply(self, record: TraceRecord, ctx: ToolContext) -> ToolResult:
        block = cfg_block(ctx.cfg, self.name)
        patterns = list(getattr(block, "exclude", []) or []) if block else []
        if not patterns:
            return ToolResult(
                name=self.name, kind=self.kind,
                redactions_applied=0, metadata_patch={"fields_excluded": 0},
            )
        pats = [p.split(".") for p in patterns if p]
        count = 0

        def _t(text: str, path: str, _ft: FieldType) -> str:
            nonlocal count
            segs = path.split(".") if path else []
            for pat in pats:
                if _segs_match(segs, pat):
                    count += 1
                    return f"[EXCLUDED:{path}]"
            return text

        walk_string_fields(record, _t)
        return ToolResult(
            name=self.name, kind=self.kind,
            redactions_applied=count, metadata_patch={"fields_excluded": count},
        )

    def describe(self, cfg: Any) -> ToolInfo:
        is_on = self.enabled(cfg)
        return ToolInfo(
            name=self.name,
            display_name=self.display_name,
            kind=self.kind,
            enabled=is_on,
            state="enabled" if is_on else "disabled",
            detail="excludes prompt-bearing fields (system_layer, reasoning_content) by default",
            setup_cmd=None,
            disable_cmd=None,
        )
