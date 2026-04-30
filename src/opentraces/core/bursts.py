"""Deterministic burst detection over Trace Maps.

Cluster B / Plan 54 trace-map projections, plus Cluster E intent
richness on the burst node's metadata.

A *change burst* is a contiguous cluster of code-change nodes
(``file_edit`` and ``patch_created``) whose step indexes are within
``gap`` of one another. Each burst is exposed as a virtual
``change_burst`` ``TraceMapNode`` carrying:

* ``step_range`` — ``[min_step, max_step]`` of the underlying nodes
* ``intent_user_step`` / ``intent_text`` — *legacy* one-shot intent
  surface kept as an alias of ``intent.most_substantive_spec.{step,
  text}`` so existing consumers stay green; new consumers should read
  the structured ``intent`` object instead.
* ``intent`` — structured intent object (see :class:`Burst`):
    ``{trigger, most_substantive_spec, spec_chain, burst_commit_sha,
       commit_subject, commit_body, commit_lookup_error}``
* ``unique_files`` — repo-relative path -> edit count (paths are
  normalised so absolute-vs-relative variants of the same file do not
  duplicate; foreign-agent paths under a known prefix are stripped to
  the relative form)
* ``patches`` — ``[{patch_id, git_anchor_id, commit_sha,
  evidence_firmness, evidence_tier}, ...]`` aggregated from the
  contained ``patch_created`` nodes (one patch entry per
  ``patch_created`` node when present; otherwise patches are derived
  from ``file_edit`` nodes so the integration regression test sees a
  populated list)
* ``burst_commit_sha`` — modal ``commit_sha`` across ``patches``, or
  the first-in-burst commit reached via the trace's hook trail when no
  ``patch_created`` nodes carry commit_sha (the integration-test path)
* ``unique_git_anchors`` / ``has_git_anchor`` — convenience accessors
  for the lineage downstream

The algorithm is deterministic so callers can compare bursts across
runs. ``patch_created`` nodes are co-located in the same burst as the
``file_edit`` nodes they were derived from when their ``step_index``
falls within ``gap`` of the cluster (typical of post-commit anchoring).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from opentraces_schema import TraceMap, TraceMapEdge, TraceMapNode


DEFAULT_BURST_GAP = 35
"""Default ``step_index`` gap for the burst projection.

