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


def _count_bucket_trace_dirs(driver, box) -> int:
    """Ground-truth captured-trace count: the number of traces registered in
    the project ``state.json`` file(s) — the SAME source the checkpoint audit
    reads, and the registry of captured traces regardless of bucket LAYOUT.

    Why not the obvious surfaces:
    - Trace Index: a lazily-built SQLite projection, EMPTY at cold-build time,
      inconsistent across machines (the first on-main nightly failed every
      verify_provides on it).
    - Bucket trace dirs (traces/v1): correct for v2 worlds but ZERO for the
      legacy 0.3.3 world (c-legacy-v033), whose traces live in the old
      projects/<slug>/traces/*.jsonl layout — surfaced by the next nightly.
    - Bucket manifest: the manifest-projection gap (issue #25).
    state.json covers BOTH layouts and is written the instant capture lands;
    json is stdlib so any box python reads it.
    """
    cmd = (
        'total=0; for f in $(find "$HOME/.opentraces/projects" -name state.json '
        '2>/dev/null); do '
        'n=$(python3 -c "import json,sys; '
        'print(len(json.load(open(sys.argv[1])).get(\'traces\',{})))" "$f" '
        '2>/dev/null || echo 0); total=$((total+n)); done; echo $total'
    )
    res = driver.exec(box, ["bash", "-lc", cmd])
    try:
        return int((res.stdout or "").strip() or 0)
    except (ValueError, AttributeError):
        return 0


def _probe_min_captured_traces(driver, box, value) -> ProbeResult:
    need = int(value)
    have = _count_bucket_trace_dirs(driver, box)
    if have >= need:
        return True, f"bucket holds {have} captured trace(s) >= {need}"
    return False, f"bucket holds {have} captured trace(s), precondition needs {need}"


def _probe_context_tree_built(driver, box, value) -> ProbeResult:
    # Ground truth: a context companion in the bucket, NOT the Trace Index
    # (lazily built, empty at cold-build time) nor `ctx list` (manifest gap,
    # issue #25). A captured world with context has context.jsonl.gz / a
    # context blob under the bucket — filesystem-checkable on any platform.
    if not value:
        return True, "context_tree_built not requested"
    cmd = (
        'find "$HOME/.opentraces/bucket" \\( -name "context.jsonl.gz" '
        '-o -path "*context*" -name "*.json.gz" \\) 2>/dev/null | head -1'
    )
    res = driver.exec(box, ["bash", "-lc", cmd])
    if (res.stdout or "").strip():
        return True, "bucket holds a context companion"
    return False, "no context companion found in the bucket"


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
