"""Thin hook-side adapter between a Claude Code session file and
:mod:`opentraces.enrichment.intent`.

The plan 038 Phase 2 split:
    * All summarization logic lives in ``enrichment/intent``. That module
      takes a parsed :class:`TraceRecord` and a model client; it knows
      nothing about Claude Code's hook payload format.
    * This adapter knows how to go from a Claude Code ``on_stop`` hook
      payload (``{"transcript_path": ...}``) to a parsed trace, invoke
      the enrichment module, and return the result. ~30 lines.

Kept intentionally tiny so Hermes (or any future agent) can mirror it
with minimal duplication.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from opentraces_schema import TraceRecord

from ....enrichment.intent import ModelClient, enrich_intent
from ..parse import ClaudeCodeParser

logger = logging.getLogger(__name__)


def parse_session_file(session_path: Path) -> TraceRecord | None:
    """Parse a single Claude Code session JSONL into a TraceRecord.

    Returns None if the file is missing, empty, or the parser rejects it
    (e.g. too corrupted to be useful).
    """
    if not session_path.exists():
        logger.warning("intent_adapter: session file not found: %s", session_path)
        return None
    parser = ClaudeCodeParser()
    try:
        return parser.parse_session(session_path)
    except Exception as exc:
        logger.warning("intent_adapter: parse failed for %s: %s", session_path, exc)
        return None


def enrich_from_hook_payload(
    payload: dict[str, Any],
    model_client: ModelClient,
    *,
    force: bool = False,
) -> TraceRecord | None:
    """Run intent enrichment given an on_stop hook payload.

    Resolves the transcript path from the payload, parses it via the
    Claude Code parser, and hands the trace to :func:`enrich_intent`.
    Returns the (mutated) trace on success, ``None`` when the payload
    doesn't carry a transcript path or the parser failed.

    Does **not** write anything back to disk — that's the caller's
    concern. This function is the pure glue between a payload dict and
    an enriched in-memory trace.
    """
    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        logger.debug("intent_adapter: payload has no transcript_path; skipping")
        return None

    trace = parse_session_file(Path(transcript_path))
    if trace is None:
        return None

    return enrich_intent(trace, model_client, force=force)


__all__ = ["enrich_from_hook_payload", "parse_session_file"]