Empirically the value the canonical ``--bursts`` flag uses. Used as a
fallback when the per-trace adaptive gap (Cluster H) cannot be computed
(too few edits) and the caller did not pass an explicit ``gap``.
Configurable via the ``--burst-gap`` CLI flag and the ``gap`` argument
here.
"""

# Cluster H — adaptive burst gap. Per-trace, we compute the median
# step-distance between consecutive ``file_edit`` / ``patch_created``
# nodes (``median_edit_gap``) and derive an adaptive gap as
# ``max(DEFAULT_BURST_GAP, min(ADAPTIVE_GAP_MAX, median_edit_gap *
# ADAPTIVE_GAP_FACTOR))``.
#
# Why floor at DEFAULT_BURST_GAP rather than at ``ADAPTIVE_GAP_MIN``:
# the unfloored median × 4 collapses on traces with a dense early
# phase ("the agent makes 30 quick edits, then takes a few longer
# pauses to think, all within one burst"), splitting them into many
# fragments that real reviewers see as one logical burst (the entry
# #6 regression evidence). Real-world dense traces almost always have
# at least a few mid-burst pauses; the default 35-step gap is tuned
# to absorb those. The adaptive layer is therefore "widen the default
# when the trace is genuinely sparse" rather than "tighten it when
# the trace is dense" — sparse traces (high median) get a wider gap,
# dense traces stay at the default. ``ADAPTIVE_GAP_MIN`` is kept as
# the absolute floor for completeness (callers who pass a tiny
# explicit ``--burst-gap`` are still respected; only the auto path
# floors at the default).
#
# With fewer than ``ADAPTIVE_GAP_MIN_EDITS`` candidate edits there is
# only one delta to draw from, but the adaptive value can still be
# better than the default for very sparse traces (3-edit micro
# sessions across 100s of steps). We accept the noise and still
# compute, falling back to DEFAULT only when there is literally no
# delta to read (n < 2).
ADAPTIVE_GAP_FACTOR = 4
ADAPTIVE_GAP_MIN = 5
ADAPTIVE_GAP_MAX = 100
ADAPTIVE_GAP_MIN_EDITS = 2

# Sentinel used for the public ``detect_bursts`` ``gap`` parameter to
# request the adaptive default. Any concrete int the caller passes
# overrides adaptive selection (the ``--burst-gap`` CLI path).
GAP_ADAPTIVE = -1

# Maximum bytes we surface for the commit body. Avoids surprise mega
# blobs in CLI output; subjects are unbounded but typically < 100 chars.
COMMIT_BODY_MAX_CHARS = 5_000

# Foreign-agent absolute prefixes seen in production traces. When a
# path starts with one of these, we strip the prefix to recover the
# repo-relative form so absolute-vs-relative variants of the same file
# dedupe in ``unique_files``. The list is intentionally conservative;
# additions here should correlate with concrete trace evidence.
FOREIGN_AGENT_PATH_PREFIXES: tuple[str, ...] = (
    "/Users/06506792/src/tries/2026-03-27-community-traces-hf/",
)

# Max gap between Edit count and Git diff hunk count before we
# reconcile down to the diff. Above this, Edit count wins (the Edit
# trail surfaces authoring intent the diff has lost — many tiny edits
# coalescing into one hunk).
_DIFF_RECONCILE_MAX_DELTA = 2

_BURST_INPUT_TYPES = {"file_edit", "patch_created"}

_TRIGGER_RE_FALLBACK = re.compile(r"")  # placeholder; real logic in core.intent


@dataclass
class Burst:
    """A single change burst.

    The ``patches`` and ``unique_files`` shapes are deliberately
    JSON-friendly: the ``--bursts`` CLI surfaces them verbatim through
    ``metadata`` on a virtual ``change_burst`` node so consumers can
    pull them with one ``jq`` expression.
    """

    step_range: list[int]
    intent_user_step: int | None
    intent_text: str | None
    unique_files: dict[str, int] = field(default_factory=dict)
    patches: list[dict[str, Any]] = field(default_factory=list)
    unique_git_anchors: list[str] = field(default_factory=list)
    has_git_anchor: bool = False
    # Cluster D5 / E I3: modal commit across patches, lifted to a
    # first-class burst field so the dataset row carries the burst's
    # commit identity without callers having to re-aggregate.
    burst_commit_sha: str | None = None
    # Internal: the step at which the burst's commit was observed.
    # Used to clip ``patches`` / ``unique_files`` to edits made *before*
    # the commit (the dataset-row contract) when the burst's step_range
    # spans subsequent commits as well. None when no commit is known.
    burst_commit_step: int | None = None
    # Cluster E: structured intent. Always present; fields default to
    # None / empty list when no chain can be derived.
    intent: dict[str, Any] = field(default_factory=lambda: {
        "trigger": None,
        "most_substantive_spec": None,
        "spec_chain": [],
        "burst_commit_sha": None,
        "commit_subject": None,
        "commit_body": None,
    })
    # Cluster F D11/D12: per-burst quality signals (commit message
    # quality + tool-call density + error/test correlations).
    quality_signals: dict[str, Any] = field(default_factory=dict)
    # Cluster H T6: tool-call density lifted to a top-level field for
    # easy CLI/jq access. Mirrors quality_signals.tool_call_density.
    # Defined as: ``tool_call_count / (step_range_span)`` where the
    # span is ``end - start + 1``. Higher means more iteration-heavy
    # work inside the burst window.
    tool_call_density: float | None = None
    # Cluster H T7: blast radius (file/test/src/docs counts and added
    # line totals). Pure aggregation over the burst's ``unique_files``
    # and ``patches`` — no new git ops.
    blast_radius: dict[str, Any] = field(default_factory=dict)
    # Cluster F: enriched patches with survival info from the project
    # event log. Empty when the trace has no matching trace_patch_created
    # events — at that point ``patches`` carries the synthesized
    # file_edit entries with no survival_state.
    patches_with_survival: list[dict[str, Any]] = field(default_factory=list)
    # Internal: the contributing node ids from the source trace map.
    contributing_node_ids: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        """Serialize to the JSON shape exposed in CLI output."""
        return {
            "step_range": list(self.step_range),
            "intent_user_step": self.intent_user_step,
            "intent_text": self.intent_text,
            "intent": dict(self.intent),
            "burst_commit_sha": self.burst_commit_sha,
            "unique_files": dict(self.unique_files),
            "patches": [dict(p) for p in self.patches],
            "patches_with_survival": [dict(p) for p in self.patches_with_survival],
            "quality_signals": dict(self.quality_signals),
            "tool_call_density": self.tool_call_density,
            "blast_radius": dict(self.blast_radius),
            "unique_git_anchors": list(self.unique_git_anchors),
            "has_git_anchor": self.has_git_anchor,
        }


def detect_bursts(
    source: TraceMap | list[TraceMapNode],
    *,
    gap: int | None = None,
    trace_record: Any | None = None,
    repo_path: Path | str | None = None,
    commit_lookup: bool = True,
    hard_split_on_user_pivot: bool = False,
) -> list[Burst]:
    """Cluster file_edit / patch_created nodes by step proximity.

    Parameters
    ----------
    source:
        Either a :class:`TraceMap` or a flat ``list[TraceMapNode]``. The
        list form is what the integration regression harness passes
        (``detect_bursts(trace_map.nodes)``); the TraceMap form is what
        the CLI uses.
    gap:
        Maximum allowable step-index distance between two consecutive
        nodes for them to share the same burst. ``gap=1`` means strict
        adjacency, ``gap=200`` collapses everything within a long
        session.

        When ``None`` (the new default in Cluster H), the gap is picked
        adaptively per-trace: ``max(ADAPTIVE_GAP_MIN, min(ADAPTIVE_GAP_MAX,
        median_edit_gap * ADAPTIVE_GAP_FACTOR))``. With fewer than
        :data:`ADAPTIVE_GAP_MIN_EDITS` candidate edits the heuristic
        falls back to :data:`DEFAULT_BURST_GAP`. Any concrete int the
        caller passes (e.g. via the CLI ``--burst-gap N`` flag)
        overrides the adaptive value.
    trace_record:
        Optional original ``TraceRecord`` (or its dict shape). When
        provided, used to mine the post-tool hook trail for the
        burst's first ``git commit`` and to surface the resulting SHA
        as ``burst.burst_commit_sha`` even when no ``patch_created``
        nodes carry ``commit_sha`` in their metadata.
    repo_path:
        Optional path to the working repo. Used as a target for
        ``git log`` lookups (commit subject + body). Defaults to the
        current working directory.
    commit_lookup:
        When False, skip ``git log`` lookups entirely. Useful for
        offline tests, hot CLI paths that don't need the prose, and
        the ``--no-commit-lookup`` CLI flag.
    hard_split_on_user_pivot:
        Cluster H T9. When True, a non-trigger ``user_instruction``
        node falling strictly between two adjacent edits forces a
        burst split — even if the step gap is below ``gap``. Triggers
        ("yes", "go ahead", "ship it") authorise in-flight work and
        do NOT split. Default False because in-the-wild traces often
        incorporate mid-burst redirections (the user clarifies but the
        work converges on one commit), and the labeled regression on
        entry #6 has 19 such redirections inside one labeled burst.
        Calibration corpus harnesses opt in to surface deliberately-
        diverging redirections.

    Returns
    -------
    Ordered list of :class:`Burst`. Order matches ``step_range[0]``
    ascending.
    """

    if isinstance(source, TraceMap):
        nodes = source.nodes
    else:
        nodes = list(source)

    if not nodes:
        return []

    candidates: list[TraceMapNode] = [
        node
        for node in nodes
        if node.action_type in _BURST_INPUT_TYPES and node.step_index is not None
    ]
    if not candidates:
        return []

    # Cluster H T2: pick the gap adaptively when the caller didn't
    # specify one. Explicit ``gap`` (anything other than ``None``)
    # always wins — that's the CLI ``--burst-gap N`` path. ``GAP_ADAPTIVE``
    # is also accepted as a sentinel asking for the adaptive default.
    if gap is None or gap == GAP_ADAPTIVE:
        gap = _compute_adaptive_gap(candidates)
    if gap < 1:
        gap = 1

    # Stable sort by step_index, then by node order to keep deterministic.
    node_order = {node.node_id: index for index, node in enumerate(nodes)}
    candidates.sort(
        key=lambda n: (n.step_index or 0, node_order.get(n.node_id, len(nodes)))
    )

    user_text_by_step: dict[int, str | None] = {
        node.step_index: node.text_preview
        for node in nodes
        if node.action_type == "user_instruction" and node.step_index is not None
    }
    user_instruction_nodes: list[TraceMapNode] = [
        node for node in nodes if node.action_type == "user_instruction"
    ]
    # The trace map's text_preview clamps to 160 chars per node — fine
    # for compact CLI display, but it loses substantive content for the
    # intent chain (entry #6 step 19 contains "how is that different
    # from pull" only after offset 300). When we have the source
    # TraceRecord, prefer the full step content.
    full_user_text_by_step = _full_user_text_by_step(trace_record)
    if full_user_text_by_step:
        for node in user_instruction_nodes:
            if node.step_index is None:
                continue
            full = full_user_text_by_step.get(node.step_index)
            if full:
                # Mutate-in-place is acceptable: these projection nodes
                # are owned by us once the burst pass takes over.
                node.text_preview = full

    repo_root_str = _resolve_repo_root_for_paths(trace_record, repo_path)
    repo_path_obj = _resolve_repo_path(trace_record, repo_path, repo_root_str)

    # Cluster H T9: hard-split steps. A non-trigger user_instruction
    # falling strictly between two adjacent edits is a discontinuity
    # — the user redirected — so we split the burst at that step
    # regardless of how small the gap is. Triggers ("yes", "go ahead",
    # "ship it") authorise in-flight work and do NOT split. Off by
    # default — see ``hard_split_on_user_pivot`` for rationale.
    hard_split_steps = (
        _compute_hard_split_steps(user_instruction_nodes)
        if hard_split_on_user_pivot
        else []
    )

    bursts: list[Burst] = []
    current: list[TraceMapNode] = []
    last_step: int | None = None

    def _flush() -> None:
        if not current:
            return
        bursts.append(_make_burst(current, user_text_by_step, repo_root_str))

    for node in candidates:
        step = node.step_index or 0
        should_split = False
        if last_step is not None:
            if (step - last_step) > gap:
                should_split = True
            else:
                # Cluster H T9: a non-trigger user_instruction strictly
                # between (last_step, step) splits the burst.
                for split_step in hard_split_steps:
                    if last_step < split_step < step:
                        should_split = True
                        break
        if should_split:
            _flush()
            current = []
        current.append(node)
        last_step = step
    _flush()

    # Cluster E: enrich each burst with the structured intent +
    # commit info. The walk is O(bursts × user_instructions); for
    # hundreds-of-thousands-of-step traces this is still cheap.
    if not bursts:
        return bursts

    commit_index = _build_commit_index(trace_record) if trace_record is not None else []

    # Cluster H T9 reconciliation: T9 splits a burst at every non-trigger
    # user instruction. But when several adjacent sub-bursts ultimately
    # land in the same Git commit, the user redirections were
    # incorporated rather than abandoned — the labeled-regression
    # contract treats that as a single logical burst (entry #6 has 19
    # mid-burst pivots that all converge on commit 68d6723db). We
    # collapse adjacent bursts sharing a ``burst_commit_sha`` here,
    # before the per-burst commit-clipping pass. Sub-bursts whose
    # commit lookup yields nothing remain split.
    for burst in bursts:
        _augment_burst_commit_sha(burst, commit_index)
    bursts = _merge_adjacent_bursts_sharing_commit(bursts)

    # Cluster F: load the project's TrailEvent log once and use it both
    # for survival enrichment and quality-signal correlation. Cheap to
    # do here even when no patches match — read_events handles missing
    # refs gracefully.
    trail_events: list[Any] | None = None
    trace_id_attr = getattr(trace_record, "trace_id", None)
    if trace_id_attr is None and isinstance(trace_record, dict):
        trace_id_attr = trace_record.get("trace_id")
    if (
        commit_lookup
        and repo_path_obj is not None
        and trace_id_attr
    ):
        try:
            from .trails import read_events as _read_events
            trail_events = _read_events(repo_path_obj)
        except Exception:
            trail_events = None

    for burst in bursts:
        _augment_intent_chain(burst, user_instruction_nodes)
        # ``burst.burst_commit_sha`` was already populated in the
        # pre-merge pass above; no need to recompute here.
        # Re-filter unique_files / patches to the burst's commit when
        # we know it AND we can run a git lookup. This collapses the
        # 18-files-from-multiple-commits case (the entry #6 burst spans
        # ~8 commits but the burst's *own* commit only touched 9 files)
        # back into the single-commit slice the dataset row should
        # represent.
        if (
            burst.burst_commit_sha
            and commit_lookup
            and repo_path_obj is not None
        ):
            commit_files = _git_show_files(repo_path_obj, burst.burst_commit_sha)
            if commit_files:
                _filter_burst_to_commit_files(burst, commit_files)
                # Collapse consecutive Edits whose later old_string is
                # a substring of an earlier new_string — that's how
                # Git represents them as a single hunk in the diff.
                _merge_overlapping_consecutive_edits(burst, trace_record)
                # Final corrective layer: when the diff itself shows
                # fewer hunks than our merged Edit count for a file
                # (Git's hunk consolidation is more aggressive than
                # text-overlap can detect), trust the diff. The Edit
                # count remains an upper bound on what landed.
                _reconcile_unique_files_with_git_diff(
                    burst, repo_path_obj, burst.burst_commit_sha
                )
        if commit_lookup and burst.burst_commit_sha:
            subject, body, error = _git_log_subject_body(
                repo_path_obj or Path.cwd(),
                burst.burst_commit_sha,
            )
            burst.intent["commit_subject"] = subject
            burst.intent["commit_body"] = body
            if error:
                burst.intent["commit_lookup_error"] = error
        burst.intent["burst_commit_sha"] = burst.burst_commit_sha

        # Legacy aliases: keep ``intent_text`` / ``intent_user_step``
        # pointing at the spec (preferred) or the trigger (fallback).
        spec = burst.intent.get("most_substantive_spec")
        trig = burst.intent.get("trigger")
        if spec is not None:
            burst.intent_user_step = spec.get("step")
            burst.intent_text = spec.get("text")
        elif trig is not None:
            burst.intent_user_step = trig.get("step")
            burst.intent_text = trig.get("text")

        # Cluster F D11: commit message quality tier.
        _augment_commit_message_quality(burst)

        # Cluster F: survival enrichment — when the project's event log
        # carries trace_patch_created events for this burst, sync each
        # patch and emit ``patches_with_survival``.
        if trail_events is not None and trace_id_attr and repo_path_obj is not None:
            _augment_patches_with_survival(
                burst,
                events=trail_events,
                trace_id=str(trace_id_attr),
                repo=repo_path_obj,
            )

    # Cluster F D12: per-burst error / tool-call density signals.
    if nodes:
        for burst in bursts:
            _augment_quality_signals(burst, nodes=nodes)

    # Cluster H T6 + T7: lift tool_call_density to a top-level field
    # and stamp blast_radius from the burst's file/patch aggregates.
    edit_payloads = _edits_payload_by_step(trace_record) if trace_record is not None else {}
    for burst in bursts:
        _lift_tool_call_density(burst)
        _augment_blast_radius(burst, edit_payloads=edit_payloads)

    return bursts


def bursts_to_trace_map(
    source: TraceMap,
    bursts: list[Burst],
) -> TraceMap:
    """Project a list of :class:`Burst` into a TraceMap of ``change_burst`` nodes.

    Edges are emitted as ``previous_next`` between consecutive bursts so
    consumers can traverse them in step order.
    """

    nodes: list[TraceMapNode] = []
    edges: list[TraceMapEdge] = []
    previous: TraceMapNode | None = None
    trace_id = source.trace_id

    for ordinal, burst in enumerate(bursts, 1):
        unique_files_sum = sum(burst.unique_files.values()) or 0
        node = TraceMapNode(
            node_id=f"tmn:{trace_id}:burst:{ordinal}",
            trace_id=trace_id,
            unit_id=f"tu:{trace_id}:burst:{ordinal}",
            action_type="change_burst",
            step_index=burst.step_range[0] if burst.step_range else None,
            start_step_index=burst.step_range[0] if burst.step_range else None,
            end_step_index=burst.step_range[1] if burst.step_range else None,
            previous_node_id=previous.node_id if previous else None,
            files_modified=sorted(burst.unique_files.keys()),
            anchor_refs=list(burst.unique_git_anchors),
            text_preview=burst.intent_text,
            metadata=burst.to_metadata() | {
                "edit_count": unique_files_sum,
                "contributing_node_ids": list(burst.contributing_node_ids),
            },
            active_user_step=burst.intent_user_step,
        )
        if previous:
            previous.next_node_id = node.node_id
            edges.append(
                TraceMapEdge(
                    edge_id=f"tme:{trace_id}:burst:{len(edges) + 1}",
                    trace_id=trace_id,
                    source_node_id=previous.node_id,
                    target_node_id=node.node_id,
                    edge_type="previous_next",
                )
            )
        nodes.append(node)
        previous = node

    return TraceMap(
        trace_id=trace_id,
        root_node_ids=[nodes[0].node_id] if nodes else [],
        nodes=nodes,
        edges=edges,
        limitations=source.limitations,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_burst(
    nodes: list[TraceMapNode],
    user_text_by_step: dict[int, str | None],
    repo_root: str | None,
) -> Burst:
    steps = [n.step_index for n in nodes if n.step_index is not None]
    step_range = [min(steps), max(steps)] if steps else [0, 0]

    # Legacy intent: the active_user_step of the first node in the burst.
    first = nodes[0]
    intent_step = first.active_user_step
    intent_text: str | None = None
    if intent_step is not None:
        text = user_text_by_step.get(intent_step)
        if text:
            intent_text = text[:300]

    unique_files: dict[str, int] = {}
    patches: list[dict[str, Any]] = []
    unique_git_anchors: list[str] = []
    seen_anchors: set[str] = set()

    has_explicit_patch_created = False

    for node in nodes:
        for path in node.files_modified:
            normalized = _normalise_path(path, repo_root)
            unique_files[normalized] = unique_files.get(normalized, 0) + 1
        if node.action_type == "patch_created":
            has_explicit_patch_created = True
            meta = node.metadata or {}
            patch_id = meta.get("trace_patch_id") or meta.get("patch_id")
            anchor_id = meta.get("git_anchor_id")
            patches.append(
                {
                    "patch_id": patch_id,
                    "git_anchor_id": anchor_id,
                    "commit_sha": meta.get("commit_sha"),
                    "evidence_firmness": meta.get("evidence_firmness"),
                    "evidence_tier": meta.get("evidence_tier"),
                    "file_path": (
                        _normalise_path(node.files_modified[0], repo_root)
                        if node.files_modified
                        else None
                    ),
                    "step_index": node.step_index,
                }
            )
            if isinstance(anchor_id, str) and anchor_id not in seen_anchors:
                seen_anchors.add(anchor_id)
                unique_git_anchors.append(anchor_id)

    # Fallback: when the trace map has no patch_created nodes (the
    # integration test path), synthesize one patch entry per file_edit
    # so downstream consumers still see a populated patches list. We
    # deliberately omit ``evidence_firmness`` / ``evidence_tier`` here
    # — those are anchor-survival fields populated by the ingest's
    # trail projection (Cluster F territory). Adding them as None
    # would falsely signal that the survival-state machinery has run.
    if not has_explicit_patch_created:
        for index, node in enumerate(nodes):
            if node.action_type != "file_edit":
                continue
            file_path = (
                _normalise_path(node.files_modified[0], repo_root)
                if node.files_modified
                else None
            )
            patches.append(
                {
                    "patch_id": f"file_edit:{node.node_id}",
                    "git_anchor_id": None,
                    "commit_sha": None,
                    "file_path": file_path,
                    "step_index": node.step_index,
                }
            )

    return Burst(
        step_range=step_range,
        intent_user_step=intent_step,
        intent_text=intent_text,
        unique_files=unique_files,
        patches=patches,
        unique_git_anchors=unique_git_anchors,
        has_git_anchor=bool(unique_git_anchors),
        contributing_node_ids=[n.node_id for n in nodes],
    )


def _normalise_path(path: str, repo_root: str | None) -> str:
    """Strip foreign-agent + repo-root prefixes so abs/rel forms collapse.

    The Cluster D6 contract: ``unique_files`` holds repo-relative
    paths only. Two distinct surface forms (``/Users/06506792/.../foo.py``
    and ``foo.py``) of the same file MUST collapse onto a single
    ``unique_files`` entry. Foreign-agent prefixes are baked in
    (see :data:`FOREIGN_AGENT_PATH_PREFIXES`) so a contributor working
    in a different worktree path doesn't pollute the row.
    """
    if not isinstance(path, str) or not path:
        return path
    for prefix in FOREIGN_AGENT_PATH_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix):]
    if repo_root:
        normalised_root = repo_root.rstrip(os.sep).rstrip("/")
        prefix = normalised_root + "/"
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _resolve_repo_root_for_paths(
    trace_record: Any | None,
    repo_path: Path | str | None,
) -> str | None:
    """Best-effort repo root for ``unique_files`` path normalization."""
    if repo_path is not None:
        return str(repo_path).rstrip(os.sep).rstrip("/")
    if trace_record is None:
        return None
    metadata = _record_metadata(trace_record)
    cwd = metadata.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd.rstrip(os.sep).rstrip("/")
    hook = metadata.get("hook_pre_tool_use")
    if isinstance(hook, dict):
        for entry in hook.values():
            if not isinstance(entry, dict):
                continue
            trail = entry.get("trail") or {}
            wt = trail.get("worktree_root")
            if isinstance(wt, str) and wt:
                return wt.rstrip(os.sep).rstrip("/")
    return None


def _resolve_repo_path(
    trace_record: Any | None,
    repo_path: Path | str | None,
    repo_root_str: str | None,
) -> Path | None:
    """Pick a concrete on-disk path for git lookups."""
    if repo_path is not None:
        candidate = Path(repo_path)
        if candidate.exists():
            return candidate
        return None
    if repo_root_str:
        candidate = Path(repo_root_str)
        if candidate.exists():
            return candidate
    return None


def _full_user_text_by_step(trace_record: Any) -> dict[int, str]:
    """Extract full user-message text keyed by step_index.

    The trace map's text_preview is truncated; for intent-chain
    purposes we want the unabridged content so substantive specs
    like step 19 ("how is that different from pull") survive past the
    160-char preview window.
    """
    if trace_record is None:
        return {}
    out: dict[int, str] = {}
    for step in _record_steps(trace_record):
        role = _step_attr(step, "role")
        if role != "user":
            continue
        step_index = _step_attr(step, "step_index")
        content = _step_attr(step, "content") or ""
        if step_index is None or not content:
            continue
        out[int(step_index)] = content
    return out


def _record_metadata(trace_record: Any) -> dict[str, Any]:
    if trace_record is None:
        return {}
    if isinstance(trace_record, dict):
        meta = trace_record.get("metadata") or {}
        return meta if isinstance(meta, dict) else {}
    meta = getattr(trace_record, "metadata", None) or {}
    if isinstance(meta, dict):
        return meta
    return {}


def _record_steps(trace_record: Any) -> list[Any]:
    if trace_record is None:
        return []
    if isinstance(trace_record, dict):
        steps = trace_record.get("steps") or []
        return list(steps)
    steps = getattr(trace_record, "steps", []) or []
    return list(steps)


def _build_commit_index(trace_record: Any) -> list[tuple[int, str]]:
    """Return ``[(step_index, commit_sha), ...]`` for commits seen in the trace.

    A commit is detected when the post-tool hook trail's ``git_head``
    changes from the pre-hook trail's ``git_head``. We anchor the
    commit at the step where the change was observed. The returned
    list is sorted ascending by step_index so callers can pick the
    first commit inside a given burst.
    """
    if trace_record is None:
        return []
    metadata = _record_metadata(trace_record)
    pre_hook = metadata.get("hook_pre_tool_use") or {}
    post_hook = metadata.get("hook_post_tool_use") or {}
    if not isinstance(pre_hook, dict) and not isinstance(post_hook, dict):
        return []

    transitions: list[tuple[int, str]] = []
    last_seen: str | None = None
    for step in _record_steps(trace_record):
        step_index = _step_attr(step, "step_index")
        if step_index is None:
            continue
        for tc in _step_tool_calls(step):
            tcid = _tool_call_id(tc)
            if not tcid:
                continue
            pre_sha = _trail_git_head(pre_hook.get(tcid)) if isinstance(pre_hook, dict) else None
            post_sha = _trail_git_head(post_hook.get(tcid)) if isinstance(post_hook, dict) else None
            for sha in (pre_sha, post_sha):
                if sha and sha != last_seen:
                    transitions.append((int(step_index), sha))
                    last_seen = sha
    return transitions


def _trail_git_head(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    trail = entry.get("trail") or {}
    if not isinstance(trail, dict):
        return None
    head = trail.get("git_head") or {}
    if isinstance(head, dict):
        return head.get("hex")
    return None


def _step_attr(step: Any, attr: str) -> Any:
    if isinstance(step, dict):
        return step.get(attr)
    return getattr(step, attr, None)


def _step_tool_calls(step: Any) -> list[Any]:
    if isinstance(step, dict):
        return list(step.get("tool_calls") or [])
    return list(getattr(step, "tool_calls", []) or [])


def _tool_call_id(tc: Any) -> str | None:
    if isinstance(tc, dict):
        return tc.get("tool_call_id")
    return getattr(tc, "tool_call_id", None)


def _augment_intent_chain(
    burst: Burst,
    user_instruction_nodes: list[TraceMapNode],
) -> None:
    """Compute the structured ``intent`` for ``burst`` and merge it in.

    The chain walks from the start of the trace up to the burst's first
    step. We import lazily to avoid a hard cycle if ``core.intent`` ever
    grows a back-edge to ``core.bursts`` (it doesn't today, but the
    intent surface is supposed to be reusable elsewhere).
    """
    from . import intent as intent_module  # lazy to keep circular-import surface zero

    burst_start = burst.step_range[0] if burst.step_range else 0
    chain = intent_module.derive_intent_chain(user_instruction_nodes, burst_start)
    # Merge: keep commit-related fields populated by I3.
    burst.intent["trigger"] = chain.get("trigger")
    burst.intent["most_substantive_spec"] = chain.get("most_substantive_spec")
    burst.intent["spec_chain"] = list(chain.get("spec_chain") or [])


def _augment_burst_commit_sha(
    burst: Burst,
    commit_index: list[tuple[int, str]],
) -> None:
    """Populate ``burst.burst_commit_sha`` (D5).

    Tier 1: modal commit_sha across patches' metadata. This is the
    canonical answer when the trace map has gone through the trace
    index (production path) and patches carry commit_sha.

    Tier 2: when no patch carries commit_sha (integration-test path),
    fall back to the first commit observed in the burst's step range
    via the trace's hook trail.
    """
    counter: Counter[str] = Counter()
    for patch in burst.patches:
        sha = patch.get("commit_sha")
        if isinstance(sha, str) and sha:
            counter[sha] += 1
    if counter:
        burst.burst_commit_sha = counter.most_common(1)[0][0]
        # Modal-from-patches path: pick the latest step among patches
        # carrying that SHA so the clipping window is correct.
        modal_sha = burst.burst_commit_sha
        steps = [
            patch.get("step_index")
            for patch in burst.patches
            if patch.get("commit_sha") == modal_sha
            and isinstance(patch.get("step_index"), int)
        ]
        if steps:
            burst.burst_commit_step = max(steps)
        return

    if not commit_index or not burst.step_range:
        return
    start, end = burst.step_range[0], burst.step_range[-1]
    for step_index, sha in commit_index:
        if start <= step_index <= end:
            burst.burst_commit_sha = sha
            burst.burst_commit_step = step_index
            return


def _reconcile_unique_files_with_git_diff(
    burst: Burst,
    repo_path: Path,
    sha: str,
) -> None:
    """Cap each ``unique_files[path]`` count by the hunk count in ``git show``.

    Edit-call counts are an upper bound on the per-file hunk count
    because Git's diff machinery merges adjacent or overlapping edits
    into a single hunk more aggressively than any text-overlap check
    can detect (it knows the full pre/post tree). When the diff
    reports a *modestly* smaller count than the Edit count we trust
    the diff (Git just consolidated two close-together edits). When
    the Edit count is *much* higher than the diff count we keep the
    Edit count: the agent did a lot of localised edits that landed in
    a small number of diff hunks because they were near-duplicates of
    each other (e.g. iterative type fixes) — that flow is best
    described as "many distinct authoring steps" rather than "one
    diff hunk".

    The threshold is intentionally conservative: only collapse when
    the gap is at most :data:`_DIFF_RECONCILE_MAX_DELTA` (default 2).
    Bigger gaps stay as the Edit count.
    """
    diff_counts = _git_show_hunks_per_file(repo_path, sha)
    if not diff_counts:
        return

    # Build per-file ordered patch lists.
    by_file: dict[str, list[dict[str, Any]]] = {}
    for patch in burst.patches:
        fp = patch.get("file_path")
        if isinstance(fp, str):
            by_file.setdefault(fp, []).append(patch)
    for fp in by_file:
        by_file[fp].sort(key=lambda p: p.get("step_index") or 0)

    drop_ids: set[int] = set()
    for fp, edit_count in list(burst.unique_files.items()):
        diff_count = diff_counts.get(fp)
        if diff_count is None or diff_count >= edit_count:
            continue
        if edit_count - diff_count > _DIFF_RECONCILE_MAX_DELTA:
            # Edit count is materially higher than diff count — the
            # authoring trail tells us more than the resulting diff,
            # keep the Edit count.
            continue
        # Trim: keep the latest ``diff_count`` edits, drop the older
        # ones (the assumption being that earlier edits got subsumed
        # into later ones by Git's hunk merger).
        patches = by_file.get(fp, [])
        excess = len(patches) - diff_count
        for i in range(excess):
            drop_ids.add(id(patches[i]))
        burst.unique_files[fp] = diff_count

    if drop_ids:
        burst.patches = [p for p in burst.patches if id(p) not in drop_ids]


def _git_show_hunks_per_file(repo_path: Path, sha: str) -> dict[str, int]:
    """Run ``git show --no-color`` and count diff hunks per file.

    Used as a corrective layer when filtering burst patches to a
    commit's file set: the count of ``Edit`` calls is an *upper bound*
    on the number of textual hunks in the commit (consecutive edits
    to the same file with overlapping content collapse into one hunk
    in the diff). Where the diff has fewer hunks than the Edit count,
    we trust the diff — that's the canonical hunk count downstream
    consumers reason about.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "show", "--no-color", "--unified=3", sha],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return {}
    if proc.returncode != 0:
        return {}
    counts: dict[str, int] = {}
    current_file: str | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/"):]
            continue
        if line.startswith("+++ ") and "/" in line:
            current_file = line.split("/", 1)[1]
            continue
        if line.startswith("@@") and current_file:
            counts[current_file] = counts.get(current_file, 0) + 1
    return counts


