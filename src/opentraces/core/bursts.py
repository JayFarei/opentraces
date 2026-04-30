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

Empirically the value the canonical ``--bursts`` flag uses. Configurable
via the ``--burst-gap`` CLI flag and the ``gap`` argument here.
"""

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
            "unique_git_anchors": list(self.unique_git_anchors),
            "has_git_anchor": self.has_git_anchor,
        }


def detect_bursts(
    source: TraceMap | list[TraceMapNode],
    *,
    gap: int = DEFAULT_BURST_GAP,
    trace_record: Any | None = None,
    repo_path: Path | str | None = None,
    commit_lookup: bool = True,
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
    if gap < 1:
        gap = 1

    candidates: list[TraceMapNode] = [
        node
        for node in nodes
        if node.action_type in _BURST_INPUT_TYPES and node.step_index is not None
    ]
    if not candidates:
        return []

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

    bursts: list[Burst] = []
    current: list[TraceMapNode] = []
    last_step: int | None = None

    def _flush() -> None:
        if not current:
            return
        bursts.append(_make_burst(current, user_text_by_step, repo_root_str))

    for node in candidates:
        step = node.step_index or 0
        if last_step is not None and (step - last_step) > gap:
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

    for burst in bursts:
        _augment_intent_chain(burst, user_instruction_nodes)
        _augment_burst_commit_sha(burst, commit_index)
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
