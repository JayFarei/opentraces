"""Intent enrichment for change_burst nodes.

Cluster E / Plan 54 intent richness.

A *trigger* is a short user instruction whose only role is authorising a
previously-discussed action ("yes", "go ahead", "let's commit"). It carries
no domain spec — the substantive intent lives in earlier user instructions.

This module provides two surfaces:

* :func:`is_trigger` — boolean test for "is this user_instruction a pure
  trigger" (short imperative, optionally with trailing punctuation, no
  substantive remainder past the trigger phrase).
* :func:`derive_intent_chain` — given the user_instruction nodes of a
  trace and the start step of a burst, return the structured intent
  object: the latest trigger before the burst, the latest non-trigger
  spec before the burst (the "most substantive spec"), and the full
  ordered list of non-trigger user instructions up to and including the
  most substantive spec.

The trigger-detection regexes are intentionally narrow: anchored at the
start, capped at ~120 characters, with a "spec content past trigger"
guard so phrases like "yes implement the redis migration with TLS and
add a benchmark" never get misclassified as triggers.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from opentraces_schema import TraceMapNode

# Hard cap on the considered length of a candidate trigger. Anything
# longer is automatically not a trigger (carries too much content).
TRIGGER_MAX_LEN = 120

# Maximum substantive remainder past a *soft* trigger phrase ("yes",
# "ok", "sure", ...). If more non-whitespace content trails the
# acknowledgement, we treat the whole instruction as a spec, not a
# trigger. ~30 chars admits things like "yes!" / "ok then." while
# rejecting "yes implement the redis migration with TLS".
TRIGGER_SOFT_REMAINDER_MAX = 30

# Maximum substantive remainder past a *directive* trigger phrase
# ("let's go ahead", "ship it", "make the commit", ...). Directive
# phrases routinely carry a short adjunct like "and commit" or "with
# the rename" that is still a trigger, not a spec — give them more
# rope before the cliff. This is the cap used to reject prose like
# "let's go ahead and migrate the redis cluster to use TLS along with
# the new JSON serialization layer".
TRIGGER_DIRECTIVE_REMAINDER_MAX = 80

# Hard cap for any single trigger remainder — used as a safety floor
# even on directive matches.
TRIGGER_REMAINDER_MAX = TRIGGER_DIRECTIVE_REMAINDER_MAX

# Trigger text limit when surfaced in the structured intent (the
# trigger field itself).
TRIGGER_TEXT_DISPLAY_LIMIT = 200

# Spec text limit when surfaced in the structured intent (each spec
# entry, including most_substantive_spec).
SPEC_TEXT_DISPLAY_LIMIT = 500

# Image placeholders Claude Code injects for screenshots/pasted images.
# These have no textual content — they should be filtered from the
# intent chain entirely.
_IMAGE_PLACEHOLDER_PREFIX = "[Image"


# Each entry is (pattern, kind). "soft" patterns are bare
# acknowledgements; spec content past them disqualifies. "directive"
# patterns carry an action verb and tolerate a short adjunct.
_TRIGGER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), kind)
    for p, kind in (
        # Pure acknowledgements.
        (r"^\s*(yes|ok|okay|sure|right|exactly|good|great|perfect)[\s,.!]?$", "soft"),
        (r"^\s*(yes|ok|okay|sure|right|exactly|good|great|perfect)[\s,.!]+", "soft"),
        # Directives — short imperative authorising the discussed action.
        (
            r"^\s*(lets?\s+go\s+ahead|lets?\s+(just|do|run|commit|ship|make|continue))",
            "directive",
        ),
        (r"^\s*(let'?s\s+go|let'?s\s+do|do\s+it|ship\s+it|run\s+it|now\s+do)", "directive"),
        (r"^\s*(go\s+ahead|continue|proceed)", "directive"),
        (r"^\s*(make\s+(a|the)\s+commit|commit\s+(it|this))", "directive"),
        (r"^\s*(why\s+don'?t\s+we|why\s+not)", "directive"),
    )
)


def is_trigger(text: str | None, max_len: int = TRIGGER_MAX_LEN) -> bool:
    """Return True iff ``text`` is a short trigger phrase.

    A trigger is a *short imperative* authorising action; it carries no
    domain spec. The check has three gates:

    1. Length cap. Stripped text longer than ``max_len`` characters is
       never a trigger (real triggers are short).
    2. Phrase prefix match. The text must start with one of the known
       trigger phrases (case-insensitive, see ``_TRIGGER_PATTERNS``).
    3. Remainder cap. After the matched prefix, any substantive
       remainder (more than ``TRIGGER_REMAINDER_MAX`` non-whitespace
       characters) disqualifies the message — that's a spec, not a
       trigger.

    Image placeholders (``[Image: ...]``) are filtered out separately
    by :func:`derive_intent_chain` and are not considered text user
    instructions; for safety this function also returns False for them.
    """
    if not text:
        return False
    stripped = _strip_image_placeholders(text).strip()
    if not stripped:
        # Pure image placeholder, no text — never a trigger.
        return False
    if len(stripped) > max_len:
        return False
    for pattern, kind in _TRIGGER_PATTERNS:
        match = pattern.match(stripped)
        if not match:
            continue
        remainder = stripped[match.end():].strip(" .,!?")
        cap = (
            TRIGGER_SOFT_REMAINDER_MAX
            if kind == "soft"
            else TRIGGER_DIRECTIVE_REMAINDER_MAX
        )
        if len(remainder) > cap:
            # Substantive spec follows the trigger phrase — not a
            # pure trigger.
            return False
        return True
    return False


def _strip_image_placeholders(text: str) -> str:
    """Strip leading ``[Image: ...]`` / ``[Image #N]`` markers.

    Some user instructions start with one or more image-placeholder
    blocks before substantive text (the user pasted a screenshot then
    typed a question). We want the substantive text on the chain, not
    the placeholder. The pattern is intentionally narrow: we only
    strip placeholders that are at the start of a stripped string.
    """
    out = text.lstrip()
    while out.startswith(_IMAGE_PLACEHOLDER_PREFIX):
        end = out.find("]")
        if end == -1:
            break
        out = out[end + 1:].lstrip()
    return out


def _is_image_placeholder(text: str | None) -> bool:
    """Return True iff ``text`` carries no substantive content past
    image-placeholder markers."""
    if not text:
        return False
    stripped = _strip_image_placeholders(text)
    return not stripped


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _ordered_user_instructions(
    nodes: Iterable[TraceMapNode],
) -> list[TraceMapNode]:
    """Return ``user_instruction`` nodes ordered by ``step_index`` ascending.

    Nodes without a step_index are dropped — they cannot be placed on
    the chain. Image-placeholder messages are filtered too; they carry
    no text.
    """
    result: list[TraceMapNode] = []
    for node in nodes:
        if node.action_type != "user_instruction":
            continue
        if node.step_index is None:
            continue
        if _is_image_placeholder(node.text_preview):
            continue
        result.append(node)
    result.sort(key=lambda n: n.step_index or 0)
    return result


def derive_intent_chain(
    user_instruction_nodes: Iterable[TraceMapNode],
    burst_step_start: int,
) -> dict[str, Any]:
    """Derive the structured intent for a burst.

    Walks the chain of user instructions up to ``burst_step_start`` and
    splits them into triggers vs specs.

    Parameters
    ----------
    user_instruction_nodes:
        ``user_instruction`` nodes from the trace map. Order is
        normalised internally by ``step_index``.
    burst_step_start:
        The first ``step_index`` of the burst. Only user instructions
        with ``step_index <= burst_step_start`` are considered.

    Returns
    -------
    A dict with three keys:

    * ``trigger`` — the latest trigger user_instruction with
      ``step_index <= burst_step_start``, as
      ``{"step": int, "text": str <= 200 chars}``, or ``None``.
    * ``most_substantive_spec`` — the latest non-trigger user
      instruction with ``step_index <= burst_step_start``, as
      ``{"step": int, "text": str <= 500 chars}``, or ``None``.
    * ``spec_chain`` — ordered list of every non-trigger user
      instruction from start of trace up to and including the most
      substantive spec, each as ``{"step": int, "text": str <= 500
      chars}``. If there is no substantive spec, this list is empty.
    """
    ordered = _ordered_user_instructions(user_instruction_nodes)
    in_window = [n for n in ordered if (n.step_index or 0) <= burst_step_start]

    trigger_entry: dict[str, Any] | None = None
    spec_entry: dict[str, Any] | None = None

    # Walk in reverse: latest trigger and latest spec wins.
    for node in reversed(in_window):
        text = (node.text_preview or "").strip()
        if not text:
            continue
        step = node.step_index
        if step is None:
            continue
        if is_trigger(text):
            if trigger_entry is None:
                trigger_entry = {
                    "step": step,
                    "text": _truncate(text, TRIGGER_TEXT_DISPLAY_LIMIT),
                }
        else:
            if spec_entry is None:
                spec_entry = {
                    "step": step,
                    "text": _truncate(text, SPEC_TEXT_DISPLAY_LIMIT),
                }
        if trigger_entry is not None and spec_entry is not None:
            break

    spec_chain: list[dict[str, Any]] = []
    if spec_entry is not None:
        spec_step = spec_entry["step"]
        for node in ordered:
            if (node.step_index or 0) > spec_step:
                break
            text = (node.text_preview or "").strip()
            if not text:
                continue
            if is_trigger(text):
                continue
            spec_chain.append(
                {
                    "step": node.step_index,
                    "text": _truncate(text, SPEC_TEXT_DISPLAY_LIMIT),
                }
            )

    return {
        "trigger": trigger_entry,
        "most_substantive_spec": spec_entry,
        "spec_chain": spec_chain,
    }


def derive_subagent_intent(
    dispatch_node: TraceMapNode,
    dispatch_step: int | None,
) -> dict[str, Any]:
    """Derive the structured intent for a *sub-agent* burst.

    A sub-agent's driving instruction is the ``Task``/``Agent`` tool
    prompt the parent passed, not a ``user_instruction``. That prompt is
    surfaced on the dispatching ``subagent_call`` node's metadata under
    ``subagent_dispatch`` (see ``core.trace_map``). This returns the same
    shape as :func:`derive_intent_chain` so the burst-intent surface stays
    uniform, with the spec sourced from the Task ``prompt`` (falling back
    to ``description``) and an extra ``subagent`` block recording
    provenance.

    Returns ``None`` when the dispatch node carries no usable prompt or
    description — callers should then fall back to the user-instruction
    chain.
    """
    meta = getattr(dispatch_node, "metadata", None) or {}
    dispatch = meta.get("subagent_dispatch")
    if not isinstance(dispatch, dict):
        return None  # type: ignore[return-value]

    prompt = (dispatch.get("prompt") or "").strip()
    description = (dispatch.get("description") or "").strip()
    subagent_type = dispatch.get("subagent_type")
    spec_text = prompt or description
    if not spec_text:
        return None  # type: ignore[return-value]

    spec_entry = {
        "step": dispatch_step,
        "text": _truncate(spec_text, SPEC_TEXT_DISPLAY_LIMIT),
    }
    return {
        "trigger": None,
        "most_substantive_spec": spec_entry,
        "spec_chain": [spec_entry],
        "subagent": {
            "subagent_type": subagent_type if isinstance(subagent_type, str) else None,
            "description": (
                _truncate(description, SPEC_TEXT_DISPLAY_LIMIT) if description else None
            ),
            "dispatch_step": dispatch_step,
        },
    }


__all__ = [
    "derive_intent_chain",
    "derive_subagent_intent",
    "is_trigger",
    "TRIGGER_MAX_LEN",
    "TRIGGER_REMAINDER_MAX",
    "TRIGGER_TEXT_DISPLAY_LIMIT",
    "SPEC_TEXT_DISPLAY_LIMIT",
]