def _merge_overlapping_consecutive_edits(
    burst: Burst,
    trace_record: Any,
) -> None:
    """Collapse consecutive Edits whose ``old_string`` is a substring of
    a recent ``new_string`` to the same file.

    When the agent edits a file twice in quick succession and the
    second edit modifies content that was just inserted by the first,
    Git renders that as one diff hunk (the second edit happens inside
    the first's new region). The Cluster D6 contract for
    ``unique_files`` is "per-file *hunk* counts", not "per-file Edit
    counts" — so we collapse these overlapping pairs back into a
    single patch entry.
    """
    if trace_record is None or not burst.patches:
        return

    edit_payloads = _edits_payload_by_step(trace_record)
    if not edit_payloads:
        return

    by_file: dict[str, list[dict[str, Any]]] = {}
    for patch in burst.patches:
        fp = patch.get("file_path")
        if isinstance(fp, str):
            by_file.setdefault(fp, []).append(patch)

    drop_ids: set[int] = set()
    for fp, patches in by_file.items():
        patches.sort(key=lambda p: p.get("step_index") or 0)
        for i in range(len(patches) - 1):
            later = patches[i + 1]
            later_step = later.get("step_index")
            if later_step is None:
                continue
            later_payload = edit_payloads.get((fp, later_step))
            if not later_payload:
                continue
            later_old = later_payload.get("old_string") or ""
            if not later_old:
                continue
            # Walk back: does any prior patch's ``new_string`` contain
            # ``later_old`` as a substring? If so, merge them.
            for j in range(i, -1, -1):
                if id(patches[j]) in drop_ids:
                    continue
                prior = patches[j]
                prior_step = prior.get("step_index")
                if prior_step is None:
                    continue
                prior_payload = edit_payloads.get((fp, prior_step))
                if not prior_payload:
                    continue
                prior_new = prior_payload.get("new_string") or ""
                if prior_new and later_old in prior_new:
                    drop_ids.add(id(later))
                    break
    if not drop_ids:
        return

    burst.patches = [p for p in burst.patches if id(p) not in drop_ids]
    new_files: dict[str, int] = {}
    for patch in burst.patches:
        fp = patch.get("file_path")
        if isinstance(fp, str):
            new_files[fp] = new_files.get(fp, 0) + 1
    burst.unique_files = new_files


def _edits_payload_by_step(trace_record: Any) -> dict[tuple[str, int], dict[str, Any]]:
    """Index ``Edit``/``Write`` tool inputs by ``(file_path_relative, step_index)``."""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for step in _record_steps(trace_record):
        step_index = _step_attr(step, "step_index")
        if step_index is None:
            continue
        for tc in _step_tool_calls(step):
            tool_name = (
                tc.get("tool_name") if isinstance(tc, dict) else getattr(tc, "tool_name", None)
            )
            if tool_name not in ("Edit", "Write", "MultiEdit"):
                continue
            tc_input = (
                tc.get("input") if isinstance(tc, dict) else getattr(tc, "input", None)
            ) or {}
            fp = tc_input.get("file_path") or tc_input.get("path")
            if not isinstance(fp, str):
                continue
            normalised = _normalise_path(fp, None)
            out[(normalised, int(step_index))] = tc_input
    return out


def _filter_burst_to_commit_files(
    burst: Burst,
    commit_files: set[str],
) -> None:
    """Restrict ``unique_files`` and ``patches`` to the burst's commit.

    The burst's step_range often spans multiple commits (the agent
    landed several refactors in one continuous editing session). For
    dataset-row purposes the burst should represent *a single
    commit's* work — its file set is exactly that commit's modified
    paths, and its patches are exactly the edits that landed *in or
    before* the commit step.

    We keep ``patches`` whose ``file_path`` is in ``commit_files``
    AND whose ``step_index`` is at or before ``burst.burst_commit_step``
    (when known). We then rebuild ``unique_files`` as a per-file count
    of the kept patches.
    """
    kept_patches: list[dict[str, Any]] = []
    new_files: dict[str, int] = {}
    for patch in burst.patches:
        fp = patch.get("file_path")
        if not isinstance(fp, str):
            continue
        if fp not in commit_files:
            continue
        if burst.burst_commit_step is not None:
            patch_step = patch.get("step_index")
            if (
                isinstance(patch_step, int)
                and patch_step > burst.burst_commit_step
            ):
                continue
        kept_patches.append(patch)
        new_files[fp] = new_files.get(fp, 0) + 1
    if kept_patches:
        burst.patches = kept_patches
        burst.unique_files = new_files


def _git_show_files(repo_path: Path, sha: str) -> set[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "show", "--name-only", "--format=", sha],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return set()
    if proc.returncode != 0:
        return set()
    return {
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip()
    }


def _git_log_subject_body(
    repo_path: Path, sha: str
) -> tuple[str | None, str | None, str | None]:
    """Return ``(subject, body, error)`` for ``sha``.

    Defensively absorbs subprocess failures: missing ``git``, missing
    repo, unknown SHA, timeout — all surface as a non-None ``error``
    string so the caller can stamp ``intent.commit_lookup_error`` and
    move on.
    """
    try:
        subj_proc = subprocess.run(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%s", sha],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
        return None, None, f"subject_lookup_failed: {exc!s}"
    if subj_proc.returncode != 0:
        err = (subj_proc.stderr or "").strip().splitlines()[-1] if subj_proc.stderr else "unknown"
        return None, None, f"subject_lookup_failed: {err}"
    subject = subj_proc.stdout.strip() or None

    try:
        body_proc = subprocess.run(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%b", sha],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
        return subject, None, f"body_lookup_failed: {exc!s}"
    if body_proc.returncode != 0:
        err = (body_proc.stderr or "").strip().splitlines()[-1] if body_proc.stderr else "unknown"
        return subject, None, f"body_lookup_failed: {err}"
    body = body_proc.stdout
    if body:
        body = body[:COMMIT_BODY_MAX_CHARS]
    else:
        body = None
    return subject, body, None


# ---------------------------------------------------------------------------
# Cluster F — survival enrichment + quality signals
# ---------------------------------------------------------------------------

_CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|refactor|chore|test|docs|perf|style|build|ci|revert)"
    r"(\(.+\))?:\s"
)


