"""Agent-as-judge protocol + the append-only verdict / gold ledger.

The ``agent`` judge is the in-loop operating agent (Claude/Codex on the user's subscription),
run LOCALLY, posting verdicts back through a tool. No external LLM endpoint; default CI replays
recorded verdicts. The packet the agent receives is **evidence-blind**: it contains the bound
read-only evidence for ONE (criterion, trace) and the judging question — and deliberately NO
skill text and NO marker tokens, so the channel that made marker-stuffing possible does not
exist at the judge layer.

Ledger integrity (br/56): agent verdicts and human gold are SEPARATE files with SEPARATE
writers. ``post_verdict`` (agent path) can never write the gold file; ``record_human_label``
is the only writer to ``labels/human.jsonl`` and refuses unless ``human_confirm=True`` (wired to
an explicit human keystroke at the CLI). ``judge_id`` is stamped by the caller from the running
session, never trusted from a verdict body — so a verdict cannot impersonate a human.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .evidence import resolve_evidence
from .rubric import Rubric, Verdict, verdict_from_dict

#: A grounding quote must be a verbatim span of evidence VALUES of at least this length, so
#: structural JSON punctuation / single ubiquitous chars cannot satisfy the check (M8).
_MIN_QUOTE_LEN = 4
_MARKER_RE = re.compile(r"rule\[[^\]]*\]")


class JudgeError(ValueError):
    """Raised when a posted verdict fails the on-post integrity checks."""


def _strip_markers(text: str) -> str:
    """Remove SkillOpt marker tokens (rule[...]) so they can never enter the judge packet (M7)."""
    return _MARKER_RE.sub("", text or "")


def _evidence_values_text(payload: Any) -> str:
    """Collect the string/scalar VALUES of an evidence bundle (not its JSON structure) (M8)."""
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            out.append(node)
        elif isinstance(node, (int, float, bool)):
            out.append(str(node))

    walk(payload)
    return "\n".join(out)


def _verdict_id(rubric_id: str, criterion_id: str, trace_id: str, judge_method: str, digest: str) -> str:
    return hashlib.sha256(
        f"{rubric_id}:{criterion_id}:{trace_id}:{judge_method}:{digest}".encode("utf-8")
    ).hexdigest()[:16]


def build_judge_packet(
    rubric: Rubric,
    criterion_id: str,
    trace_id: str,
    *,
    episode: dict[str, Any],
    record: Any = None,
    epoch: str = "e0",
) -> dict[str, Any]:
    """Build the evidence-blind packet the in-loop agent judges (no skill text, no markers)."""
    criterion = next((c for c in rubric.criteria if c.id == criterion_id), None)
    if criterion is None:
        raise JudgeError(f"unknown criterion {criterion_id!r}")
    if criterion.judge_method != "agent":
        raise JudgeError(f"criterion {criterion_id!r} is {criterion.judge_method}, not an agent judge")
    bundle = resolve_evidence(criterion.evidence, trace_id=trace_id, episode=episode,
                              record=record, epoch=epoch)
    request_id = _verdict_id(rubric.rubric_id, criterion_id, trace_id, "agent", bundle["digest"])
    return {
        "judge_request_id": request_id,
        # The trace this verdict is about. post_verdict reads it back so the stored
        # verdict carries a real trace_id; without it the (criterion_id, trace_id)
        # calibration join keys on "" and agent verdicts never match a trace. An opaque
        # id, not skill text: it carries no marker/reward signal (M7 stays intact).
        "trace_id": trace_id,
        "criterion": {
            "criterion_id": criterion_id,
            # marker tokens are stripped so the packet can never carry a SkillOpt reward token (M7)
            "rubric_text": _strip_markers(criterion.rubric_text),
            "direction": criterion.direction,
            "allowed_labels": ["pass", "fail", "abstain"],
        },
        "bound_evidence": bundle["payload"],
        "evidence_digest": bundle["digest"],
        "evidence_epoch": bundle["epoch"],
        "evidence_available": bundle["available"],
        # NB: deliberately NO skill text and NO marker tokens in this packet.
        "instructions": (
            "Render value in [0,1] and a one-sentence rationale grounded in a VERBATIM span of "
            "bound_evidence. Judge the EVIDENCE (what the agent did), not any skill text and not "
            "whether prescribed steps were followed — judge whether the desired OUTCOME was "
            "achieved. If the evidence is insufficient, return label='abstain'."
        ),
    }


def post_verdict(
    package_dir: Path,
    packet: dict[str, Any],
    *,
    value: float,
    rationale: str,
    evidence_quote: str,
    judge_id: str,
    rubric_id: str = "",
) -> Verdict:
    """Record an agent verdict for ``packet``, with on-post integrity checks.

    ``judge_id`` is the running agent identity (e.g. ``agent:claude@<session>``), stamped by the
    caller — never read from a verdict body. A post to the gold ledger is impossible from here.
    """
    if not 0.0 <= float(value) <= 1.0:
        raise JudgeError("value must be in [0,1]")
    if not rationale.strip():
        raise JudgeError("rationale is required for an agent verdict")
    if not str(judge_id).startswith(("agent:", "deterministic:")):
        raise JudgeError("judge_id must be an agent/deterministic identity (gold is written elsewhere)")
    available = packet.get("evidence_available", False)  # (M10) unsafe default abstains, never passes
    # groundedness (M6/M8): the quote must be a verbatim span (>= _MIN_QUOTE_LEN) of evidence
    # VALUES (not the JSON structure) — an empty/whitespace/single-punctuation quote is rejected.
    # Abstaining verdicts (no evidence) are exempt: there is nothing to quote.
    if available:
        q = (evidence_quote or "").strip()
        values_text = _evidence_values_text(packet.get("bound_evidence", {}))
        if len(q) < _MIN_QUOTE_LEN or q not in values_text:
            raise JudgeError(
                f"rationale not grounded: evidence_quote must be a verbatim span "
                f"(>= {_MIN_QUOTE_LEN} chars) of the bound evidence values")
    criterion_id = packet["criterion"]["criterion_id"]
    digest = packet["evidence_digest"]
    trace_id = packet.get("refs", {}).get("trace_id") or packet.get("trace_id") or ""
    vid = packet["judge_request_id"]
    label = "abstain" if not available else ("pass" if value >= 0.5 else "fail")
    verdict = Verdict(
        verdict_id=vid, rubric_id=rubric_id, criterion_id=criterion_id, trace_id=trace_id,
        judge_method="agent", judge_id=judge_id, value=float(value), label=label,
        rationale=rationale[:2000], evidence_digest=digest, evidence_epoch=packet.get("evidence_epoch", "e0"),
        blocked=not available,
        blocked_reason="evidence_unavailable" if not available else "",
    )
    _append_jsonl(package_dir / "verdicts" / "agent.jsonl", verdict.to_dict())
    return verdict


def record_human_label(
    package_dir: Path,
    *,
    rubric_id: str,
    trace_id: str,
    label: int,
    rationale: str = "",
    human_id: str = "human",
    human_confirm: bool = False,
    emulated: bool = False,
) -> dict[str, Any]:
    """The ONLY writer to the gold ledger. Refuses unless a human confirms (or emulated=True).

    ``label`` is the trace-level gold: 1=effective, 0=ineffective. ``emulated=True`` writes a
    transparently-flagged stand-in label (a pipeline demo, not real gold) and is recorded as
    ``human_id="human:emulated"`` so calibration/reporting can keep recommended=False.
    """
    if int(label) not in (0, 1):
        raise JudgeError("label must be 0 (ineffective) or 1 (effective)")
    row = {
        "schema_version": "opentraces.skill_verifier_gold.v1",
        "rubric_id": rubric_id, "trace_id": trace_id, "label": int(label),
        "rationale": rationale[:2000],
        "human_id": ("human:emulated" if emulated else f"human:{human_id}"),
        "emulated": bool(emulated),
    }
    # (M1) emulated labels NEVER touch the gold ledger — they go to a separate file so they can
    # never be read back as human gold or laundered into `calibrated`. The gold ledger
    # (human.jsonl) has exactly ONE writer path: an explicit human keystroke (human_confirm=True).
    if emulated:
        _append_jsonl(package_dir / "labels" / "emulated.jsonl", row)
        return row
    if not human_confirm:
        raise JudgeError("record_human_label requires human_confirm=True (an explicit human keystroke)")
    _append_jsonl(package_dir / "labels" / "human.jsonl", row)
    return row


def read_human_labels(package_dir: Path) -> dict[str, int]:
    """Load REAL gold labels (trace_id -> {0,1}); later rows win. Emulated labels are excluded."""
    out: dict[str, int] = {}
    for row in _read_jsonl(package_dir / "labels" / "human.jsonl"):
        if row.get("emulated"):
            continue  # defense in depth: a stray emulated row in the gold file is ignored
        out[str(row.get("trace_id"))] = int(row.get("label"))
    return out


def read_emulated_labels(package_dir: Path) -> dict[str, int]:
    """Load EMULATED stand-in labels (trace_id -> {0,1}); a pipeline demo, never real gold."""
    out: dict[str, int] = {}
    for row in _read_jsonl(package_dir / "labels" / "emulated.jsonl"):
        out[str(row.get("trace_id"))] = int(row.get("label"))
    return out


def read_verdicts(package_dir: Path, method: str = "agent") -> list[Verdict]:
    """Load verdicts for a judge method, de-duped by verdict_id (last write wins)."""
    by_id: dict[str, Verdict] = {}
    for row in _read_jsonl(package_dir / "verdicts" / f"{method}.jsonl"):
        try:
            v = verdict_from_dict(row)
        except Exception:
            continue
        by_id[v.verdict_id] = v
    return list(by_id.values())


def agent_verdict_map(package_dir: Path) -> dict[tuple[str, str], float]:
    """(criterion_id, trace_id) -> value, for feeding calibrate_rubric."""
    return {(v.criterion_id, v.trace_id): v.value for v in read_verdicts(package_dir, "agent")}


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows
