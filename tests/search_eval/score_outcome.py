"""U2 - outcome scorers (pure, deterministic).

Relevance metrics over a ranked result list, computed at **trace granularity**.
``trace query`` returns one ``CandidatePacket`` per *unit* (step / map node /
patch), so several packets can share a ``trace_id``; every scorer here first
dedupes to distinct traces in first-occurrence order before measuring.

All functions are pure and deterministic - given the same ranked traces and
gold they return the same numbers, so the outcome half of the report is
byte-stable across runs even though the perf half is wall-clock.
"""

from __future__ import annotations

import math
from typing import Iterable


def distinct_traces(trace_ids: Iterable[str]) -> list[str]:
    """Collapse a per-unit trace_id list to distinct traces, first occurrence."""

    seen: list[str] = []
    seen_set: set[str] = set()
    for tid in trace_ids:
        if tid and tid not in seen_set:
            seen.append(tid)
            seen_set.add(tid)
    return seen


def recall_at_k(ranked: list[str], gold: Iterable[str], k: int) -> float:
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    topk = set(ranked[:k])
    return len(gold_set & topk) / len(gold_set)


def hit_at_k(ranked: list[str], gold: Iterable[str], k: int) -> float:
    gold_set = set(gold)
    return 1.0 if gold_set & set(ranked[:k]) else 0.0


def mrr(ranked: list[str], gold: Iterable[str]) -> float:
    gold_set = set(gold)
    for idx, tid in enumerate(ranked, start=1):
        if tid in gold_set:
            return 1.0 / idx
    return 0.0


def first_rank(ranked: list[str], gold: Iterable[str]) -> int | None:
    gold_set = set(gold)
    for idx, tid in enumerate(ranked, start=1):
        if tid in gold_set:
            return idx
    return None


def ndcg_at_k(ranked: list[str], gold: Iterable[str], k: int) -> float:
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    dcg = 0.0
    for idx, tid in enumerate(ranked[:k], start=1):
        if tid in gold_set:
            dcg += 1.0 / math.log2(idx + 1)
    ideal_hits = min(len(gold_set), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return (dcg / idcg) if idcg > 0 else 0.0


def kendall_tau(ranked: list[str], gold_order: list[str]) -> float | None:
    """Kendall tau-b between the returned order of the gold traces and the
    planted (intended) gold order. Only the gold traces present in ``ranked``
    participate; returns ``None`` when fewer than 2 are present.
    """

    gold_index = {tid: i for i, tid in enumerate(gold_order)}
    # subsequence of gold traces in the order they appear in the result
    present = [gold_index[t] for t in ranked if t in gold_index]
    n = len(present)
    if n < 2:
        return None
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            a, b = present[i], present[j]
            if a < b:
                concordant += 1
            elif a > b:
                discordant += 1
    total = n * (n - 1) / 2
    return (concordant - discordant) / total if total else None


def recency_hit(ranked: list[str], gold: Iterable[str], gold_latest: str | None) -> dict:
    """Two recency-hit@1 readings:

    * ``among_gold`` - is the latest gold trace the *first gold trace* in the
      result (isolates the recency signal from general relevance). This is R6's
      "recency-hit@1 on the planted set".
    * ``global`` - is the latest gold trace the overall rank-1 result.
    """

    gold_set = set(gold)
    among_gold = 0.0
    if gold_latest is not None:
        present_gold = [t for t in ranked if t in gold_set]
        if present_gold and present_gold[0] == gold_latest:
            among_gold = 1.0
    glob = 1.0 if (gold_latest is not None and ranked and ranked[0] == gold_latest) else 0.0
    return {"among_gold": among_gold, "global": glob}


def score_row(ranked: list[str], gold_trace_ids: list[str], gold_kind: str,
              gold_latest: str | None, targets: dict) -> dict:
    """Score one query row against its recorded gold, returning all metrics
    plus a per-target pass map derived from ``targets``.
    """

    k = int(targets.get("recall_at", 10))
    metrics = {
        "distinct_returned": len(ranked),
        "recall_at_k": recall_at_k(ranked, gold_trace_ids, k),
        "mrr": mrr(ranked, gold_trace_ids),
        "ndcg_at_k": ndcg_at_k(ranked, gold_trace_ids, k),
        "first_rank": first_rank(ranked, gold_trace_ids),
        "k": k,
    }
    if gold_kind == "ordered":
        metrics["kendall_tau"] = kendall_tau(ranked, gold_trace_ids)
    if gold_latest is not None:
        metrics["recency_hit"] = recency_hit(ranked, gold_trace_ids, gold_latest)

    passes: dict[str, bool] = {}
    if "recall_min" in targets:
        passes["recall"] = metrics["recall_at_k"] >= targets["recall_min"]
    if "mrr_min" in targets:
        passes["mrr"] = metrics["mrr"] >= targets["mrr_min"]
    if "tau_min" in targets:
        tau = metrics.get("kendall_tau")
        passes["tau"] = tau is not None and tau >= targets["tau_min"]
    if "recency_hit_at" in targets:
        rh = metrics.get("recency_hit") or {}
        passes["recency_hit"] = rh.get("among_gold", 0.0) >= 1.0
    if "top_k_rank" in targets:
        rank = metrics["first_rank"]
        passes["top_k_rank"] = rank is not None and rank <= targets["top_k_rank"]

    metrics["passes"] = passes
    metrics["all_targets_met"] = bool(passes) and all(passes.values())
    return metrics