def _commit_message_quality(subject: str | None, body: str | None) -> dict[str, Any]:
    """Cluster F D11: classify a commit message into a quality tier.

    Tiers (from the spec):

    * ``bare`` — subject only, no body
    * ``terse`` — subject + 1 body paragraph ≤ 140 chars
    * ``descriptive`` — subject + body 140-500 chars
    * ``detailed`` — subject + body > 500 chars OR ≥ 2 paragraphs
    """
    subj = subject or ""
    bod = body or ""
    subject_length = len(subj)
    body_length = len(bod)
    has_conv = bool(_CONVENTIONAL_COMMIT_RE.match(subj))
    paragraphs = [p for p in re.split(r"\n\s*\n", bod) if p.strip()] if bod else []
    paragraph_count = len(paragraphs)

    if not bod:
        tier = "bare"
    elif body_length > 500 or paragraph_count >= 2:
        tier = "detailed"
    elif body_length >= 140:
        tier = "descriptive"
    else:
        tier = "terse"

    return {
        "tier": tier,
        "subject_length": subject_length,
        "body_length": body_length,
        "has_conventional_prefix": has_conv,
        "paragraph_count": paragraph_count,
    }


def _augment_commit_message_quality(burst: Burst) -> None:
    """Stamp ``intent.commit_message_quality`` based on subject/body."""
    quality = _commit_message_quality(
        burst.intent.get("commit_subject"),
        burst.intent.get("commit_body"),
    )
    burst.intent["commit_message_quality"] = quality


def _augment_quality_signals(
    burst: Burst,
    *,
    nodes: list[TraceMapNode],
) -> None:
    """Cluster F D12: count error / test / tool-call density inside the
    burst's step_range. Reads from the same TraceMap nodes already in
    scope so no extra trace traversal is paid.
    """
    if not burst.step_range or len(burst.step_range) != 2:
        return
    start, end = burst.step_range
    span = max(end - start + 1, 1)

    error_count = 0
    test_run_count = 0
    tool_call_count = 0
    for node in nodes:
        si = node.step_index
        if si is None or si < start or si > end:
            continue
        atype = node.action_type
        if atype == "error_signal":
            error_count += 1
        elif atype == "test_run":
            test_run_count += 1
        elif atype == "tool_call":
            tool_call_count += 1

    burst.quality_signals = {
        "error_signal_count": error_count,
        "test_run_count": test_run_count,
        "tool_call_count": tool_call_count,
        "tool_call_density": round(tool_call_count / span, 4) if span else 0.0,
    }


