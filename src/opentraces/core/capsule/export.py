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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..progress import ProgressLike

from ..bucket_store import read_trace_record_object, trace_record_path
from ..config import get_project_dir
from .contract import build_capsule_id, freeze_capsule
from .redaction import REDACTION_FLOOR, assert_redaction_gate, redact_envelope

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


def _representative_step(record: Any, span_lo: int, span_hi: int) -> int:
    """Anchor step for a ``--from-step/--to-step`` span (#195 D13 seam).

    The span selects the carried slice; the anchor step is derived FROM it (the
    slice picks the scope, not a step/radius knob). Prefer the last error-marked
    step inside the span (so a failure in the range still anchors the failing-step
    card); otherwise the span midpoint.
    """

    steps = list(getattr(record, "steps", []) or [])
    lo, hi = min(span_lo, span_hi), max(span_lo, span_hi)
    last_err: int | None = None
    for idx, step in enumerate(steps):
        si = getattr(step, "step_index", None)
        si = si if isinstance(si, int) else idx
        if lo <= si <= hi and _ERROR_MARKERS.search(_step_text(step)):
            last_err = si
    if last_err is not None:
        return last_err
    return (lo + hi) // 2


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
    # #137 HB#1: source this trace's RECORDED Trail anchors for the carried
    # snapshot. COMPANION-FIRST — read the trace's per-trace ``trail.jsonl.gz`` and
    # build the projection from it; only a MISSING companion (uncaptured / foreign
    # trace) falls back to a bounded live read of the trace's own events. Neither
    # path recomputes CURRENT survival: a capsule is a carried, zero-reachability
    # snapshot, so current anchor liveness (alive_on_path / reverted / lost), which
    # needs a per-anchor live-repo walk, is left to the consumer/gate
    # (``ot trail track``) and declared not-recomputed here. This is both correct
    # (two seals of the same trace stay byte-stable) and fast (seconds, not the
    # ~1000s a 46-anchor live recompute cost).
    try:
        from ..config import get_project_dir

        slug = get_project_dir(project_dir).name
        if slug:
            from .bucket_trail import trail_anchors_from_bucket

            companion = trail_anchors_from_bucket(project_dir, slug, trace_id)
            if companion is not None:
                return companion

        # Bounded live fallback (no companion only): read the trace's own events
        # and carry the recorded anchors (still no survival recompute).
        from ..trails.event_log import read_events_for_trace
        from ..trails.query import build_trail_query_projection_from_events
        from .bucket_trail import recorded_anchor_rows

        events = read_events_for_trace(project_dir, trace_id)
        projection = build_trail_query_projection_from_events(project_dir, events)
        return recorded_anchor_rows(projection, trace_id)
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


def _sha_pushed(project_dir: Path, sha: str | None) -> bool:
    """Minimal LOCAL probe: is ``sha`` reachable from a remote-tracking branch?

    ``git branch -r --contains <sha>`` lists the remote-tracking branches that
    contain the commit; a non-empty result means the commit has been pushed to
    at least one remote. Anything the probe cannot positively confirm (no git,
    an unknown sha, a detached local-only commit) reads as NOT pushed — the
    honest, conservative downgrade the ``repo_pin_unpushed`` limitation records.

    This is deliberately homed HERE, next to ``reachable_locally``, so the #130
    Trail session-origin resolver can later replace it in one place.
    """

    if not sha:
        return False
    out = _git(project_dir, ["branch", "-r", "--contains", f"{sha}^{{commit}}"])
    return bool(out and out.strip())


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
    # #195 — an honest ``pushed`` claim on the pin. ``reachable_locally`` was only
    # ever true because the capsule was built on the capture host; off-machine the
    # pin can be an unfetchable local object. ``pushed`` is the portable claim.
    pushed = _sha_pushed(project_dir, sha)

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
        # #195 additive: the honest off-machine claim (mirrors reachable_locally's
        # additive precedent; NOT in REQUIRED_KEYS, capsule stays v1).
        "pushed": pushed,
        "changed_files": sorted(changed),
    }


