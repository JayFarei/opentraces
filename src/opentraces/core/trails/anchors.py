"""Delayed Git Anchor reconciliation for Trace Trails."""

from __future__ import annotations

import difflib
import subprocess
import time
from pathlib import Path
from typing import Any

from ...enrichment._shared import path_matches
from ...enrichment.attribution import _norm, _parse_diff_hunks_with_content
from .contract import ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION
from .event_log import append_event_batch, read_events_scoped
from .ids import (
    GIT_ANCHOR_CANONICALIZATION,
    content_ref,
    git_anchor_ref,
    id_from_payload,
    trace_patch_ref,
)
from .models import ATTRIBUTION_VERSION, GitObjectID, TrailEvent, TrailEventDraft
from .search_records import (
    build_anchor_search_summary_payload,
    iter_coverage_claims,
    iter_search_records,
)

ANCHOR_ALGORITHMS_PHASE3 = ["exact_range_hash"]
ANCHOR_ALGORITHMS_PHASE5 = ["exact_range_hash", "structural_match"]
STRUCTURAL_MATCH_THRESHOLD = 0.85


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _oid(repo: Path, rev_path: str) -> dict[str, str] | None:
    out = _git(repo, "rev-parse", rev_path, check=False)
    if not out:
        return None
    try:
        return GitObjectID(hex=out).model_dump(mode="json")
    except Exception:
        return None


