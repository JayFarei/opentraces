"""Provider judge (issue #141): optional unattended fallback.

Resolves all of a slicer's :class:`JudgmentRequest`s in ONE structured model
call via ``core.llm_provider.detect_provider()`` (Claude CLI / Codex CLI /
Anthropic API, in that preference order). Any failure — no provider available,
a malformed response — degrades gracefully to ``{}`` (the slicer's deterministic
defaults), so the operation never crashes on a trace.
"""
from __future__ import annotations

from typing import Any

from .. import steps as S


def _window_view(steps: list[Any], window: list[int]) -> str:
    lo, hi = window[0], window[-1]
    lines = []
    for i in range(max(0, lo), min(len(steps), hi + 1)):
        s = steps[i]
        tools = ",".join(sorted(set(S.tool_names(s))))[:40]
        prev = (S.content(s) or S.obs_text(s) or "").strip().replace("\n", " ")[:80]
        lines.append(f"{i} {S.role(s)} [{tools}] {prev}")
    return "\n".join(lines)


def resolve_via_provider(*, slicer_name: str, steps: list[Any], requests: list[Any]) -> dict:
    try:
        from ...llm_provider import detect_provider
    except Exception:  # noqa: BLE001
        return {}
    provider = detect_provider()
    if provider is None or not requests:
        return {}

    blocks = []
    for r in requests:
        rd = r.to_dict() if hasattr(r, "to_dict") else r
        blocks.append(
            f"### {rd['id']} (kind={rd['kind']})\n{rd['prompt']}\n"
            f"window steps:\n{_window_view(steps, rd['window'])}\n"
            f"allowed decisions: {rd['answer_schema'].get('decision')}"
        )
    user_message = (
        f"You are the cheap-LLM judge for the `{slicer_name}` trace slicer. "
        f"Answer EACH judgment request with a decision drawn from its allowed set "
        f"and a confidence in [0,1].\n\n" + "\n\n".join(blocks)
    )
    schema = {
        "type": "object",
        "properties": {
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "decision": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["id", "decision"],
                },
            }
        },
        "required": ["answers"],
    }
    try:
        result = provider.call_structured(
            system_prompt=(
                "You decide trajectory boundaries for an agent-trace slicer. Be "
                "conservative: only choose the non-default decision when the "
                "evidence is clear."
            ),
            user_message=user_message,
            json_schema=schema,
        )
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for a in (result.payload or {}).get("answers", []):
        if isinstance(a, dict) and a.get("id"):
            out[str(a["id"])] = {
                "decision": str(a.get("decision", "")),
                "confidence": float(a.get("confidence", 1.0) or 1.0),
            }
    return out


# Symmetry with the other judges: the dispatcher calls module.resolve().
def resolve(*, slicer_name: str, steps: list[Any], requests: list[Any]) -> dict:
    return resolve_via_provider(slicer_name=slicer_name, steps=steps, requests=requests)
