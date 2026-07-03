"""Companion-leaf field-type classification (#143 move 2).

The ctx (``ContextLayer`` / ``context_resume_packet``) and trail (``TrailEvent``)
substrate shapes are sanitized today only by being flattened into the generic
dict floor at ``field_type=GENERAL`` — so the field-appropriate rules
(path-anonymize a ``cwd``, treat a chat message as NER-relevant prose, treat a
tool description as tool input) are LOST at the inline boundary.

``companion_field_type`` is a pure path-classifier (a sibling of
``scanner._classify_tool``) that maps an inlined ctx/trail leaf path onto the
EXISTING four-member ``FieldType`` taxonomy plus an ``is_path_leaf`` flag. It
introduces NO new ``FieldType`` member — reuse is sufficient. It walks nothing;
the walker is :func:`opentraces.security.pipeline.sanitize_companion_dict`.

The default companion floor mirrors ``dataset_rows.DATASET_ROW_FLOOR``: the
zero-dependency detector floor plus ``path_anonymizer`` (which the companion
walker runs, in its tail-consuming mode, over every string leaf).
"""

from __future__ import annotations

import re

from .scanner import FieldType

# Default companion floor. Mirrors ``DATASET_ROW_FLOOR`` (regex, entropy,
# business_logic, path_anonymizer): the in-tree detector floor plus the
# tail-consuming path anonymizer. ``trufflehog`` / ``llm_pii`` / ``classifier``
# are NOT here — trufflehog stays TraceRecord-only for M1, llm_pii is an
# availability-gated NER a caller opts into, classifier is a Judge (a gate, not
# a leaf mutator).
COMPANION_FLOOR: tuple[str, ...] = (
    "regex",
    "entropy",
    "business_logic",
    "path_anonymizer",
)

# A leaf whose name carries a filesystem path (``*cwd*`` / ``*path*`` /
# ``file_path``). Matched on the LEAF segment so an ancestor key never triggers
# a false positive. Path leaves are ALWAYS path-anonymized regardless of the
# detector outcome.
_PATH_LEAF_RE = re.compile(r"(?:cwd|path)", re.IGNORECASE)

# Tool-input surfaces on the ctx ``tool_registry`` layer.
_TOOL_INPUT_HINTS = ("tool_registry", "input_schema", "tool_input")


def _leaf_name(path: str) -> str:
    """Return the last dotted segment, stripped of any trailing ``[n]`` index."""
    if not path:
        return ""
    leaf = path.rsplit(".", 1)[-1]
    return re.sub(r"\[\d+\]$", "", leaf)


def _is_path_leaf(path: str, leaf: str) -> bool:
    return bool(_PATH_LEAF_RE.search(leaf))


def companion_field_type(path: str) -> tuple[FieldType, bool]:
    """Classify an inlined ctx/trail leaf ``path`` onto ``(FieldType, is_path_leaf)``.

    - ctx ``messages[].content`` → ``GENERAL`` (the NER-relevant conversation
      surface; routed so ``privacy_filter`` / ``llm_pii`` fire when present).
    - ctx ``tool_registry[].description`` / tool inputs → ``TOOL_INPUT``.
    - trail ``authored_text`` → ``GENERAL`` prose (visited so the path/secret
      rules run over the exact code an agent wrote).
    - any ``*cwd*`` / ``*path*`` / ``file_path`` leaf → ``is_path_leaf=True``
      (always path-anonymize), keeping ``GENERAL`` / ``TOOL_INPUT`` so the
      secret rules also run.
    - anything else → ``GENERAL``, not a path leaf.
    """
    p = path or ""
    lower = p.lower()
    leaf = _leaf_name(p)
    is_path_leaf = _is_path_leaf(p, leaf)

    # ctx conversation content → GENERAL (NER routing).
    if "messages" in lower and leaf == "content":
        return FieldType.GENERAL, is_path_leaf

    # ctx tool_registry descriptions / tool inputs → TOOL_INPUT.
    if any(hint in lower for hint in _TOOL_INPUT_HINTS):
        return FieldType.TOOL_INPUT, is_path_leaf

    # trail authored_text → GENERAL prose/code.
    if leaf == "authored_text":
        return FieldType.GENERAL, is_path_leaf

    return FieldType.GENERAL, is_path_leaf


__all__ = ["COMPANION_FLOOR", "companion_field_type"]
