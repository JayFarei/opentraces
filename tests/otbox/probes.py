"""Runtime precondition probes (otbox 2.0 phase 4).

The 1.0 tautology this kills: the precondition resolver checked a
checkpoint's STATIC ``provides`` list, so a journey declaring
``requires_survival_states = ["reverted"]`` ran happily against a world
whose substrate had actually landed on ``unknown`` (the survival-walk
flagship passed with zero results).

Probes re-verify preconditions against the LIVE box through the public CLI,
after restore and before any step. A failed probe is an ERROR
(``precondition_unmet``) — never SKIP (which hides), never PASS (which lies).

Scope rule (per the spec): probes are MANDATORY for gold-intent journeys
whose precondition keys have a registered probe; silver/bronze journeys get
them too (they're cheap), but only gold treats an unprobeable key as a
hard gap elsewhere (the catalogue lint may tighten this later). No free-form
journey-side probe DSL — this fixed registry is the whole vocabulary.
"""

from __future__ import annotations

import json
from typing import Callable

ProbeResult = tuple[bool, str]


def _cli_json(driver, box, *args) -> dict | list | None:
    res = driver.exec(box, [*driver.cli_argv(box), "--json", *args])
    if res.returncode != 0:
        return None
    try:
        text = res.stdout
        marker = "---OPENTRACES_JSON---"
        if marker in text:
            text = text.split(marker, 1)[1]
        return json.loads(text.strip())
    except (ValueError, TypeError):
        return None


# Survival states with negative-evidence weight: a world claiming these MUST
# show them in trail search — they are what the lineage marketing claims rest
# on. alive_* / unknown legitimately show zero search rows pre-maturation, so
# probing them via search would conflate the maturation lifecycle with lying.
TRUST_CRITICAL_SURVIVAL = frozenset(
    {"reverted", "lost", "partially_preserved", "repaired", "alive_transformed"}
)


def _probe_survival_states(driver, box, value) -> ProbeResult:
    states = [s for s in (str(x) for x in (value or [])) if s in TRUST_CRITICAL_SURVIVAL]
    if not states:
        return True, "no trust-critical survival states requested"
    for state in states:
        doc = _cli_json(driver, box, "trail", "search", "--survival", state)
        count = (doc or {}).get("result_count", 0) if isinstance(doc, dict) else 0
        if not count:
            return False, (
                f"survival state {state!r} declared but the live substrate has "
                f"zero matching trails (result_count={count})"
            )
    return True, f"live substrate confirms survival states {states}"


def _probe_min_captured_traces(driver, box, value) -> ProbeResult:
    # Measured via the Trace Index (what "captured" MEANS to consumers),
    # not bucket manifest envelopes: restored worlds have a known
    # manifest-projection gap (issue #25 — tick-reconciled traces are
    # queryable but absent from manifest.traces), and the probe must not
    # conflate that open product bug with "the world has no traces".
    need = int(value)
    doc = _cli_json(driver, box, "trace", "query", "--since", "3650d")
    cands = (doc or {}).get("candidates", []) if isinstance(doc, dict) else []
    have = len(cands) if isinstance(cands, list) else 0
    if have >= need:
        return True, f"trace index holds {have} candidate(s) >= {need}"
    return False, f"trace index holds {have} candidate(s), precondition needs {need}"


def _probe_context_tree_built(driver, box, value) -> ProbeResult:
    # Measured via `ctx tree` (event-log-backed), NOT `ctx list`
    # (manifest-only): restored worlds have the open manifest-projection
    # gap (issue #25) where ctx tree works while ctx list shows nothing.
    if not value:
        return True, "context_tree_built not requested"
    q = _cli_json(driver, box, "trace", "query", "--since", "3650d")
    cands = (q or {}).get("candidates", []) if isinstance(q, dict) else []
    for cand in cands[:3]:
        trace_id = cand.get("trace_id") if isinstance(cand, dict) else None
        if not trace_id:
            continue
        tree = _cli_json(driver, box, "ctx", "tree", str(trace_id))
        nodes = (tree or {}).get("nodes") or (tree or {}).get("tree") or []
        if nodes:
            return True, f"ctx tree for {str(trace_id)[:12]} has context nodes"
    return False, (
        f"no context nodes found via ctx tree across {min(len(cands), 3)} "
        f"candidate trace(s)"
    )


def _probe_otlp_receiver_running(driver, box, value) -> ProbeResult:
    if not value:
        return True, "otlp_receiver_running not requested"
    doc = _cli_json(driver, box, "capture-otlp", "status")
    running = bool((doc or {}).get("running")) if isinstance(doc, dict) else False
    if running:
        return True, "otlp receiver reports running"
    return False, "otlp receiver is not running"


def _probe_branch_commits_min(driver, box, value) -> ProbeResult:
    # Counts world history (rev-list HEAD), matching the provides semantic
    # ("the world carries >= N commits") — NOT main..HEAD, which counts a
    # different thing (commits ahead of base) and mis-measured both the
    # pr-branch and pi worlds when first tried.
    need = int(value)
    project = str(box.root / "project")
    res = driver.exec(box, ["git", "-C", project, "rev-list", "--count", "HEAD"])
    have = int(res.stdout.strip() or 0) if res.returncode == 0 else 0
    if have >= need:
        return True, f"world history carries {have} commit(s) >= {need}"
    return False, f"world history carries {have} commit(s), precondition needs {need}"


PROBES: dict[str, Callable] = {
    "requires_survival_states": _probe_survival_states,
    "min_captured_traces": _probe_min_captured_traces,
    "context_tree_built": _probe_context_tree_built,
    "otlp_receiver_running": _probe_otlp_receiver_running,
    "requires_branch_commits_min": _probe_branch_commits_min,
}


def run_probes(driver, box, preconditions: dict) -> list[tuple[str, bool, str]]:
    """Run every registered probe for the journey's precondition keys.

    Returns ``(key, ok, message)`` triples; keys without a registered probe
    are silently not probed here (the static resolver still applies).
    """
    out = []
    for key, value in (preconditions or {}).items():
        probe = PROBES.get(key)
        if probe is None:
            continue
        try:
            ok, message = probe(driver, box, value)
        except Exception as exc:  # noqa: BLE001 — a crashing probe is a failed probe
            ok, message = False, f"probe error: {exc}"
        out.append((key, ok, message))
    return out