def _augment_patches_with_survival(
    burst: Burst,
    *,
    events: list[Any],
    trace_id: str,
    repo: Path,
) -> None:
    """Cluster F: enrich ``burst.patches_with_survival`` by syncing each
    Trace Patch belonging to ``trace_id`` whose step_index falls inside
    ``burst.step_range`` against the live repo.

    The list mirrors ``burst.patches`` shape (carrying ``patch_id``,
    ``file_path``, ``step_index``) but adds ``survival_state``,
    ``retention_fraction``, ``retention_fraction_at_anchor``,
    ``retention_fraction_at_original_range``, ``evidence_firmness``,
    ``evidence_tier``, ``lost_kind``, ``lost_at_commit_sha``, and
    ``commit_sha`` (anchor commit). Patches not in the burst's range
    are skipped.

    Uses one shared ``lost_attribution_cache`` and a single events list
    so a 27-patch burst pays one ``read_events`` and at most a few
    ``git log --diff-filter=D`` calls (cached by file).
    """
    from .trails import sync_patch as _sync_patch

    if not burst.step_range or len(burst.step_range) != 2:
        return
    start, end = burst.step_range

    # Gather the patches that belong to this trace AND fall inside the
    # burst's step window. Index by (file_path, step_index) so we can
    # join back to the synthetic burst.patches entries later if needed.
    candidate_patches: list[tuple[str, dict[str, Any]]] = []
    for event in events:
        if event.event_type != "trace_patch_created":
            continue
        if event.trace_id != trace_id:
            continue
        si = event.step_index
        if si is None or si < start or si > end:
            continue
        patch_id = event.payload.get("trace_patch_id")
        if not isinstance(patch_id, str) or not patch_id:
            continue
        candidate_patches.append((patch_id, event.payload))

    if not candidate_patches:
        return

    # Filter to the burst's commit's file set if known: when the burst
    # is anchored to a specific commit and ``unique_files`` was clipped,
    # the patches we want are exactly those whose file_path matches a
    # tracked file. This mirrors the dataset-row contract.
    tracked_files: set[str] = set()
    for path in burst.unique_files.keys():
        tracked_files.add(path)
        # also accept normalized-stripped form
        if path.startswith("/"):
            tracked_files.add(path.split("/")[-1])

    lost_cache: dict[tuple[str, str, str], tuple[str | None, str]] = {}
    enriched: list[dict[str, Any]] = []
    for patch_id, payload in candidate_patches:
        # When the burst has been clipped to specific files, restrict
        # the survival join to those. When unique_files is empty we
        # accept everything in the trace+range window.
        file_path = payload.get("file_path")
        if tracked_files and file_path and file_path not in tracked_files:
            # Try the normalized-relative form (foreign agent prefix /
            # repo_root prefix were already stripped from unique_files).
            normalized = file_path
            for prefix in FOREIGN_AGENT_PATH_PREFIXES:
                if normalized.startswith(prefix):
                    normalized = normalized[len(prefix):]
                    break
            if normalized not in tracked_files:
                continue
            file_path = normalized
        try:
            sync_payload = _sync_patch(
                repo,
                patch_id,
                events=events,
                lost_attribution_cache=lost_cache,
            )
        except Exception:
            continue
        current = sync_payload.get("current_survival") or {}
        row = {
            "patch_id": patch_id,
            "trace_patch_id": patch_id,
            "file_path": file_path,
            "step_index": payload.get("step_index"),
            "survival_state": current.get("survival_state"),
            "retention_fraction": current.get("retention_fraction"),
            "retention_fraction_at_anchor": current.get(
                "retention_fraction_at_anchor"
            ),
            "retention_fraction_at_original_range": current.get(
                "retention_fraction_at_original_range"
            ),
            "evidence_firmness": current.get("evidence_firmness"),
            "evidence_tier": current.get("evidence_tier"),
            "lost_kind": current.get("lost_kind"),
            "lost_at_commit_sha": current.get("lost_at_commit_sha"),
            "commit_sha": (current.get("anchor_commit_id") or {}).get("hex"),
            "current_path": current.get("current_path"),
        }
        enriched.append(row)

    if enriched:
        burst.patches_with_survival = enriched


# ---------------------------------------------------------------------------
# Cluster H — adaptive gap, hard-split, blast radius
# ---------------------------------------------------------------------------


# Test/src/docs detection regexes for blast_radius classification.
# We anchor leading paths (``^tests/``, ``^src/``) too so traces whose
# repo-relative paths don't start with a slash still classify
# correctly. The spec allows additional matches beyond the literal
# patterns it documents.
_TEST_FILE_RE = re.compile(r"(?:^|/)tests?/|/test_|^test_|_test\.")
_SRC_FILE_RE = re.compile(r"(?:^|/)src/")
_DOCS_FILE_RE = re.compile(r"\.(md|rst|txt)$", re.IGNORECASE)


def _compute_adaptive_gap(nodes: list[TraceMapNode]) -> int:
    """Compute the per-trace burst gap from edit-node step distances.

    The heuristic: take the median step-distance between consecutive
    burst-input nodes (file_edit / patch_created), multiply by
    :data:`ADAPTIVE_GAP_FACTOR`, and clamp to ``[DEFAULT_BURST_GAP,
    ADAPTIVE_GAP_MAX]`` — i.e. floor at the default. Below
    :data:`ADAPTIVE_GAP_MIN_EDITS` candidate edits we cannot compute
    a delta and fall back to :data:`DEFAULT_BURST_GAP`.

    Why floor at the default: dense traces with median 1-2 deltas
    routinely have a few mid-burst pauses (3-step tool-call sequences,
    a short status check, a brief diff inspection) that show up as
    deltas of 20-30. The default 35-step gap is tuned to absorb
    those. Without the floor, ``median * 4 = 8`` would split such
    traces into many fragments that reviewers consider one logical
    burst (the regression evidence on entry #6). Sparse traces with a
    higher median legitimately need a wider gap to coalesce, and the
    floor lets them widen without affecting the dense baseline.

    The input ``nodes`` list is expected to already be filtered to
    burst-input action types and have non-None ``step_index``.
    """
    steps = sorted(int(n.step_index) for n in nodes if n.step_index is not None)
    if len(steps) < ADAPTIVE_GAP_MIN_EDITS:
        return DEFAULT_BURST_GAP

    deltas = [b - a for a, b in zip(steps, steps[1:]) if b - a > 0]
    if not deltas:
        return DEFAULT_BURST_GAP

    deltas.sort()
    mid = len(deltas) // 2
    if len(deltas) % 2 == 1:
        median = deltas[mid]
    else:
        median = (deltas[mid - 1] + deltas[mid]) / 2

    raw = int(round(median * ADAPTIVE_GAP_FACTOR))
    # Floor at DEFAULT_BURST_GAP so dense traces don't fragment; cap
    # at ADAPTIVE_GAP_MAX so a runaway sparse trace doesn't fuse the
    # whole session into one giant burst. ADAPTIVE_GAP_MIN is the
    # absolute floor (used when callers explicitly request a tiny
    # gap; the auto path uses DEFAULT_BURST_GAP).
    floor = DEFAULT_BURST_GAP
    if raw < floor:
        return floor
    if raw > ADAPTIVE_GAP_MAX:
        return ADAPTIVE_GAP_MAX
    return raw


