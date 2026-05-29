"""``export_capsule`` — assemble one failing session into a frozen capsule.

This is deliberately ASSEMBLY over existing primitives, not a new event-log
walker (autoreview decision A6): anchor on the single failing
``context_node_id`` and inline its ``context_resume_packet``. The capsule is
bounded by construction because it inherits the trace slice's bounds.

Pipeline: load TraceRecord from the bucket -> resolve failing step + its context
node -> build a bounded slice around it -> inline the context resume packet ->
collect trail anchors + deterministic intent + a public repo pin -> redact the
WHOLE assembled envelope through the mandatory floor (hard gate) -> freeze.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ..bucket_store import read_trace_record_object, trace_record_path
from ..config import get_project_dir
from .contract import build_capsule_id, freeze_capsule
from .redaction import assert_redaction_gate, redact_envelope

_ERROR_MARKERS = re.compile(
    r"\b(traceback|exception|error:|errno|assertion|failed|fatal|"
    r"exit code [1-9]|non-zero|panic|segfault)\b",
    re.IGNORECASE,
)
_MAX_ERROR_EXCERPT = 600


class CapsuleExportError(RuntimeError):
    """Export refused to build a capsule (empty slice, no intent, missing trace)."""


def _opentraces_version() -> str:
    try:
        from opentraces import __version__

        return f"opentraces {__version__}"
    except Exception:  # pragma: no cover - defensive
        return "opentraces (unknown)"


def _step_text(step: Any) -> str:
    """Best-effort flatten of a Step to searchable text."""

    try:
        dumped = step.model_dump(mode="json") if hasattr(step, "model_dump") else dict(step)
    except Exception:  # pragma: no cover - defensive
        return str(step)
    return json.dumps(dumped, ensure_ascii=False, default=str)


def _resolve_failing_step(record: Any, step_index: int | None) -> int:
    steps = list(getattr(record, "steps", []) or [])
    if not steps:
        raise CapsuleExportError(
            "trace has no steps; cannot build a capsule around a failing step."
        )
    if step_index is not None:
        if step_index < 0 or step_index >= len(steps):
            raise CapsuleExportError(
                f"--step {step_index} out of range (0..{len(steps) - 1})."
            )
        return step_index
    # Infer: the LAST step whose serialized content looks like an error.
    last_error = None
    for idx, step in enumerate(steps):
        if _ERROR_MARKERS.search(_step_text(step)):
            last_error = idx
    if last_error is not None:
        return last_error
    # No error marker: fall back to the last step (the session's terminal state).
    return len(steps) - 1


def _node_id_for_step(
    record: Any, project_dir: Path, trace_id: str, step_index: int, slug: str
) -> str | None:
    steps = list(getattr(record, "steps", []) or [])
    if 0 <= step_index < len(steps):
        direct = getattr(steps[step_index], "context_node_id", None)
        if direct:
            return str(direct)
    # The trace's own bucket companion is the authoritative per-step node map.
    from .bucket_context import node_id_for_step_from_bucket

    from_bucket = node_id_for_step_from_bucket(slug, trace_id, step_index)
    if from_bucket:
        return str(from_bucket)
    try:
        from ..context_tree.query import build_context_tree_projection

        projection = build_context_tree_projection(project_dir)
        node = projection.node_for_step(trace_id, step_index)
        if node is not None:
            return str(node.node_id)
    except Exception:  # pragma: no cover - projection optional
        return None
    return None


def _failing_step_summary(record: Any, step_index: int) -> dict[str, Any]:
    steps = list(getattr(record, "steps", []) or [])
    step = steps[step_index]
    text = _step_text(step)
    match = _ERROR_MARKERS.search(text)
    excerpt = ""
    if match:
        start = max(0, match.start() - 120)
        excerpt = text[start : start + _MAX_ERROR_EXCERPT]
    dumped = step.model_dump(mode="json") if hasattr(step, "model_dump") else {}
    return {
        "index": step_index,
        "type": dumped.get("type") or dumped.get("kind") or dumped.get("role"),
        "tool_name": dumped.get("tool_name") or dumped.get("name"),
        "error_excerpt": excerpt,
        "had_error_marker": bool(match),
    }


def _intent_for_step(record: Any, trace_map: Any, step_index: int) -> dict[str, Any]:
    """Deterministic intent from the burst covering the failing step.

    Falls back to the trace's task description (the user's original ask) so the
    capsule always carries SOME captured intent. A capsule with no intent at all
    is refused upstream.
    """

    headline = ""
    most_substantive = None
    trigger = None
    try:
        from ..bursts import detect_bursts

        bursts = detect_bursts(trace_map, trace_record=record, commit_lookup=False)
        for burst in bursts:
            rng = getattr(burst, "step_range", None) or []
            if len(rng) == 2 and rng[0] <= step_index <= rng[1]:
                intent = getattr(burst, "intent", {}) or {}
                most_substantive = intent.get("most_substantive_spec")
                trigger = intent.get("trigger")
                if most_substantive and most_substantive.get("text"):
                    headline = most_substantive["text"]
                elif trigger and trigger.get("text"):
                    headline = trigger["text"]
                break
    except Exception:  # pragma: no cover - bursts optional
        pass
    headline = (headline or "").strip()
    if not headline:
        task = getattr(record, "task", None)
        headline = (getattr(task, "description", "") or "").strip()
    return {
        "headline": headline,
        "most_substantive_spec": most_substantive,
        "trigger": trigger,
    }


def _trail_anchors(project_dir: Path, trace_id: str) -> list[dict[str, Any]]:
    try:
        from ..trails.query import build_trail_query_projection

        projection = build_trail_query_projection(project_dir)
        rows = projection.anchors_for_trace_with_survival(trace_id)
        return [dict(r) for r in rows]
    except Exception:  # pragma: no cover - trail optional
        return []


def _git(project_dir: Path, args: list[str]) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:  # pragma: no cover - git optional
        return None


def _normalize_remote(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    # git@github.com:owner/repo.git -> https://github.com/owner/repo
    ssh = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", url)
    if ssh:
        return f"https://{ssh.group(1)}/{ssh.group(2)}"
    return re.sub(r"\.git$", "", url)


def _repo_pin(
    project_dir: Path,
    record: Any,
    trace_id: str,
    explicit_remote: str | None,
) -> dict[str, Any]:
    task = getattr(record, "task", None)
    outcome = getattr(record, "outcome", None)
    sha = (
        getattr(task, "base_commit", None)
        or getattr(outcome, "commit_sha", None)
        or _git(project_dir, ["rev-parse", "HEAD"])
    )
    remote = (
        explicit_remote
        or getattr(task, "repository_url", None)
        or _normalize_remote(_git(project_dir, ["remote", "get-url", "origin"]))
    )
    reachable = None
    if sha:
        reachable = _git(project_dir, ["cat-file", "-e", f"{sha}^{{commit}}"]) is not None

    # changed files: relative paths only, from the record's patches. The home
    # scrub in redaction handles any absolute leak; we also relativize here.
    changed: set[str] = set()
    for patch in getattr(record, "patches", []) or []:
        fp = getattr(patch, "file_path", None)
        if not fp:
            continue
        candidate = str(fp)
        try:
            candidate = str(Path(candidate).resolve().relative_to(project_dir.resolve()))
        except Exception:
            candidate = Path(candidate).name if Path(candidate).is_absolute() else candidate
        changed.add(candidate)
    return {
        "remote_url": remote,
        "commit_sha": sha,
        "reachable_locally": reachable,
        "changed_files": sorted(changed),
    }


def export_capsule(
    *,
    project_dir: Path,
    trace_id: str,
    step_index: int | None = None,
    node_id: str | None = None,
    radius: int = 4,
    remote_url: str | None = None,
) -> dict[str, Any]:
    """Build a frozen ``opentraces.capsule.v1`` envelope for one failing session.

    Anchors on the single failing ``context_node_id``. Raises
    :class:`CapsuleExportError` for an empty slice or a capsule with no captured
    intent; records (does not raise on) an unresolved context node.
    """

    project_dir = Path(project_dir).resolve()
    slug = get_project_dir(project_dir).name
    obj = read_trace_record_object(trace_record_path(slug, trace_id))
    if obj is None:
        raise CapsuleExportError(
            f"trace {trace_id} not found in the bucket for project {slug!r}. "
            "Run `opentraces trace query --cwd` to find a trace id."
        )
    record = obj.record

    resolved_step = _resolve_failing_step(record, step_index)
    resolved_node = node_id or _node_id_for_step(
        record, project_dir, trace_id, resolved_step, slug
    )

    from ..trace_map import build_trace_map
    from ..trace_slices import slice_around_step

    trace_map = build_trace_map(record)
    slice_payload = slice_around_step(
        trace_map, record, step_index=resolved_step, radius=radius
    )
    if not slice_payload.get("steps") and not slice_payload.get("map_node_refs"):
        raise CapsuleExportError(
            "the slice around the failing step is empty; widen --radius or "
            "pick a different --step."
        )

    limitations: list[str] = list(slice_payload.get("limitations") or [])

    # Context resume packet (the machine reproduction unit). The function never
    # raises: an unresolved node returns an error envelope we record as a
    # limitation rather than failing the export.
    if resolved_node:
        from ..context_tree.resume import context_resume_packet

        from .bucket_context import resume_packet_from_bucket

        # Prefer the live event-log projection; fall back to the trace's own
        # bucket companion (self-sufficient, resolves older captured traces the
        # live ref no longer carries).
        packet = context_resume_packet(project_dir, resolved_node)
        if packet.get("error"):
            bucket_packet = resume_packet_from_bucket(slug, trace_id, resolved_node)
            if not bucket_packet.get("error"):
                packet = bucket_packet
        if packet.get("error"):
            limitations.append("context_node_unresolved")
        for lim in packet.get("limitations") or []:
            limitations.append(str(lim))
    else:
        packet = {
            "schema_version": "opentraces.context_resume.v1",
            "node_id": None,
            "error": "no context node for step",
            "limitations": ["context_node_unavailable"],
        }
        limitations.append("context_node_unresolved")

    intent = _intent_for_step(record, trace_map, resolved_step)
    if not (intent.get("headline") or "").strip():
        raise CapsuleExportError(
            "capsule has no captured intent; the unit of reproduction is intent. "
            "Refusing to export a hollow capsule."
        )

    failing_step = _failing_step_summary(record, resolved_step)
    anchors = _trail_anchors(project_dir, trace_id)
    if not anchors:
        limitations.append("trail_anchors_unavailable")
    repo_pin = _repo_pin(project_dir, record, trace_id, remote_url)
    if not repo_pin.get("commit_sha"):
        limitations.append("repo_pin_no_commit")
    if repo_pin.get("reachable_locally") is False:
        limitations.append("repo_pin_unreachable_locally")

    agent = getattr(record, "agent", None)
    ctx_summary = getattr(record, "context_tree_summary", {}) or {}
    source = {
        "project_slug": slug,
        "trace_id": trace_id,
        "context_node_id": resolved_node,
        "step_index": resolved_step,
        "agent": getattr(agent, "name", None),
        "agent_version": getattr(agent, "version", None),
        "model": getattr(agent, "model", None),
        "capture_method": _capture_method(packet, ctx_summary),
        "completeness": _completeness(packet),
    }

    capsule_id = build_capsule_id(
        trace_id=trace_id,
        node_id=resolved_node,
        start_step_index=int(slice_payload.get("start_step_index", resolved_step)),
        end_step_index=int(slice_payload.get("end_step_index", resolved_step)),
        repo_commit_sha=repo_pin.get("commit_sha"),
    )

    render_state = {
        "redaction": "redacted_ok",
        "closure": "closure_intent_only" if "context_node_unresolved" in limitations else "closure_full",
        "replay": "replay_unverified",
    }

    # Assemble the RAW envelope, then redact the whole thing in one pass.
    raw = freeze_capsule(
        capsule_id=capsule_id,
        source=source,
        intent=intent,
        failing_step=failing_step,
        slice_payload=slice_payload,
        context_resume_packet=packet,
        trail_anchors=anchors,
        repo_pin=repo_pin,
        redaction={"manifest": None},  # placeholder, filled below
        render_state=render_state,
        limitations=limitations,
        created_with=_opentraces_version(),
    )

    # Pull the manifest placeholder out so the redactor never has to reason
    # about redacting its own manifest; redact everything else; reattach.
    raw_redaction = raw.pop("redaction")
    redacted, manifest = redact_envelope(raw)
    assert_redaction_gate(manifest)
    redacted["redaction"] = {"manifest": manifest}
    return redacted


def _capture_method(packet: dict[str, Any], ctx_summary: dict[str, Any]) -> str | None:
    for key in ("system_layer", "messages_layer", "tool_registry_layer", "runtime_state_layer"):
        layer = packet.get(key)
        if isinstance(layer, dict) and layer.get("capture_method"):
            return layer["capture_method"]
    return ctx_summary.get("capture_method")


def _completeness(packet: dict[str, Any]) -> str | None:
    for key in ("system_layer", "messages_layer", "tool_registry_layer", "runtime_state_layer"):
        layer = packet.get(key)
        if isinstance(layer, dict) and layer.get("completeness"):
            return layer["completeness"]
    return None


__all__ = ["CapsuleExportError", "export_capsule"]
