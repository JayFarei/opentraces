"""Independent re-observation audit for Context Tree companions (issue #210).

This module is the single verifier for the seal-family W2 lane: it re-reads
raw bucket bytes (``trace.json``, ``context.jsonl.gz``, the shared
``bucket/events/v1`` mirror) directly off disk and cross-checks them,
WITHOUT calling any of the projection/backfill machinery it is meant to
audit (``bucket_envelope.project_per_trace_exports``,
``context_backfill.apply_backfill``, ...). It never trusts a backfill's own
report of what it did.

For every trace it buckets into exactly one of three states:

  - ``consistent``: every ``Step.context_node_id`` stamped in ``trace.json``
    resolves to a ``context_node_observed`` line in that trace's OWN
    ``context.jsonl.gz``, AND the companion's distinct ``context_node_observed``
    node-id SET equals the event-mirror's distinct node-id set for that
    trace_id.
  - ``legitimately_empty``: zero stamped ids AND zero context events for this
    trace anywhere in the event mirror (no context capture ever ran for it).
  - ``inconsistent``: anything else — a dangling stamped id that resolves to
    nothing in the companion, and/or a companion/mirror node-id-set mismatch
    (projection staleness or append failure).

Node-id SETS, not raw event-line counts, are the comparison key: the shared
event mirror is an append-only log and a node's backing
``context_node_observed`` event can legitimately be re-appended (retpark /
re-sync) without changing which distinct nodes exist, so a raw line-count
cross-check produces false positives whenever a node was appended more than
once. Two batches disagreeing about the DISTINCT set of node ids for a trace
is the real staleness/loss signal; a duplicate copy of the same node id is
not.

``scripts/audit_context_companions.py`` is the thin CLI entrypoint over this
engine; ``core/context_backfill.py`` (the W2 backfill verb) reads this
engine's per-trace results to build its plan, but the engine itself never
imports the backfill module — the audit direction is one-way.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context_tree.contract import CONTEXT_EVENT_TYPES

CONTEXT_NODE_OBSERVED = "context_node_observed"


@dataclass
class TraceAudit:
    trace_id: str
    project_slug: str
    classification: str  # consistent | legitimately_empty | inconsistent
    stamped_ids: list[str]
    dangling_ids: list[str]
    companion_context_count: int
    mirror_context_count: int
    reasons: list[str] = field(default_factory=list)
    agent_name: str | None = None
    metadata_source: str | None = None
    mirror_node_ids: frozenset[str] = field(default_factory=frozenset)
    companion_node_ids: frozenset[str] = field(default_factory=frozenset)
    trace_json_path: Path | None = None


@dataclass
class AuditReport:
    bucket_root: Path
    total_traces: int
    consistent: int
    legitimately_empty: int
    inconsistent: int
    traces: list[TraceAudit]

    def counts(self) -> dict[str, int]:
        return {
            "total_traces": self.total_traces,
            "consistent": self.consistent,
            "legitimately_empty": self.legitimately_empty,
            "inconsistent": self.inconsistent,
        }

    def inconsistent_traces(self) -> list[TraceAudit]:
        return [t for t in self.traces if t.classification == "inconsistent"]

    def legitimately_empty_traces(self) -> list[TraceAudit]:
        return [t for t in self.traces if t.classification == "legitimately_empty"]


def _read_gzip_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    out: list[dict[str, Any]] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
    except (OSError, gzip.BadGzipFile, json.JSONDecodeError):
        # A corrupt/unreadable companion is treated as empty content — the
        # caller's mismatch logic will still flag it inconsistent if the
        # mirror or the stamped ids disagree.
        return []
    return out


def load_event_mirror_context_index(
    bucket_root: Path,
) -> tuple[dict[str, int], dict[str, set[str]]]:
    """Stream every batch in ``bucket/events/v1/batches`` ONCE.

    Returns ``(context_count_by_trace, node_ids_by_trace)`` built from raw
    JSON (not the ``TrailEvent`` pydantic model — this is a deliberately
    independent re-parse of the same bytes, not a reuse of the substrate's
    own read path).

    Per issue #210's "a skip is a lie" principle: absence of readable mirror
    data must never be mistaken for proof that zero context events exist. A
    missing ``batches`` dir raises ``FileNotFoundError`` and an
    unreadable/corrupt batch raises ``ValueError`` — both are hard, blocking
    audit errors, never silently treated as "empty".
    """

    batches_dir = bucket_root / "events" / "v1" / "batches"
    context_count: dict[str, int] = {}
    node_ids: dict[str, set[str]] = {}
    if not batches_dir.exists():
        raise FileNotFoundError(
            f"event mirror batches dir missing at {batches_dir} — a missing "
            "mirror cannot be treated as zero context events; run "
            "'opentraces setup watcher tick' or 'opentraces bucket repair' "
            "to (re)build it before auditing"
        )

    for batch_path in sorted(batches_dir.glob("*.jsonl.gz")):
        try:
            with gzip.open(batch_path, "rt", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, gzip.BadGzipFile) as exc:
            raise ValueError(
                f"unreadable event mirror batch {batch_path}: {exc} — a "
                "corrupt batch cannot be silently skipped, it may be hiding "
                "the only record of context events for one or more traces"
            ) from exc
        for line_no, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as exc:
                # Line-level corruption policy (PR #217 real-bucket apply
                # crash follow-up; same "skip is a lie" rule as the
                # batch-level check above): a corrupt line that LOOKS like a
                # context event (cheap string prefilter, no parse needed)
                # may be the only record of a context node — blocking. A
                # corrupt NON-context line (e.g. a truncated attribution
                # event, as observed on the real bucket) is irrelevant to
                # this context audit and is skipped here; the backfill APPLY
                # path counts and surfaces those separately.
                if any(marker in line for marker in CONTEXT_EVENT_TYPES):
                    raise ValueError(
                        f"corrupt context event line in events mirror batch "
                        f"{batch_path} (line {line_no}): {exc} — a context "
                        "event the audit cannot read is a blocking failure, "
                        "not a pass"
                    ) from exc
                continue
            et = d.get("event_type")
            if et not in CONTEXT_EVENT_TYPES:
                continue
            tid = d.get("trace_id")
            payload = d.get("payload")
            if not tid and isinstance(payload, dict):
                tid = payload.get("trace_id")
            if not tid:
                continue
            context_count[tid] = context_count.get(tid, 0) + 1
            if et == CONTEXT_NODE_OBSERVED and isinstance(payload, dict):
                nid = payload.get("node_id")
                if nid:
                    node_ids.setdefault(tid, set()).add(nid)
    return context_count, node_ids


def iter_bucket_traces(bucket_root: Path) -> list[tuple[str, str, Path]]:
    """Enumerate every ``(project_slug, trace_id, trace_json_path)`` on disk.

    Walks the filesystem directly under ``bucket/traces/v1/`` rather than
    trusting ``manifest.json``'s ``traces[]`` inventory — the manifest is
    itself bucket state we do not want to depend on for enumeration
    correctness.
    """

    traces_root = bucket_root / "traces" / "v1"
    out: list[tuple[str, str, Path]] = []
    if not traces_root.exists():
        return out
    for project_dir in sorted(p for p in traces_root.iterdir() if p.is_dir()):
        for trace_dir in sorted(p for p in project_dir.iterdir() if p.is_dir()):
            trace_json = trace_dir / "trace.json"
            if trace_json.exists():
                out.append((project_dir.name, trace_dir.name, trace_json))
    return out


def audit_trace(
    project_slug: str,
    trace_id: str,
    trace_json_path: Path,
    context_path: Path,
    mirror_context_count: dict[str, int],
    mirror_node_ids: dict[str, set[str]],
) -> TraceAudit:
    try:
        record = json.loads(trace_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return TraceAudit(
            trace_id=trace_id,
            project_slug=project_slug,
            classification="inconsistent",
            stamped_ids=[],
            dangling_ids=[],
            companion_context_count=0,
            mirror_context_count=mirror_context_count.get(trace_id, 0),
            reasons=[f"unreadable trace.json: {exc}"],
            trace_json_path=trace_json_path,
        )

    steps = record.get("steps") or []
    stamped_ids = sorted({
        s.get("context_node_id")
        for s in steps
        if isinstance(s, dict) and s.get("context_node_id")
    })

    companion_lines = _read_gzip_jsonl(context_path)
    companion_context_count = len(companion_lines)
    companion_node_ids = {
        line.get("payload", {}).get("node_id")
        for line in companion_lines
        if isinstance(line, dict)
        and line.get("event_type") == CONTEXT_NODE_OBSERVED
        and isinstance(line.get("payload"), dict)
        and line["payload"].get("node_id")
    }

    dangling_ids = sorted(nid for nid in stamped_ids if nid not in companion_node_ids)

    mirror_count = mirror_context_count.get(trace_id, 0)
    mirror_nodes = mirror_node_ids.get(trace_id, set())
    node_sets_match = companion_node_ids == mirror_nodes

    reasons: list[str] = []
    if dangling_ids:
        reasons.append(f"{len(dangling_ids)} stamped id(s) absent from own companion")
    if not node_sets_match:
        missing_from_companion = sorted(mirror_nodes - companion_node_ids)
        extra_in_companion = sorted(companion_node_ids - mirror_nodes)
        if missing_from_companion:
            reasons.append(
                f"{len(missing_from_companion)} node id(s) in the mirror missing "
                f"from the companion (stale projection)"
            )
        if extra_in_companion:
            reasons.append(
                f"{len(extra_in_companion)} node id(s) in the companion absent "
                f"from the mirror"
            )
    # A stamped id absent from the companion but present in the mirror is
    # still a defect (stale companion), already covered by the set-mismatch
    # check above in the common case; also flag if a stamped id resolves to
    # nothing anywhere (mirror included) — the append-failure/no-recovery
    # case scope item 3 targets.
    unrecoverable_dangling = sorted(nid for nid in dangling_ids if nid not in mirror_nodes)
    if unrecoverable_dangling:
        reasons.append(
            f"{len(unrecoverable_dangling)} stamped id(s) resolve to nothing "
            f"anywhere (mirror included) — unrecoverable dangling reference"
        )

    if not stamped_ids and mirror_count == 0 and companion_context_count == 0:
        classification = "legitimately_empty"
    elif not dangling_ids and node_sets_match:
        classification = "consistent"
    else:
        classification = "inconsistent"

    agent = record.get("agent") if isinstance(record.get("agent"), dict) else {}
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}

    return TraceAudit(
        trace_id=trace_id,
        project_slug=project_slug,
        classification=classification,
        stamped_ids=stamped_ids,
        dangling_ids=dangling_ids,
        companion_context_count=companion_context_count,
        mirror_context_count=mirror_count,
        reasons=reasons,
        agent_name=agent.get("name"),
        metadata_source=metadata.get("source"),
        mirror_node_ids=frozenset(mirror_nodes),
        companion_node_ids=frozenset(companion_node_ids),
        trace_json_path=trace_json_path,
    )


def audit_bucket(bucket_root: Path) -> AuditReport:
    """Run the full independent audit over one bucket root.

    Raises if the bucket cannot be read at all (per the loopcraft condition:
    "a skip is an error").
    """

    bucket_root = Path(bucket_root)
    if not bucket_root.exists():
        raise FileNotFoundError(f"bucket root does not exist: {bucket_root}")

    mirror_context_count, mirror_node_ids = load_event_mirror_context_index(bucket_root)

    traces_root = bucket_root / "traces" / "v1"
    if not traces_root.exists():
        raise FileNotFoundError(f"no traces/v1 dir under bucket root: {bucket_root}")

    results: list[TraceAudit] = []
    for project_slug, trace_id, trace_json_path in iter_bucket_traces(bucket_root):
        context_path = trace_json_path.parent / "context.jsonl.gz"
        results.append(
            audit_trace(
                project_slug,
                trace_id,
                trace_json_path,
                context_path,
                mirror_context_count,
                mirror_node_ids,
            )
        )

    consistent = sum(1 for t in results if t.classification == "consistent")
    legit_empty = sum(1 for t in results if t.classification == "legitimately_empty")
    inconsistent = sum(1 for t in results if t.classification == "inconsistent")

    return AuditReport(
        bucket_root=bucket_root,
        total_traces=len(results),
        consistent=consistent,
        legitimately_empty=legit_empty,
        inconsistent=inconsistent,
        traces=results,
    )