def _stable_patch_id(repo: Path, commit: str) -> str | None:
    diff = _git(repo, "show", "--format=", "--no-color", commit)
    proc = subprocess.run(
        ["git", "patch-id", "--stable"],
        cwd=repo,
        input=diff,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.split()[0]


def _find_exact_anchor(
    patch: dict[str, Any], hunks: dict[str, list[dict]]
) -> dict[str, Any] | None:
    file_path = patch.get("file_path")
    authored = patch.get("authored_text") or ""
    needle = _norm(authored)
    if not file_path or not needle:
        return None
    for hunk_path, file_hunks in hunks.items():
        if not path_matches(file_path, hunk_path):
            continue
        for hunk in file_hunks:
            # #44: reuse the per-hunk normalized added text precomputed once per
            # reconcile (``_norm_added``) instead of re-normalizing the same hunk
            # for every (patch, hunk) pair. Falls back to live normalization when
            # the precomputed key is absent (legacy callers / hand-built hunks).
            if "_norm_added" in hunk:
                haystack = hunk["_norm_added"]
            else:
                haystack = _norm(hunk.get("added_text") or "")
            if needle and needle in haystack:
                return {
                    "path": hunk_path,
                    "range": {
                        "start_line": hunk.get("added_start"),
                        "end_line": hunk.get("added_end"),
                    },
                }
    return None


def _find_structural_anchor(
    patch: dict[str, Any], hunks: dict[str, list[dict]]
) -> dict[str, Any] | None:
    """Phase 5 structural fallback: line-level similarity over the same path.

    When the exact-range matcher fails (because a formatter rewrote
    non-whitespace characters such as quote style, parentheses, or
    operator spacing), this fallback compares the patch's authored text
    against each hunk's added text using ``difflib.SequenceMatcher``.

    Threshold ``STRUCTURAL_MATCH_THRESHOLD`` is tuned to accept
    quote-style and minor refactor changes while rejecting unrelated
    lines. Real AST-aware structural matching is future work; this is
    the substrate path so the evidence tier is wired correctly when
    better algorithms land.

    Returns a match dict with ``path``, ``range``, and ``similarity``
    fields, or ``None`` when no hunk in the same file scores above
    threshold.
    """
    file_path = patch.get("file_path")
    authored = patch.get("authored_text") or ""
    if not file_path or not authored.strip():
        return None
    authored_len = len(authored)
    best: dict[str, Any] | None = None
    best_score = 0.0
    for hunk_path, file_hunks in hunks.items():
        if not path_matches(file_path, hunk_path):
            continue
        for hunk in file_hunks:
            added = hunk.get("added_text") or ""
            if not added.strip():
                continue
            # #44 compute gate 1 — length bound (pure arithmetic). difflib's
            # documented invariant is ratio() <= real_quick_ratio(), and for two
            # sequences real_quick_ratio() is bounded above by
            # 2*min(len)/(len(a)+len(b)) (the best case where the shorter is a
            # subsequence of the longer). If that bound is already below the
            # threshold the pair CANNOT score >= threshold, so skip it WITHOUT
            # constructing a SequenceMatcher. This rejected 100% of the 1,077
            # length-mismatched pairs in the 30-minute-hang incident.
            added_len = len(added)
            denom = authored_len + added_len
            if denom == 0:
                continue
            length_bound = 2 * min(authored_len, added_len) / denom
            if length_bound < STRUCTURAL_MATCH_THRESHOLD:
                continue
            # #44 compute gate 2 — quick_ratio() is the cheap O(n) upper bound on
            # ratio(). Build the matcher ONCE; probe quick_ratio() first and only
            # pay for the full O(n*m) ratio() when the bound still admits a match.
            sm = difflib.SequenceMatcher(None, authored, added, autojunk=False)
            if sm.quick_ratio() < STRUCTURAL_MATCH_THRESHOLD:
                continue
            score = sm.ratio()
            if score >= STRUCTURAL_MATCH_THRESHOLD and score > best_score:
                best = {
                    "path": hunk_path,
                    "range": {
                        "start_line": hunk.get("added_start"),
                        "end_line": hunk.get("added_end"),
                    },
                    "similarity": round(score, 4),
                }
                best_score = score
    return best


def reconcile_commit_anchors(
    repo: Path,
    commit_ref: str = "HEAD",
    *,
    writer: str = "post-commit-correlator",
    capture_method: list[str] | None = None,
    trace_id: str | None = None,
    attribution_version: str | None = None,
    events: list[TrailEvent] | None = None,
    summary_out: dict[str, Any] | None = None,
    deadline: float | None = None,
    patch_events: list[TrailEvent] | None = None,
    anchor_keys: set[tuple] | None = None,
    search_keys: set[tuple] | None = None,
    append_events: bool = True,
) -> list[dict[str, Any]]:
    """Search existing Trace Patches against a commit and append anchor events.

    The post-commit correlator (Phase 3) calls this with the default
    ``capture_method=["post_commit_correlator"]`` and no trace filter.
    Phase 5's ``trail attach`` wraps this with
    ``capture_method=["manual_attach"]`` and a ``trace_id`` filter so the
    user can retroactively connect one trace's evidence to one commit
    without rewriting source events for other traces.

    #65: ``patch_events``/``anchor_keys``/``search_keys`` are the
    pre-extracted form of ``events`` — the batched maturation scan streams
    the whole-log read through a sink that keeps ONLY patch events plus the
    dedup key tuples, never retaining anchor/search events (the plan-090
    summary events carry results[] arrays that ballooned to GBs when
    re-materialised per tick). When all three are supplied, ``events`` is
    not consulted. ``anchor_keys`` entries are ``(trace_patch_id,
    commit_hex)``; ``search_keys`` entries are ``(trace_patch_id,
    search_head_sha, attribution_version)`` — both already filtered (or
    over-inclusive: keys for OTHER commits are harmless because every
    membership test below includes this commit's sha).

    ``append_events=False`` is the side-effect-free maturation mode: the caller
    receives this chunk's ``search_results`` and ``anchor_drafts`` in
    ``summary_out`` and decides when to append them. Default callers keep the
    original append-on-return behavior.
    """
    repo = repo.resolve()
    effective_capture_method = (
        list(capture_method) if capture_method else ["post_commit_correlator"]
    )
    effective_attribution_version = attribution_version or ATTRIBUTION_VERSION
    commit = _git(repo, "rev-parse", commit_ref)
    commit_id = {"algo": "sha1", "hex": commit}
    diff = _git(repo, "show", "--format=", "--no-color", "-U3", commit)
    hunks = _parse_diff_hunks_with_content(diff)
    # #44 (d): normalize each hunk's added_text ONCE per reconcile and stash it on
    # the hunk dict (``_norm_added``). _find_exact_anchor reuses it per (patch,
    # hunk) pair instead of re-normalizing the same hunk N times.
    for file_hunks in hunks.values():
        for hunk in file_hunks:
            hunk["_norm_added"] = _norm(hunk.get("added_text") or "")
    # Bug B: this reconciler needs only 3 event types, and the anchor/search
    # dedup only consults events referencing THIS commit. Read that scoped slice
    # (streamed per-commit, no whole-log materialisation, no verify) instead of
    # the full ~N-event history that drove the post-commit hook to ~7.5GB RSS.
    #
    # #23: when ``events`` is pre-supplied (the batched maturation scan reads
    # the anchor/search slice for ALL recent commits in one whole-log pass),
    # filter that supplied list to this commit's sha here exactly as the scoped
    # commit_filter would — preserving plan-090 R5 dedup. When ``events`` is
    # None the per-commit scoped read is byte-identical (the post-commit hook
    # path is unchanged).
    if patch_events is not None and anchor_keys is not None and search_keys is not None:
        # #65 pre-extracted path: keys arrive ready-made. No coverage claims
        # here — the caller's SQLite key cache (post_commit.py, maturation.py)
        # does not index v3 coverage claims yet, so this path dedups on exact
        # keys only, exactly as before #358. ``all_patch_events`` is the
        # UNSCOPED list the caller supplied — the trace_id scope filter is
        # applied uniformly below, after the sort, alongside the direct-read
        # branch's.
        existing_anchor_keys = anchor_keys
        existing_search_keys = search_keys
        claims: list[dict[str, Any]] = []
        all_patch_events = patch_events
    else:
        if events is None:
            events = read_events_scoped(
                repo,
                event_types={
                    "trace_patch_created",
                    "git_anchor_created",
                    "git_anchor_search_completed",
                },
                commit_filter={
                    "git_anchor_created": "commit_id",
                    "git_anchor_search_completed": "search_head",
                },
                commit_sha=commit,
            )
        else:
            events = [
                event
                for event in events
                if (
                    event.event_type == "trace_patch_created"
                    or (
                        event.event_type == "git_anchor_created"
                        and (event.payload.get("commit_id") or {}).get("hex") == commit
                    )
                    or (
                        event.event_type == "git_anchor_search_completed"
                        and (event.payload.get("search_head") or {}).get("hex") == commit
                    )
                )
            ]
        existing_anchor_keys = {
            (id_from_payload(event.payload, "trace_patch"), (event.payload.get("commit_id") or {}).get("hex"))
            for event in events
            if event.event_type == "git_anchor_created"
        }
        # Dedup is per-(patch, commit, attribution_version). Expand BOTH the legacy
        # per-patch search events and the new v2 summary events through the shared
        # reader so the key set is identical across shapes (plan 090, R5). This is
        # the single load-bearing invariant: an already-searched (patch, commit,
        # version) must not be re-emitted, and a not-yet-searched patch (e.g. a
        # late-ingested one) must still be searched.
        existing_search_keys = {
            (
                record["trace_patch_id"],
                record["search_head_sha"],
                record["attribution_version"],
            )
            for event in events
            if event.event_type == "git_anchor_search_completed"
            for record in iter_search_records(event)
        }
        # #358: v3 events may carry a coverage through-pointer INSTEAD of a
        # per-patch key for every unknown outcome. ``events`` is already
        # scoped to this commit's search_head (above), so only the
        # attribution_version needs filtering here — a claim from a stale
        # ATTRIBUTION_VERSION must not suppress a re-search under a newer one.
        claims = [
            claim
            for event in events
            if event.event_type == "git_anchor_search_completed"
            for claim in iter_coverage_claims(event)
            if claim["attribution_version"] == effective_attribution_version
        ]
        all_patch_events = [
            event for event in events if event.event_type == "trace_patch_created"
        ]

    # #358: sort ONCE, in canonical event_sequence order, over the UNSCOPED
    # patch list — both the deadline-break resume semantics (unaffected) and
    # the coverage through-pointer semantics (new) depend on that order. Sort
    # explicitly rather than trusting the read path (the #65 pre-extracted
    # path and a caller-provided ``events`` list are not guaranteed sorted).
    # ``patch_events`` (the list the search loop below iterates) is then a
    # stable filter of the sorted list, so it inherits the same order without
    # a second sort.
    all_patch_events = sorted(all_patch_events, key=lambda event: event.event_sequence)
    patch_events = [
        event
        for event in all_patch_events
        if trace_id is None or event.trace_id == trace_id
    ]

    # #358: resolve each coverage claim's through_trace_patch_id — and, in the
    # search loop below, every visited patch's own position — to an INDEX in
    # ``all_patch_events`` (the FULL, un-scoped ordering), never to a raw
    # event_sequence number (a compaction rewrite can renumber) and never to
    # an index into the trace-scoped ``patch_events`` list: an unscoped
    # claim's boundary patch may belong to a DIFFERENT trace than a
    # trace-scoped run (R5, ``trail attach``) is searching, so resolving
    # against the scoped list would make that claim unreadable to a scoped
    # run and force a spurious re-search. A duplicate trace_patch_id
    # (re-ingested session, hook retry) collapses to its FIRST occurrence —
    # keep-first, never overwrite — because that is the only resolution whose
    # answer cannot change as MORE duplicates of the same id are ingested
    # later. Keep-LAST was tried and reverted: it makes an EXISTING claim's
    # effective boundary drift forward every time the log grows a fresh
    # duplicate of its through-id, so a distinct patch ingested (and never
    # searched) after the claim can end up silently claim-covered — fail
    # CLOSED, which the design forbids ("fail open to re-search, never fail
    # closed"). Keep-first pins every id's position for good the moment it is
    # first seen; a later duplicate of that id always resolves back to the
    # SAME position, so it can never extend a stored claim past ground it
    # never covered. A claim whose through-id is not found here covers
    # NOTHING: fail open to re-search, never fail closed.
    patch_position: dict[str, int] = {}
    for position, patch_event in enumerate(all_patch_events):
        pid = id_from_payload(patch_event.payload, "trace_patch")
        if pid and pid not in patch_position:
            patch_position[pid] = position
    resolved_claims: list[tuple[int, str | None]] = [
        (patch_position[claim["through_trace_patch_id"]], claim["scope_trace_id"])
        for claim in claims
        if claim["through_trace_patch_id"] in patch_position
    ]

    def _covered_by_claim(position: int, patch_trace_id: str | None) -> bool:
        # scope_trace_id None covers every trace up to the boundary; a scoped
        # claim (R5, ``trail attach``) covers ONLY its own trace's patches —
        # never leak coverage to a trace the claiming run never searched.
        return any(
            position <= boundary and (scope is None or scope == patch_trace_id)
            for boundary, scope in resolved_claims
        )

    # plan 090: collect every patch's search outcome into ONE per-commit
    # summary event (search_results) rather than appending one
    # git_anchor_search_completed event per patch. The K git_anchor_created
    # events are still emitted per match, byte-identical (R5).
    anchor_drafts: list[TrailEventDraft] = []
    created: list[dict[str, Any]] = []
    search_results: list[dict[str, Any]] = []
    # #44 (b): _stable_patch_id(repo, commit) is loop-invariant — compute it
    # lazily on the first anchor and reuse. (427 subprocess pairs -> 1.)
    _patch_id_cache: dict[str, str | None] = {}

    def _patch_id() -> str | None:
        if commit not in _patch_id_cache:
            _patch_id_cache[commit] = _stable_patch_id(repo, commit)
        return _patch_id_cache[commit]

    # #44 (c): _oid(repo, f"{commit}:{path}") is per-(commit, path) — cache it so
    # multiple patches landing in the same file share one rev-parse subprocess.
    _oid_cache: dict[str, dict[str, str] | None] = {}

    def _oid_for(path: str) -> dict[str, str] | None:
        if path not in _oid_cache:
            _oid_cache[path] = _oid(repo, f"{commit}:{path}")
        return _oid_cache[path]

    # #44 Phase 2: wall-clock budget. ``deadline`` (time.monotonic absolute) is
    # checked at the TOP of the per-patch loop. On expiry we break out and STILL
    # append the summary covering the searched subset + created anchors below.
    budget_exhausted = False
    patches_searched = 0
    patches_total = len(patch_events)
    # #358 adversarial repair: the claim this run earns must be the MAXIMUM
    # first-occurrence position it actually visited-and-processed (searched,
    # or skipped as already-anchored/already-searched/claim-covered) WITHOUT
    # hitting the deadline break — never simply "whichever patch_event the
    # loop happened to iterate last". Raw iteration order (event_sequence)
    # and first-occurrence-position order can diverge: a duplicate
    # trace_patch_id resolves, via ``patch_position`` (keep-first), to an
    # EARLIER position than its own raw index, so a duplicate visited late in
    # the loop must never overwrite a higher-position id this run already
    # visited — that would silently shrink the claim below ground already
    # covered. ``_note_processed`` tracks the running maximum; every
    # ``continue`` below and the full search path call it, the ``break`` does
    # not. Stays unset (no claim emitted) if every processed patch lacked an
    # id, which can only happen if NO patch was ever searched either (id-less
    # patches can never reach the search step), so the ``if search_results:``
    # emission gate already excludes that case.
    coverage_position = -1
    coverage_trace_patch_id: str | None = None

    def _note_processed(pid: str) -> None:
        nonlocal coverage_position, coverage_trace_patch_id
        position = patch_position[pid]
        if position > coverage_position:
            coverage_position = position
            coverage_trace_patch_id = pid

    for patch_event in patch_events:
        patch = patch_event.payload
        trace_patch_id = id_from_payload(patch, "trace_patch")
        if not trace_patch_id:
            continue
        if (trace_patch_id, commit) in existing_anchor_keys:
            _note_processed(trace_patch_id)
            continue
        already_searched = (
            trace_patch_id,
            commit,
            effective_attribution_version,
        ) in existing_search_keys
        # patch_position[trace_patch_id] is guaranteed present: patch_events is
        # a filter of all_patch_events, which is exactly where patch_position
        # was built from.
        if already_searched or _covered_by_claim(
            patch_position[trace_patch_id], patch_event.trace_id
        ):
            # A prior search for this (patch, commit) already recorded a
            # result under the same attribution version — via an exact key
            # (legacy/v2, or a v3 anchored entry) or a v3 coverage claim
            # (unknown outcomes never get an exact key under v3) — don't
            # re-record a duplicate. Newer attribution versions are allowed to
            # append a new search so periodic re-search remains possible.
            _note_processed(trace_patch_id)
            continue
        # #65: gate the wall-clock budget AFTER the cheap dedup-skips above, never
        # before. An already-searched/anchored prefix must not consume the
        # deadline — otherwise a commit with a large covered prefix burns the whole
        # budget replaying skips and never reaches its unsearched tail, so a
        # partially searched commit could never finish across ticks (the livelock).
        if deadline is not None and time.monotonic() >= deadline:
            budget_exhausted = True
            break
        _note_processed(trace_patch_id)
        patches_searched += 1
        match = _find_exact_anchor(patch, hunks)
        evidence_tier = "exact_range_hash"
        evidence_firmness = "firm"
        anchor_limitations: list[str] = []
        if match is None:
            structural = _find_structural_anchor(patch, hunks)
            if structural is not None:
                match = {
                    "path": structural["path"],
                    "range": structural["range"],
                }
                evidence_tier = "structural_match"
                evidence_firmness = "provisional"
                anchor_limitations.append("structural_match_below_exact_threshold")
        anchor_payload = None
        created_anchor_ids: list[str] = []
        if match:
            blob_id = _oid_for(match["path"])
            # #32 before-blob guard: a commit whose target-file content equals
            # the patch's pre-edit blob is the state BEFORE the patch landed, so
            # it cannot be this patch's landing commit. Reject the match (a plain
            # `git revert` lands a commit that restores the pre-edit content; the
            # structural matcher would otherwise mis-anchor the revert's
            # re-introduced old lines, leaving survival stuck at `unknown` instead
            # of resolving to `reverted`). Patches without a recorded
            # before_blob_id (legacy / fs_watcher with no parent blob) skip the
            # guard, preserving prior behavior. Temporal-free and backfill-safe.
            before_hex = (patch.get("before_blob_id") or {}).get("hex")
            blob_hex = (blob_id or {}).get("hex")
            if before_hex and blob_hex and blob_hex == before_hex:
                match = None
        if match:
            git_anchor_object_ref = content_ref(
                kind="git_anchor",
                canonicalization=GIT_ANCHOR_CANONICALIZATION,
                relation="anchored_in_git",
                material={
                    "trace_patch_ref": trace_patch_ref(trace_patch_id),
                    "commit_id": commit_id,
                    "path": match["path"],
                    "range": match["range"],
                    "evidence_tier": evidence_tier,
                },
            )
            git_anchor_id = git_anchor_object_ref["id"]
            created_anchor_ids = [git_anchor_id]
            anchor_payload = {
                "git_anchor_id": git_anchor_id,
                "git_anchor_ref": git_anchor_ref(git_anchor_id),
                "trace_patch_id": trace_patch_id,
                "trace_patch_ref": trace_patch_ref(trace_patch_id),
                "commit_id": commit_id,
                "path": match["path"],
                "range": match["range"],
                "blob_id": blob_id,
                "patch_id": _patch_id(),
                "observed_ref": commit_ref,
                "relation": "anchored_in_git",
                "evidence_tier": evidence_tier,
                "evidence_firmness": evidence_firmness,
                "source": writer,
                "limitations": anchor_limitations,
            }

        # Record this patch's search outcome into the per-commit summary. Built
        # ONLY from the trace_id-filtered patches this loop visited, so the
        # manual-attach trace scoping (R5 scenario 5) is preserved. Commit
        # identity lives only in the summary's top-level search_head; no
        # ``*_sha`` keys here (payload validation rejects bare git shas).
        search_results.append(
            {
                "trace_patch_id": trace_patch_id,
                "trace_id": patch_event.trace_id,
                "step_index": patch_event.step_index,
                "generation_index": patch_event.generation_index,
                "result": "anchored" if anchor_payload else "unknown",
                "created_anchor_ids": created_anchor_ids,
            }
        )
        existing_search_keys.add(
            (trace_patch_id, commit, effective_attribution_version)
        )
        if anchor_payload:
            anchor_drafts.append(
                TrailEventDraft(
                    event_type="git_anchor_created",
                    trace_id=patch_event.trace_id,
                    generation_index=patch_event.generation_index,
                    step_index=patch_event.step_index,
                    capture_method=effective_capture_method,
                    ATTRIBUTION_VERSION=effective_attribution_version,
                    payload=anchor_payload,
                )
            )
            existing_anchor_keys.add((trace_patch_id, commit))
            created.append(anchor_payload)

    drafts: list[TrailEventDraft] = []
    if append_events:
        if search_results:
            # #358: the v3 coverage claim this run earns. ``coverage_
            # trace_patch_id`` is guaranteed non-None here — search_results is
            # non-empty, so at least one patch reached the search step, and
            # only an id-bearing patch can reach it (so ``_note_processed``
            # ran at least once). scope_trace_id is the ``trace_id`` ARGUMENT
            # (not any per-patch trace_id) — null for an unscoped run, set for
            # a trace-scoped run (R5, ``trail attach``).
            coverage = {
                "through_trace_patch_id": coverage_trace_patch_id,
                "scope_trace_id": trace_id,
            }
            # One summary event per (commit, reconcile-run): trace_id/step_index are
            # None because it spans the searched patches (per-patch trace_id /
            # step_index live inside results[]).
            drafts.append(
                TrailEventDraft(
                    event_type="git_anchor_search_completed",
                    trace_id=None,
                    step_index=None,
                    capture_method=effective_capture_method,
                    ATTRIBUTION_VERSION=effective_attribution_version,
                    payload=build_anchor_search_summary_payload(
                        schema_version=ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION,
                        search_head=commit_id,
                        algorithms_attempted=ANCHOR_ALGORITHMS_PHASE5,
                        results=search_results,
                        coverage=coverage,
                    ),
                )
            )
        drafts.extend(anchor_drafts)

        if drafts:
            append_event_batch(repo, drafts, writer=writer)
    if summary_out is not None:
        # #23: surface the per-patch search count to the caller so maturation can
        # sum these instead of re-reading the whole log twice (before/after) just
        # to diff search-record counts. One per-patch search outcome was recorded
        # per visited patch (search_results), matching the prior _event_counts
        # delta semantics. On a partial (budgeted) run this reports ONLY the
        # searches actually recorded this run — maturation sums it.
        summary_out["searches_recorded"] = len(search_results)
        # #44 Phase 2: budget surface. patches_searched counts the patches that
        # passed the budget gate this run; patches_remaining is the unvisited
        # tail. On a non-budgeted (or non-tripped) run budget_exhausted is False
        # and patches_remaining is 0.
        summary_out["budget_exhausted"] = budget_exhausted
        summary_out["patches_searched"] = patches_searched
        summary_out["patches_remaining"] = patches_total - patches_searched
        if not append_events:
            summary_out["search_results"] = search_results
            summary_out["anchor_drafts"] = anchor_drafts
    return created