def export_capsule(
    *,
    project_dir: Path,
    trace_id: str,
    step_index: int | None = None,
    from_step: int | None = None,
    to_step: int | None = None,
    node_id: str | None = None,
    radius: int = 4,
    remote_url: str | None = None,
    test_command: str | None = None,
    expect_error: str | None = None,
    setup_command: str | None = None,
    consumes: list[dict[str, Any]] | None = None,
    product: str | None = None,
    product_full_span: bool = False,
    include_prompts: bool = False,
    progress: "ProgressLike | None" = None,
) -> dict[str, Any]:
    """Build a frozen ``opentraces.capsule.v1`` envelope for one failing session.

    Anchors on the single failing ``context_node_id``. Raises
    :class:`CapsuleExportError` for an empty slice or a capsule with no captured
    intent; records (does not raise on) an unresolved context node.

    ``progress`` is an optional, keyword-only :class:`~opentraces.core.progress.
    ProgressLike` reporter (issue #98). When omitted it coalesces to a no-op so
    every existing caller (share / issue / all tests) stays byte-identical. Named
    stages are driven at the verified slow points (trace load, slice build,
    context resolve, trail anchors, redact); the reporter's background heartbeat
    covers the in-C blocking inside each projection scan. The reporter is the
    caller's object — the CLI reads ``reporter.telemetry()`` AFTER this returns —
    so the return type is unchanged (no ``(capsule, telemetry)`` tuple).

    ``product_full_span`` (issue #98) opts OUT of the default ``--product`` radius
    cap, restoring the historical unbounded ``min..max`` episode span. Default
    ``False`` bounds the product slice to ``2*radius`` around the first match.
    """

    from ..progress import NullProgress

    reporter: "ProgressLike" = progress if progress is not None else NullProgress()

    project_dir = Path(project_dir).resolve()
    slug = get_project_dir(project_dir).name
    reporter.stage("load_trace")
    obj = read_trace_record_object(trace_record_path(slug, trace_id))
    if obj is None:
        raise CapsuleExportError(
            f"trace {trace_id} not found in the bucket for project {slug!r}. "
            "Run `opentraces trace query --cwd` to find a trace id."
        )
    record = obj.record

    # #195 D13 — the ``--from-step/--to-step`` SPAN seam. When a span is given the
    # carried slice is that explicit range (via ``slice_by_steps``) and the anchor
    # step is DERIVED from it; ``--step``/``--radius`` stay the hidden back-compat
    # convenience. Span takes precedence over the legacy step/radius/product paths.
    span_mode = from_step is not None or to_step is not None
    span_lo = span_hi = None
    if span_mode:
        span_lo = from_step if from_step is not None else to_step
        span_hi = to_step if to_step is not None else from_step
        span_lo, span_hi = min(int(span_lo), int(span_hi)), max(int(span_lo), int(span_hi))
        resolved_step = _representative_step(record, span_lo, span_hi)
    else:
        resolved_step = _resolve_failing_step(record, step_index)
    resolved_node = node_id or _node_id_for_step(
        record, project_dir, trace_id, resolved_step, slug
    )

    from ..trace_map import build_trace_map
    from ..trace_slices import slice_around_step, slice_by_steps, slice_for_product

    reporter.stage("build_slice")
    trace_map = build_trace_map(record)
    product_episode_no_match = False
    if span_mode:
        slice_payload = slice_by_steps(
            trace_map,
            record,
            start_step_index=span_lo,
            end_step_index=span_hi,
            source="capsule_span",
        )
    elif product:
        # Plan 090 — bound the episode to the steps that reference the consumed
        # product. Heuristic (no captured per-step product label); fall back to a
        # radius slice when nothing references it (and record that honestly).
        # Issue #98 — the product episode is bounded to ``2*radius`` by default
        # (the actual hang the issue reported); ``--product-full-span`` opts back
        # into the historical unbounded ``min..max`` span.
        slice_radius = None if product_full_span else radius
        slice_payload = slice_for_product(
            trace_map, record, product_match=product, radius=slice_radius
        )
        if slice_payload is None:
            product_episode_no_match = True
            slice_payload = slice_around_step(
                trace_map, record, step_index=resolved_step, radius=radius
            )
    else:
        slice_payload = slice_around_step(
            trace_map, record, step_index=resolved_step, radius=radius
        )
    if not slice_payload.get("steps") and not slice_payload.get("map_node_refs"):
        raise CapsuleExportError(
            "the slice around the failing step is empty; widen --radius or "
            "pick a different --step."
        )

    limitations: list[str] = list(slice_payload.get("limitations") or [])
    if product:
        limitations.append("product_inferred_not_captured")
        if product_episode_no_match:
            limitations.append("product_episode_no_match")

    # Context resume packet (the machine reproduction unit). The function never
    # raises: an unresolved node returns an error envelope we record as a
    # limitation rather than failing the export.
    reporter.stage("resolve_context")
    if resolved_node:
        from ..context_tree.resume import context_resume_packet

        from .bucket_context import resume_packet_from_bucket

        # #137 HB#1: COMPANION-FIRST, mirroring the trail-anchor face. The trace's
        # own per-trace bucket companion resolves the node self-sufficiently in
        # ~ms, whereas the live ``context_resume_packet`` is the whole-log
        # projection that WEDGES on a mature ref — the SECOND of capsule export's
        # two live-first whole-log walks (the first, trail anchors, is already
        # companion-first). ``context_resume_packet`` never raises, but a wedge is
        # not an error, so live-first never reached the fallback. Try the
        # companion first; fall back to the live read only when the companion
        # cannot resolve the node (uncaptured / foreign trace).
        packet = resume_packet_from_bucket(slug, trace_id, resolved_node)
        if packet.get("error"):
            live_packet = context_resume_packet(project_dir, resolved_node)
            if not live_packet.get("error"):
                packet = live_packet
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

    from .summary import build_summary

    summary = build_summary(
        record=record, slice_payload=slice_payload, failing_step=failing_step, intent=intent,
    )

    # Runnable repro (the article's "replayable test"): a declared command wins;
    # otherwise best-effort extraction from the failing command step.
    from .test_extract import declared_test, extract_test_payload

    test = declared_test(test_command, expect_error) or extract_test_payload(record, resolved_step)
    if test is None:
        limitations.append("no_executable_test")

    # Environment manifest: what's needed to run the test reproducibly (not host-coupled).
    env_obj = getattr(record, "environment", None)
    eco = getattr(env_obj, "language_ecosystem", None)
    # #195 — language_ecosystem shape fix: consumers expect the scalar ``.name``/``str``
    # form, but the schema carries ``list[str]``. Emit the scalar (first element) and
    # DECLARE ``language_ecosystem_shape_defect`` when the source genuinely carries a
    # non-empty list, so a downstream null-resolution bug is a labeled gap, not silent.
    language_ecosystem_shape_defect = False
    if isinstance(eco, str):
        language_ecosystem = eco
    elif isinstance(eco, (list, tuple)):
        language_ecosystem = str(eco[0]) if eco else None
        language_ecosystem_shape_defect = bool(eco)
    elif eco is not None:
        language_ecosystem = getattr(eco, "name", None)
    else:
        language_ecosystem = None
    environment = {
        "dependencies": list(getattr(record, "dependencies", []) or [])[:200],
        "language_ecosystem": language_ecosystem,
        "setup": [setup_command] if setup_command else [],
        # Consumed dependencies the verdict can be re-posed against (plan 089):
        # the library version / API endpoint the client doesn't control.
        "consumes": list(consumes or []),
        # ADR-0008 §5 / #154 — the env_tier trust ordinal, stamped at its L0
        # FLOOR here as a real field so the replay clamp reads it off the
        # environment block and #202's dependency resolver can later RAISE it
        # (L0→L1→L3→L4) WITHOUT a second envelope change. Floor-defaulted +
        # additive (mirrors the honesty front-matter): today's corpus honestly
        # reads L0, so verdict_trust clamps to floor and never over-claims. There
        # is no L2 in the ladder (the L0/L1/L3/L4 gap is deliberate).
        "env_tier": "L0",
    }

    reporter.stage("trail_anchors")
    anchors = _trail_anchors(project_dir, trace_id)
    if not anchors:
        limitations.append("trail_anchors_unavailable")
    else:
        # Declare on the envelope that the carried anchors record their identity
        # and capture-time evidence, but current liveness was NOT recomputed at
        # seal time (it is a reachability-bearing consumer operation; run
        # ``ot trail track`` against the repo). Keeps the capsule a true snapshot.
        from .bucket_trail import SURVIVAL_NOT_RECOMPUTED

        limitations.append(SURVIVAL_NOT_RECOMPUTED)
    repo_pin = _repo_pin(project_dir, record, trace_id, remote_url)
    if not repo_pin.get("commit_sha"):
        limitations.append("repo_pin_no_commit")
    if repo_pin.get("reachable_locally") is False:
        limitations.append("repo_pin_unreachable_locally")
    # #195 — an unpushed pin cannot be fetched off the capture host; say so.
    if repo_pin.get("commit_sha") and repo_pin.get("pushed") is not True:
        limitations.append("repo_pin_unpushed")
    if language_ecosystem_shape_defect:
        limitations.append("language_ecosystem_shape_defect")

    agent = getattr(record, "agent", None)
    ctx_summary = getattr(record, "context_tree_summary", {}) or {}
    capture_method = _capture_method(packet, ctx_summary)
    source = {
        "project_slug": slug,
        "trace_id": trace_id,
        "context_node_id": resolved_node,
        "step_index": resolved_step,
        "agent": getattr(agent, "name", None),
        "agent_version": getattr(agent, "version", None),
        "model": getattr(agent, "model", None),
        "capture_method": capture_method,
        "completeness": _completeness(packet),
        # #195 honesty front-matter (ADR-0008 §4/§5): additive keys, NOT in
        # REQUIRED_KEYS. Each factor floors to its weakest value when un-upgraded,
        # so today's corpus honestly reads L0/floor and never over-claims. Trust
        # rises only when a sibling raises real state (#202 resolver, wheels, microVM).
        "env_tier": "L0",
        "verdict_trust": "floor",
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

    # Plan 090 — usage-episode grouping anchor (one consumed product/dependency).
    # ``product`` is the caller-supplied name; binding is "inferred" because there
    # is no captured per-step product label (it is heuristic string-matching over
    # tool calls — see the product_episode slice). Null-tolerant: None when absent.
    product_anchor = {"name": product, "binding": "inferred"} if product else None

    # Plan 090 — structural egress declaration. Fields are bools/ints/strings only
    # (NEVER a classifier verdict). ``system_prompt_included`` / reasoning reflect the
    # developer's --include-prompts choice; the capsule_scope exclusion (default-off
    # for prompts) is what physically enforces it before redaction runs.
    sys_layer = packet.get("system_layer") if isinstance(packet, dict) else None
    msgs_layer = packet.get("messages_layer") if isinstance(packet, dict) else None
    system_has_content = isinstance(sys_layer, dict) and bool(sys_layer.get("content"))
    messages_present = isinstance(msgs_layer, dict) and bool(msgs_layer.get("content"))
    slice_steps_n = len(slice_payload.get("steps") or [])
    privacy_scope = {
        "system_prompt_included": bool(include_prompts and system_has_content),
        "reasoning_included": bool(include_prompts),
        "messages_included": bool(slice_steps_n > 0 or messages_present),
        # #195 F2 fix — DECLARE, not close. The per-layer ``completeness`` self-report
        # optimistically says ``full`` even when carrying hash-only messages
        # (transcript_reconstruction capture: 0 bodies, sha256 hashes only). Derive
        # from the capture method instead so a consumer gating on ``== full`` is never
        # told sha256 hashes are full message bodies. No gate: hash-only still seals.
        "messages_completeness": (
            "hash_only" if capture_method == "transcript_reconstruction" else "full"
        ),
        "steps_included": slice_steps_n,
        "redaction_floor": list(REDACTION_FLOOR),
        "developer_approved": False,
    }

    # Assemble the RAW envelope, then redact the whole thing in one pass.
    raw = freeze_capsule(
        capsule_id=capsule_id,
        source=source,
        summary=summary,
        test=test,
        environment=environment,
        bundle=None,  # attached at write time when --bundle is requested
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
        product=product_anchor,
        privacy_scope=privacy_scope,
    )

    # Pull the manifest placeholder out so the redactor never has to reason
    # about redacting its own manifest; exclude prompt-bearing fields by default
    # (opt back in with --include-prompts); redact everything else; reattach.
    raw_redaction = raw.pop("redaction")
    from ...security.tools.capsule_scope_tool import DEFAULT_PROMPT_EXCLUDE

    exclude_paths = None if include_prompts else list(DEFAULT_PROMPT_EXCLUDE)
    reporter.stage("redact")
    redacted, manifest = redact_envelope(raw, exclude_paths=exclude_paths)
    assert_redaction_gate(manifest)
    redacted["redaction"] = {"manifest": manifest}
    # #197 — stamp the mini-bucket digest AFTER redaction. The mini-bucket
    # re-redacts the source companions through the substrate capability; its
    # deterministic 16-hex digest is the capsule's integrity claim over the
    # scoped companion content. Stamped post-redaction (a hash is not a secret;
    # 16 hex stays below the entropy floor so it survives ensure_redacted intact)
    # and mirroring the redaction-manifest placeholder pattern above.
    try:
        from .share import build_mini_bucket

        mini = build_mini_bucket(project_dir, slug, [trace_id])
        redacted["mini_bucket_digest"] = mini["digest"]
    except Exception:  # pragma: no cover - mini-bucket is additive, never fatal
        redacted["mini_bucket_digest"] = None
    reporter.done()
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
