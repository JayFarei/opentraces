#!/usr/bin/env python3
"""Replay-contract capture-completeness audit (seal-family W6, issue #214).

The independent, bucket-bytes verifier that keeps W2's context-companion
integrity fix (issue #210) permanent. It re-observes the raw bucket on disk,
never a producer's own report, and answers one question for a captured trace:

    Does this trace carry the context data its replay consumers need?

For a trace it asserts three invariants, keyed to the honest promise of the
capture method that produced it:

  (a) every ``Step.context_node_id`` stamped on ``trace.json`` resolves to a
      ``context_node_observed`` line inside that trace's OWN
      ``context.jsonl.gz`` (no dangling stamp — the exact shape the W2 defect
      produced: a step pointing at a node that was never persisted);
  (b) the companion is non-empty whenever the trace is context-capable
      (its ``context_tree_summary.capture_methods`` is non-empty, i.e. it was
      produced by a source that emits context: claude / codex / pi / otel),
      AND whenever context events for the trace exist in the bucket event
      mirror — a zero-event companion on a context-capable source is a RED,
      not a pass (this is what kills the vacuous-green where "no events" is
      mistaken for "nothing to check");
  (c) the per-trace companion context-event count equals the event mirror's
      count for that ``trace_id`` (a stale companion written before its events
      landed is a RED).

Every trace buckets into exactly ONE of:

  * ``consistent``          — invariants hold; replay consumers will resolve.
  * ``legitimately_empty``  — NOT context-capable, no events anywhere, no
                              stamps: correct emptiness (pre-plan-077 claude,
                              hermes, git post-commit, pre-hook codex). Never
                              fabricate context for these.
  * ``inconsistent``        — any invariant above is violated.

Skip is a lie: if the trace dir, the companion, or the event mirror cannot be
read, that is a BLOCKING error (exit 2), never a silent pass.

Exit codes:
  0  audited trace(s) are all consistent or legitimately-empty
  7  at least one trace is inconsistent (the sentinel RED)
  2  a blocking read/usage error (skip-is-a-lie)

Pure stdlib so any interpreter can run it (the otbox journey runs it with the
harness ``{py_bin}`` over a box's bucket; the pytest imports ``classify_trace``
and feeds it real fault-injected capture output).
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = "opentraces.replay_audit.v1"

EXIT_OK = 0
EXIT_INCONSISTENT = 7
EXIT_BLOCKED = 2

# A source is "context-capable" when its capture produced these methods. An
# empty set means no context capture ran for the trace (legitimately empty is
# then permitted).
_CONTEXT_EVENT_PREFIX = "context_"
_NODE_EVENT = "context_node_observed"


class AuditError(Exception):
    """A blocking read/usage error. Never downgraded to a pass."""


@dataclass
class ClassifyResult:
    status: str  # consistent | legitimately_empty | inconsistent
    ok: bool
    dangling_node_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    stamped_count: int = 0
    companion_node_count: int = 0
    companion_ctx_event_count: int = 0
    mirror_ctx_event_count: int = 0
    context_capable: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "ok": self.ok,
            "context_capable": self.context_capable,
            "stamped_count": self.stamped_count,
            "companion_node_count": self.companion_node_count,
            "companion_ctx_event_count": self.companion_ctx_event_count,
            "mirror_ctx_event_count": self.mirror_ctx_event_count,
            "dangling_node_ids": list(self.dangling_node_ids),
            "reasons": list(self.reasons),
        }


def classify_trace(
    *,
    stamped_ids: Iterable[str],
    companion_node_ids: Iterable[str],
    companion_ctx_event_count: int,
    mirror_ctx_event_count: int,
    context_capable: bool,
) -> ClassifyResult:
    """Classify one trace from its already-extracted signals.

    This is the load-bearing invariant. The CLI wraps it over bucket files;
    the pytest feeds it real fault-injected capture output directly.
    """
    stamped = [s for s in stamped_ids if s]
    companion = set(x for x in companion_node_ids if x)
    dangling = sorted(set(stamped) - companion)
    reasons: list[str] = []

    companion_empty = len(companion) == 0 and companion_ctx_event_count == 0

    # (a) dangling stamps — a Step points at a node absent from its own
    #     companion. This is the exact W2 defect shape.
    if dangling:
        reasons.append(
            f"{len(dangling)} stamped context_node_id(s) absent from companion "
            f"(dangling): {dangling[:3]}{'…' if len(dangling) > 3 else ''}"
        )

    # (b) vacuous-green guard: a context-capable source MUST have a non-empty
    #     companion. Zero events is not 'nothing to check' — it is a lost
    #     capture.
    if context_capable and companion_empty:
        reasons.append(
            "context-capable source (non-empty capture_methods) but the "
            "companion has zero context events — capture was lost, not absent"
        )

    # (b') staleness / mirror divergence: events exist in the mirror but the
    #     companion never received them, or the counts diverge.
    if mirror_ctx_event_count > 0 and companion_ctx_event_count == 0:
        reasons.append(
            f"event mirror has {mirror_ctx_event_count} context event(s) for "
            "this trace but the companion is empty (stale projection)"
        )
    elif companion_ctx_event_count != mirror_ctx_event_count:
        reasons.append(
            f"companion context-event count ({companion_ctx_event_count}) != "
            f"event-mirror count ({mirror_ctx_event_count}) for this trace "
            "(stale / partial projection)"
        )

    if reasons:
        status = "inconsistent"
        ok = False
    elif (
        not context_capable
        and companion_empty
        and mirror_ctx_event_count == 0
        and not stamped
    ):
        status = "legitimately_empty"
        ok = True
    else:
        status = "consistent"
        ok = True

    return ClassifyResult(
        status=status,
        ok=ok,
        dangling_node_ids=dangling,
        reasons=reasons,
        stamped_count=len(stamped),
        companion_node_count=len(companion),
        companion_ctx_event_count=companion_ctx_event_count,
        mirror_ctx_event_count=mirror_ctx_event_count,
        context_capable=context_capable,
    )


# --------------------------------------------------------------------------
# bucket readers (pure stdlib; never import opentraces)
# --------------------------------------------------------------------------
def _read_gz_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _read_trace_json(trace_dir: Path) -> dict:
    tj = trace_dir / "trace.json"
    if not tj.exists():
        raise AuditError(f"trace.json missing under {trace_dir}")
    try:
        return json.loads(tj.read_text())
    except (OSError, ValueError) as exc:
        raise AuditError(f"cannot read {tj}: {exc}") from exc


def _stamped_ids(record: dict) -> list[str]:
    return [
        s.get("context_node_id")
        for s in record.get("steps", [])
        if isinstance(s, dict) and s.get("context_node_id")
    ]


def _capture_methods(record: dict) -> list[str]:
    summary = record.get("context_tree_summary") or {}
    return list(summary.get("capture_methods") or [])


def _companion_signals(trace_dir: Path) -> tuple[set[str], int]:
    """Return (node_ids, context-event count) from the trace's own companion.

    A MISSING companion is treated as an empty one (0 events); an UNREADABLE
    companion is a blocking error (skip-is-a-lie).
    """
    comp = trace_dir / "context.jsonl.gz"
    if not comp.exists():
        return set(), 0
    try:
        rows = _read_gz_jsonl(comp)
    except (OSError, ValueError) as exc:
        raise AuditError(f"cannot read companion {comp}: {exc}") from exc
    node_ids: set[str] = set()
    ctx_events = 0
    for e in rows:
        et = e.get("event_type", "")
        if et.startswith(_CONTEXT_EVENT_PREFIX):
            ctx_events += 1
        if et == _NODE_EVENT:
            nid = (e.get("payload") or {}).get("node_id")
            if nid:
                node_ids.add(nid)
    return node_ids, ctx_events


def _mirror_ctx_count(events_dir: Path, trace_id: str) -> int:
    """Count context events for ``trace_id`` across the bucket event mirror.

    A MISSING mirror is a blocking error (skip-is-a-lie): the audit cannot
    honestly certify completeness without it.
    """
    batches = events_dir / "batches"
    if not batches.is_dir():
        raise AuditError(
            f"bucket event mirror missing at {batches} — cannot cross-check "
            "companion completeness (skip is a lie)"
        )
    count = 0
    for fn in sorted(glob.glob(str(batches / "*.jsonl.gz"))):
        try:
            rows = _read_gz_jsonl(Path(fn))
        except (OSError, ValueError) as exc:
            raise AuditError(f"cannot read event batch {fn}: {exc}") from exc
        for e in rows:
            if e.get("trace_id") != trace_id:
                continue
            if str(e.get("event_type", "")).startswith(_CONTEXT_EVENT_PREFIX):
                count += 1
    return count


def _resolve_trace_dir(bucket: Path, trace_id: str) -> Path:
    matches = sorted(
        Path(p)
        for p in glob.glob(str(bucket / "traces" / "v1" / "*" / trace_id))
        if Path(p).is_dir()
    )
    if not matches:
        raise AuditError(
            f"no trace dir for trace_id {trace_id!r} under {bucket / 'traces' / 'v1'}"
        )
    if len(matches) > 1:
        raise AuditError(
            f"trace_id {trace_id!r} resolves to {len(matches)} project slugs: {matches}"
        )
    return matches[0]


def audit_trace(bucket: Path, trace_id: str) -> ClassifyResult:
    """Audit ONE trace by id under a bucket root."""
    trace_dir = _resolve_trace_dir(bucket, trace_id)
    record = _read_trace_json(trace_dir)
    stamped = _stamped_ids(record)
    capture_methods = _capture_methods(record)
    companion_nodes, companion_ctx = _companion_signals(trace_dir)
    mirror_ctx = _mirror_ctx_count(bucket / "events" / "v1", trace_id)
    return classify_trace(
        stamped_ids=stamped,
        companion_node_ids=companion_nodes,
        companion_ctx_event_count=companion_ctx,
        mirror_ctx_event_count=mirror_ctx,
        context_capable=bool(capture_methods),
    )


def _iter_all_trace_ids(bucket: Path) -> list[str]:
    ids: list[str] = []
    for p in sorted(glob.glob(str(bucket / "traces" / "v1" / "*" / "*"))):
        pp = Path(p)
        if pp.is_dir() and (pp / "trace.json").exists():
            ids.append(pp.name)
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay-contract capture-completeness audit")
    ap.add_argument("--bucket", required=True, help="bucket root (…/.opentraces/bucket)")
    ap.add_argument("--trace-id", help="audit one trace by id (default: every trace)")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    args = ap.parse_args(argv)

    bucket = Path(args.bucket)
    try:
        if not bucket.is_dir():
            raise AuditError(f"bucket root not a directory: {bucket}")
        trace_ids = [args.trace_id] if args.trace_id else _iter_all_trace_ids(bucket)
        if not trace_ids:
            raise AuditError(f"no traces found under {bucket}")
        results = {tid: audit_trace(bucket, tid) for tid in trace_ids}
    except AuditError as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "verdict": "blocked",
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"BLOCKED: {exc}", file=sys.stderr)
        return EXIT_BLOCKED

    counts = {"consistent": 0, "legitimately_empty": 0, "inconsistent": 0}
    for r in results.values():
        counts[r.status] += 1
    overall_ok = counts["inconsistent"] == 0
    payload = {
        "schema_version": SCHEMA_VERSION,
        "verdict": "consistent" if overall_ok else "inconsistent",
        "ok": overall_ok,
        "trace_count": len(results),
        "counts": counts,
        "traces": {tid: r.to_dict() for tid, r in results.items()},
    }
    if args.trace_id:
        # Single-trace convenience: hoist the one result to the top level so a
        # journey can assert on stable paths (status/ok/dangling_node_ids).
        only = results[args.trace_id]
        payload.update(only.to_dict())
        payload["trace_id"] = args.trace_id

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"verdict={payload['verdict']} counts={counts}")
        for tid, r in results.items():
            if not r.ok:
                print(f"  INCONSISTENT {tid}: {'; '.join(r.reasons)}", file=sys.stderr)

    return EXIT_OK if overall_ok else EXIT_INCONSISTENT


if __name__ == "__main__":
    raise SystemExit(main())