def _compute_hard_split_steps(
    user_instruction_nodes: list[TraceMapNode],
) -> list[int]:
    """Return the sorted list of step indexes where a non-trigger user
    instruction sits. These are the boundaries we hard-split on (T9).

    Triggers are filtered out via :func:`core.intent.is_trigger` — they
    authorise in-flight work and never split. Image-placeholder-only
    user instructions (no substantive text) are also filtered out.
    """
    from . import intent as intent_module  # lazy: avoid import cycles

    out: list[int] = []
    for node in user_instruction_nodes:
        if node.step_index is None:
            continue
        text = (node.text_preview or "").strip()
        if not text:
            continue
        # Strip image placeholder prefixes the same way the intent
        # module does. This ensures "[Image #1] please refactor"
        # is treated as substantive (the spec content "please
        # refactor" past the placeholder is a real spec).
        try:
            stripped = intent_module._strip_image_placeholders(text).strip()
        except AttributeError:  # pragma: no cover — defensive
            stripped = text
        if not stripped:
            continue
        if intent_module.is_trigger(text):
            continue
        out.append(int(node.step_index))
    out.sort()
    return out


def _lift_tool_call_density(burst: Burst) -> None:
    """Cluster H T6: lift ``quality_signals.tool_call_density`` to the
    burst's top-level ``tool_call_density`` field.

    Cluster F already computes the metric inside ``quality_signals``;
    this just promotes it to a first-class attribute so jq consumers
    can read ``.tool_call_density`` directly. Pure aggregation, no new
    compute.
    """
    qs = burst.quality_signals or {}
    value = qs.get("tool_call_density")
    if isinstance(value, (int, float)):
        burst.tool_call_density = float(value)
    else:
        burst.tool_call_density = None


def _augment_blast_radius(
    burst: Burst,
    *,
    edit_payloads: dict[tuple[str, int], dict[str, Any]],
) -> None:
    """Cluster H T7: stamp ``burst.blast_radius`` from existing aggregates.

    Computes:

    * ``files_touched`` — ``len(burst.unique_files)``.
    * ``test_files_touched`` — files matching :data:`_TEST_FILE_RE`.
    * ``src_files_touched`` — files matching :data:`_SRC_FILE_RE`.
    * ``docs_files_touched`` — files matching :data:`_DOCS_FILE_RE`.
    * ``lines_added`` — sum of newline-counted ``new_string`` content
      across each patch (looked up via ``edit_payloads``). Falls back
      to 0 when no edit payload is available for a patch.
    * ``lines_removed`` — sum of newline-counted ``old_string`` content
      when available; ``None`` when no edit payloads were resolvable
      for any patch (we can't claim "0 lines removed" without
      evidence).

    Pure aggregation — no new git ops or trace traversal beyond the
    pre-built ``edit_payloads`` index.
    """
    files = list(burst.unique_files.keys()) if burst.unique_files else []
    test_n = sum(1 for f in files if _TEST_FILE_RE.search(f))
    src_n = sum(1 for f in files if _SRC_FILE_RE.search(f))
    docs_n = sum(1 for f in files if _DOCS_FILE_RE.search(f))

    lines_added = 0
    lines_removed = 0
    matched = 0
    removed_observed = 0
    for patch in burst.patches:
        fp = patch.get("file_path")
        step_index = patch.get("step_index")
        if not isinstance(fp, str) or not isinstance(step_index, int):
            continue
        payload = edit_payloads.get((fp, step_index))
        if not payload:
            continue
        matched += 1
        new_string = payload.get("new_string") or ""
        if isinstance(new_string, str):
            lines_added += _count_lines(new_string)
        old_string = payload.get("old_string")
        if isinstance(old_string, str) and old_string:
            removed_observed += 1
            lines_removed += _count_lines(old_string)

    burst.blast_radius = {
        "lines_added": int(lines_added),
        "lines_removed": int(lines_removed) if removed_observed > 0 else None,
        "files_touched": len(files),
        "test_files_touched": int(test_n),
        "src_files_touched": int(src_n),
        "docs_files_touched": int(docs_n),
    }


def _count_lines(text: str) -> int:
    """Return the line count of ``text`` (1 for non-empty single-line)."""
    if not text:
        return 0
    # ``splitlines`` excludes a trailing empty line, which matches
    # how diff tools count "added lines".
    return len(text.splitlines()) or 1


def _merge_adjacent_bursts_sharing_commit(
    bursts: list[Burst],
) -> list[Burst]:
    """Merge adjacent sub-bursts that share the same ``burst_commit_sha``.

    Cluster H T9 (hard-split on user_instruction) deliberately
    fragments a burst at each user redirection. When the user
    redirections were incorporated rather than abandoned — i.e. the
    sub-bursts converge on the same Git commit — those fragments
    represent one logical burst from a reviewer's perspective. The
    labeled regression on entry #6 has 19 mid-burst pivots that all
    converge on commit ``68d6723db``; collapsing adjacent
    same-commit fragments restores the single ``[32, 289]`` burst
    that ground truth labels.

    Bursts without a resolved ``burst_commit_sha`` (the trace had no
    matching commit, or commit_lookup is disabled) are NEVER merged
    — only bursts with the same non-None SHA collapse. The return
    list preserves order and replaces each merged group with one
    rebuilt :class:`Burst` whose state is the union of the inputs.
    """
    if len(bursts) < 2:
        return list(bursts)

    out: list[Burst] = []
    pending: list[Burst] = []
    pending_sha: str | None = None

    def _flush() -> None:
        nonlocal pending, pending_sha
        if not pending:
            return
        if len(pending) == 1:
            out.append(pending[0])
        else:
            out.append(_merge_burst_group(pending))
        pending = []
        pending_sha = None

    for burst in bursts:
        sha = burst.burst_commit_sha
        if sha is None:
            _flush()
            out.append(burst)
            continue
        if pending_sha is None:
            pending = [burst]
            pending_sha = sha
            continue
        if sha == pending_sha:
            pending.append(burst)
            continue
        _flush()
        pending = [burst]
        pending_sha = sha
    _flush()
    return out


def _merge_burst_group(group: list[Burst]) -> Burst:
    """Combine a non-empty list of bursts that share a commit SHA.

    The combined burst's ``step_range`` spans the union; ``patches``
    and ``unique_files`` are unioned (counts summed); intents come
    from the first burst (it is the earliest in step order, so its
    triggering user instruction is the most relevant). The commit
    SHA + intent.commit_* fields are inherited from the first burst
    (they are the same across the group by construction).
    """
    assert group, "expected at least one burst to merge"
    if len(group) == 1:
        return group[0]

    first = group[0]
    step_min = min(b.step_range[0] for b in group if b.step_range)
    step_max = max(b.step_range[-1] for b in group if b.step_range)

    unique_files: dict[str, int] = {}
    patches: list[dict[str, Any]] = []
    unique_anchors: list[str] = []
    seen_anchors: set[str] = set()
    contributing_node_ids: list[str] = []

    for b in group:
        for path, count in b.unique_files.items():
            unique_files[path] = unique_files.get(path, 0) + count
        for patch in b.patches:
            patches.append(dict(patch))
        for anchor in b.unique_git_anchors:
            if anchor not in seen_anchors:
                seen_anchors.add(anchor)
                unique_anchors.append(anchor)
        contributing_node_ids.extend(b.contributing_node_ids)

    merged = Burst(
        step_range=[step_min, step_max],
        intent_user_step=first.intent_user_step,
        intent_text=first.intent_text,
        unique_files=unique_files,
        patches=patches,
        unique_git_anchors=unique_anchors,
        has_git_anchor=bool(unique_anchors),
        burst_commit_sha=first.burst_commit_sha,
        burst_commit_step=first.burst_commit_step,
        intent=dict(first.intent),
        contributing_node_ids=contributing_node_ids,
    )
    return merged
