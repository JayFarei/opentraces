"""Deterministic builder for the pr-intent-summary-v1 workflow.

Reads ``OT_RUN_PACKET`` for the run packet (with branch_context scope) and
writes one JSONL row per commit to ``OT_DATASET_OUTPUT``.

Each row joins a commit on the branch to the agent traces that authored it,
using two complementary signals: entity-level attribution
(:func:`opentraces.core.entity_join.join_entities_to_traces`, strongest when
the attribution cache covers the commit) and burst-by-modal-commit
(:func:`opentraces.core.bursts.detect_bursts` indexed by
``Burst.burst_commit_sha``, the fallback). The "what changed" prose comes from
:func:`opentraces.core.trace_summary.summarize_contribution`; the "why" comes
verbatim from ``Burst.intent``.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from opentraces.core import paths as ot_paths
from opentraces.core.branch_context import (
    NO_LINEAGE_MERGE_COMMIT,
    NO_LINEAGE_NO_ATTRIBUTION,
    NO_LINEAGE_NO_BURST,
    NO_LINEAGE_NO_TRACES,
    bucket_remote_url,
)
from opentraces.core.bucket_store import iter_trace_record_objects
from opentraces.core.bursts import detect_bursts
from opentraces.core.entity_join import join_entities_to_traces
from opentraces.core.llm_polish import polish_branch
from opentraces.core.trace_map import build_trace_map
from opentraces.core.trace_meta import short_trace_id
from opentraces.core.trace_summary import summarize_contribution


def main() -> int:
    packet_path = os.environ.get("OT_RUN_PACKET")
    output_path = os.environ.get("OT_DATASET_OUTPUT")
    if not packet_path or not output_path:
        print("OT_RUN_PACKET and OT_DATASET_OUTPUT must be set", file=sys.stderr)
        return 2

    packet = json.loads(Path(packet_path).read_text(encoding="utf-8"))
    scope = packet.get("scope") or {}
    if scope.get("kind") != "branch_context":
        print(
            f"unsupported scope.kind: {scope.get('kind')!r} "
            "(expected 'branch_context')",
            file=sys.stderr,
        )
        return 2

    project_dir = Path(scope.get("project_dir") or ".").expanduser().resolve()
    project_slug = str(scope.get("project_slug") or "")
    commits = list(scope.get("commits") or [])
    scope_digest = str(scope.get("scope_digest") or "")
    commit_info_by_sha = {c.get("sha"): c for c in commits if c.get("sha")}

    remote_url = bucket_remote_url()
    burst_index, records_by_trace = _build_burst_index(
        project_dir,
        project_slug=project_slug,
        commit_shas=set(commit_info_by_sha.keys()),
    )

    out_lines: list[str] = []
    for commit in commits:
        sha = commit.get("sha") or ""
        if not sha:
            continue
        is_merge = bool(commit.get("is_merge"))

        if is_merge:
            row = _row_no_lineage(commit, scope_digest, reason=NO_LINEAGE_MERGE_COMMIT)
            out_lines.append(json.dumps(row, sort_keys=True))
            continue

        try:
            contribs = join_entities_to_traces(project_dir, sha)
        except Exception:
            contribs = []
        contribs = [
            c for c in contribs
            if c.line_count > 0 and c.overlap_case in {"has_entities", "scattered"}
        ]

        commit_info = commit_info_by_sha.get(sha) or {}
        commit_subject = commit_info.get("subject")
        commit_body = commit_info.get("body")

        lineage: list[dict[str, Any]] = []
        if contribs:
            for contrib in contribs:
                lineage.append(
                    _lineage_entry(
                        trace_id=contrib.trace_id,
                        contrib=contrib,
                        burst=_find_burst_for_trace(burst_index, sha, contrib.trace_id),
                        bucket_record=records_by_trace.get(contrib.trace_id),
                        bucket_remote_url=remote_url,
                        commit_subject=commit_subject,
                        commit_body=commit_body,
                    )
                )
        else:
            for trace_id, burst, bucket_record in burst_index.get(sha, []):
                lineage.append(
                    _lineage_entry(
                        trace_id=trace_id,
                        contrib=None,
                        burst=burst,
                        bucket_record=bucket_record,
                        bucket_remote_url=remote_url,
                        commit_subject=commit_subject,
                        commit_body=commit_body,
                    )
                )

        no_reason = None
        if not lineage:
            if not burst_index:
                no_reason = NO_LINEAGE_NO_TRACES
            elif sha not in burst_index:
                no_reason = NO_LINEAGE_NO_BURST
            else:
                no_reason = NO_LINEAGE_NO_ATTRIBUTION

        row = {
            "commit_sha": sha,
            "short_sha": str(commit.get("short_sha") or sha[:7]),
            "subject": str(commit.get("subject") or ""),
            "body": str(commit.get("body") or ""),
            "is_merge": is_merge,
            "scope_digest": scope_digest,
            "lineage": lineage,
            "no_lineage_reason": no_reason,
        }
        out_lines.append(json.dumps(row, sort_keys=True))

    # Optional LLM enrichment (on by default; opt out via OT_LLM_POLISH=0).
    # polish_branch() degrades gracefully to None on any failure.
    polish_setting = (os.environ.get("OT_LLM_POLISH") or "1").strip().lower()
    if polish_setting not in {"0", "false", "no", "off"}:
        rows_for_polish = [json.loads(line) for line in out_lines]
        enrichment = polish_branch(commits=commits, rows=rows_for_polish)
        if enrichment is not None:
            out_lines.append(json.dumps(
                {
                    "_kind": "branch_summary",
                    "scope_digest": scope_digest,
                    "llm": enrichment.to_json(),
                },
                sort_keys=True,
            ))

    Path(output_path).write_text(
        ("\n".join(out_lines) + "\n") if out_lines else "",
        encoding="utf-8",
    )
    return 0


def _build_burst_index(
    project_dir: Path,
    *,
    project_slug: str,
    commit_shas: set[str],
) -> tuple[dict[str, list[tuple[str, Any, Any]]], dict[str, Any]]:
    """Return ``(index, records_by_trace)``.

    ``index`` maps burst_commit_sha -> [(trace_id, burst, bucket_record)],
    keyed only by SHAs in ``commit_shas`` (the branch's commit set).
    ``records_by_trace`` maps trace_id -> bucket_record for every trace
    scanned, so Tier-1 contributions can resolve a bucket record even when
    no burst landed on their commit.

    ``commit_lookup`` is disabled in ``detect_bursts`` — the caller already
    has commit subject/body from the branch scope, and re-fetching them
    per-burst was the dominant cost.
    """

    index: dict[str, list[tuple[str, Any, Any]]] = defaultdict(list)
    records_by_trace: dict[str, Any] = {}
    for bucket_record in iter_trace_record_objects(project_slug=project_slug or None):
        records_by_trace[bucket_record.trace_id] = bucket_record
        try:
            trace_map = build_trace_map(bucket_record.record)
            bursts = detect_bursts(
                trace_map,
                trace_record=bucket_record.record,
                repo_path=str(project_dir),
                commit_lookup=False,
            )
        except Exception:
            continue
        for burst in bursts:
            sha = burst.burst_commit_sha
            if not sha or (commit_shas and sha not in commit_shas):
                continue
            index[sha].append((bucket_record.trace_id, burst, bucket_record))
    return index, records_by_trace


def _find_burst_for_trace(
    burst_index: dict[str, list[tuple[str, Any, Any]]],
    sha: str,
    trace_id: str,
) -> Any | None:
    for tid, burst, _ in burst_index.get(sha, []):
        if tid == trace_id:
            return burst
    return None


def _lineage_entry(
    *,
    trace_id: str,
    contrib: Any | None,
    burst: Any | None,
    bucket_record: Any | None,
    bucket_remote_url: str | None,
    commit_subject: str | None,
    commit_body: str | None,
) -> dict[str, Any]:
    intent = dict((burst.intent if burst is not None else None) or {})
    # commit_lookup is off in _build_burst_index, so commit subject/body
    # come from the branch scope instead of the burst.
    if commit_subject and not intent.get("commit_subject"):
        intent["commit_subject"] = commit_subject
    if commit_body and not intent.get("commit_body"):
        intent["commit_body"] = commit_body
    spec_chain = intent.get("spec_chain") or []
    did_what: list[str] = []
    credit: dict[str, Any] = {}
    if contrib is not None:
        try:
            did_what = list(summarize_contribution(contrib, ascii_only=True))
        except Exception:
            did_what = []
        credit = {
            "line_count": int(contrib.line_count or 0),
            "line_ratio": float(contrib.line_ratio or 0.0),
            "overlap_case": str(contrib.overlap_case or ""),
            "entity_count": len(contrib.entities or []),
        }

    files_touched: list[str] = []
    patches_payload: list[dict[str, Any]] = []
    git_anchors: list[str] = []
    if burst is not None:
        files_touched = sorted((burst.unique_files or {}).keys())
        for patch in (burst.patches or []):
            patches_payload.append(
                {
                    "patch_id": patch.get("patch_id"),
                    "git_anchor_id": patch.get("git_anchor_id"),
                    "commit_sha": patch.get("commit_sha"),
                    "evidence_firmness": patch.get("evidence_firmness"),
                }
            )
        git_anchors = list(burst.unique_git_anchors or [])

    bucket_pointer: dict[str, Any] = {
        "uri": f"ot://trace/{trace_id}",
        "object_path": None,
        "remote_url": bucket_remote_url,
        "record_hash": None,
    }
    project_slug: str | None = None
    if bucket_record is not None:
        try:
            relative = bucket_record.path.relative_to(ot_paths.bucket_dir())
            bucket_pointer["object_path"] = relative.as_posix()
        except ValueError:
            bucket_pointer["object_path"] = str(bucket_record.path)
        bucket_pointer["record_hash"] = bucket_record.record_hash
        project_slug = bucket_record.project_slug

    return {
        "trace_id": trace_id,
        "short_id": short_trace_id(trace_id),
        "project_slug": project_slug,
        "credit": credit,
        "intent": {
            "trigger": intent.get("trigger"),
            "most_substantive_spec": intent.get("most_substantive_spec"),
            "spec_chain_count": len(spec_chain),
            "commit_subject": intent.get("commit_subject"),
            "commit_body": intent.get("commit_body"),
        },
        "did_what_bullets": did_what,
        "files_touched": files_touched[:32],
        "patches": patches_payload,
        "git_anchors": git_anchors,
        "bucket_pointer": bucket_pointer,
    }


def _row_no_lineage(
    commit: dict[str, Any], scope_digest: str, *, reason: str,
) -> dict[str, Any]:
    return {
        "commit_sha": str(commit.get("sha") or ""),
        "short_sha": str(commit.get("short_sha") or ""),
        "subject": str(commit.get("subject") or ""),
        "body": str(commit.get("body") or ""),
        "is_merge": bool(commit.get("is_merge")),
        "scope_digest": scope_digest,
        "lineage": [],
        "no_lineage_reason": reason,
    }


if __name__ == "__main__":
    raise SystemExit(main())
