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


def _probe_git_repo_present(driver, box, value) -> ProbeResult:
    if not value:
        return True, "git_repo_present not requested"
    project = str(box.root / "project")
    res = driver.exec(box, ["git", "-C", project, "rev-parse", "--git-dir"])
    if res.returncode == 0 and (res.stdout or "").strip():
        return True, "project is a git repo"
    return False, "project directory is not a git repo"


def _probe_post_commit_hook_installed(driver, box, value) -> ProbeResult:
    if not value:
        return True, "post_commit_hook_installed not requested"
    # The opentraces post-commit hook lives at .git/hooks/post-commit and
    # must reference opentraces (a bare sample hook would lie). Filesystem
    # check works on any platform and at cold-build time (no CLI needed).
    project = str(box.root / "project")
    cmd = (
        f'h="{project}/.git/hooks/post-commit"; '
        '[ -x "$h" ] && grep -qi opentraces "$h" && echo ok'
    )
    res = driver.exec(box, ["bash", "-lc", cmd])
    if (res.stdout or "").strip() == "ok":
        return True, "opentraces post-commit hook is installed + executable"
    return False, "no executable opentraces post-commit hook found"


def _probe_bucket_spine_v2_layout(driver, box, value) -> ProbeResult:
    if not value:
        return True, "bucket_spine_v2_layout not requested"
    # Ground truth: the v2 bucket layout has content-addressed blobs under
    # blobs/v1 AND the per-trace envelope tree under traces/v1. Filesystem
    # check (no CLI, no manifest dependency — the manifest is a lazy
    # projection, issue #25).
    cmd = (
        'b="$HOME/.opentraces/bucket"; '
        '[ -d "$b/blobs/v1" ] && [ -d "$b/traces/v1" ] && echo ok'
    )
    res = driver.exec(box, ["bash", "-lc", cmd])
    if (res.stdout or "").strip() == "ok":
        return True, "bucket carries the v2 blobs/v1 + traces/v1 layout"
    return False, "bucket is missing the v2 blobs/v1 + traces/v1 layout"


def _probe_events_mirror_v1_populated(driver, box, value) -> ProbeResult:
    if not value:
        return True, "events_mirror_v1_populated not requested"
    # The events mirror is events/v1/batches/*.jsonl.gz + index.json.
    cmd = (
        'b="$HOME/.opentraces/bucket/events/v1"; '
        '[ -f "$b/index.json" ] && '
        'ls "$b/batches/"*.jsonl.gz >/dev/null 2>&1 && echo ok'
    )
    res = driver.exec(box, ["bash", "-lc", cmd])
    if (res.stdout or "").strip() == "ok":
        return True, "bucket events/v1 mirror is populated"
    return False, "bucket events/v1 mirror is missing or empty"


def _probe_outlier_trail_companion_min_bytes(driver, box, value) -> ProbeResult:
    """Issue #213 world-honesty guard: the bucket's LARGEST ``trail.jsonl.gz``
    companion must be at least ``value`` bytes.

    A mature world that clones only manifest rows (no genuinely large outlier
    companion) would let pre-fix, O(companion) seal code short-circuit and
    stay green — the dishonest-world shape the red-proof exists to catch. This
    re-measures the real file on disk (find | sort -nr) rather than trusting
    the checkpoint's own claim.
    """
    need = int(value)
    # `wc -c < file` is byte size, portable across macOS/Linux (no `stat`
    # unit divergence) and per-file (no `wc` multi-file TOTAL line to mistake
    # for the largest). find -print0 handles any path.
    cmd = (
        'biggest=0; '
        'while IFS= read -r -d "" f; do '
        'n=$(wc -c < "$f" 2>/dev/null || echo 0); '
        '[ "$n" -gt "$biggest" ] && biggest=$n; '
        'done < <(find "$HOME/.opentraces/bucket/traces/v1" -name "trail.jsonl.gz" -print0 2>/dev/null); '
        'echo "$biggest"'
    )
    res = driver.exec(box, ["bash", "-lc", cmd])
    try:
        biggest = int((res.stdout or "").strip() or 0)
    except (ValueError, AttributeError):
        biggest = 0
    if biggest >= need:
        return True, f"largest trail companion is {biggest} bytes >= {need}"
    return False, (
        f"largest trail companion is {biggest} bytes, precondition needs >= {need} "
        f"(dishonest mature world — no genuine outlier companion)"
    )


PROBES: dict[str, Callable] = {
    "requires_survival_states": _probe_survival_states,
    "min_captured_traces": _probe_min_captured_traces,
    "outlier_trail_companion_min_bytes": _probe_outlier_trail_companion_min_bytes,
    "context_tree_built": _probe_context_tree_built,
    "otlp_receiver_running": _probe_otlp_receiver_running,
    "requires_branch_commits_min": _probe_branch_commits_min,
    "git_repo_present": _probe_git_repo_present,
    "post_commit_hook_installed": _probe_post_commit_hook_installed,
    "bucket_spine_v2_layout": _probe_bucket_spine_v2_layout,
    "events_mirror_v1_populated": _probe_events_mirror_v1_populated,
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
