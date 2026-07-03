"""Workflow judgment handshake (#186) — the ``rc=10`` needs-judgment contract.

Mirrors ``core/slicing/contract.py`` VERBATIM in structure for workflows: a
deterministic builder that, when judgment is needed, emits structured
``JudgmentRequest``s and exits ``rc=10``; the invoking agent answers and re-runs
with the answers supplied; the answers are recorded in the versioned run packet
so the projection is a pure function of ``(workflow, bucket_state, answers)``.
Re-running with the same answers is byte-identical.

The ``JudgmentRequest`` / ``JudgmentAnswer`` models are REUSED from
``core/slicing/models.py`` (imported, not forked) — one answer shape across the
whole system — so the ``judgment_schema_version`` here references the shared
``opentraces.slicing.judgment.v1``.

Subprocess boundary contract (slicing is in-process; workflows are a subprocess):
a workflow's ``scripts/build_rows.py`` declares judgment points by WRITING its
``JudgmentRequest``s to the sidecar file named by the ``OT_JUDGMENT_SIDECAR``
env var and exiting :data:`RC_NEEDS_JUDGMENT`. The script executor detects the
sidecar + ``rc=10`` and propagates the handshake up; the CLI renders the
requests + instruction and exits ``rc=10``. On re-run the answers travel INTO
the run packet (``packet["answers"]``), which the builder reads to finalize
its rows deterministically.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .slicing.contract import JUDGMENT_SCHEMA_VERSION
from .slicing.models import JudgmentAnswer, JudgmentRequest

__all__ = [
    "WORKFLOW_NEEDS_JUDGMENT_SCHEMA_VERSION",
    "JUDGMENT_SCHEMA_VERSION",
    "RC_NEEDS_JUDGMENT",
    "OT_JUDGMENT_SIDECAR_ENV",
    "JudgmentRequest",
    "JudgmentAnswer",
    "build_needs_judgment",
    "load_answers",
    "read_sidecar_requests",
]

WORKFLOW_NEEDS_JUDGMENT_SCHEMA_VERSION = "opentraces.workflow.needs_judgment.v1"

# Return code the CLI exits with when an agent-loop judgment is needed. Mirrors
# ``core.slicing.contract.RC_NEEDS_JUDGMENT`` — the same slicing precedent.
RC_NEEDS_JUDGMENT = 10

# Env var naming the sidecar path a build_rows.py writes its JudgmentRequests to
# (and the executor reads back) across the subprocess boundary.
OT_JUDGMENT_SIDECAR_ENV = "OT_JUDGMENT_SIDECAR"

_DEFAULT_INSTRUCTION = (
    "This workflow needs judgments to finalize its rows deterministically. "
    "Answer EACH request above with a decision from its answer_schema.decision "
    "set and a confidence in [0,1], write them to an answers JSON file shaped "
    'like {"answers": [{"id": "...", "decision": "...", "confidence": 0.9}]}, '
    "and re-run the same `ot dataset run` command with --answers <file>. The "
    "answers are recorded in the run packet, so the same answers -> "
    "byte-identical rows."
)


def build_needs_judgment(
    *,
    workflow: str,
    judgment_requests: list[Any],
    instruction: str | None = None,
) -> dict[str, Any]:
    """Build the ``opentraces.workflow.needs_judgment.v1`` handshake envelope.

    ``judgment_requests`` may be :class:`JudgmentRequest` instances or already
    plain dicts (as read back from a subprocess sidecar).
    """
    return {
        "schema_version": WORKFLOW_NEEDS_JUDGMENT_SCHEMA_VERSION,
        "status": "needs_judgment",
        "workflow": workflow,
        "judgment_schema_version": JUDGMENT_SCHEMA_VERSION,
        "judgment_requests": [
            (r.to_dict() if hasattr(r, "to_dict") else r) for r in judgment_requests
        ],
        "instruction": instruction or _DEFAULT_INSTRUCTION,
    }


def load_answers(path: str | Path) -> dict[str, dict[str, Any]]:
    """Parse a ``--answers`` JSON file into ``{id: {decision, confidence}}``.

    Twin of ``core.slicing.load_answers``. Accepts ``{"answers": [answer, ...]}``
    or a bare ``[answer, ...]`` list.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw.get("answers", raw) if isinstance(raw, dict) else raw
    out: dict[str, dict[str, Any]] = {}
    for a in items or []:
        ans = JudgmentAnswer.from_dict(a)
        out[ans.id] = {"decision": ans.decision, "confidence": ans.confidence}
    return out


def read_sidecar_requests(path: str | Path) -> list[dict[str, Any]]:
    """Parse the JudgmentRequests a ``build_rows.py`` wrote to its sidecar.

    Accepts ``{"judgment_requests": [request, ...]}`` or a bare
    ``[request, ...]`` list; returns the request dicts as written.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("judgment_requests", raw.get("requests", []))
    else:
        items = raw
    return [dict(item) for item in (items or [])]
