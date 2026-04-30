"""Generator for the burst-calibration corpus v1.

Cluster H — produces a small set of hand-labeled synthetic traces under
``tests/integration/fixtures/burst_calibration_corpus/v1/`` for the
calibration test (``tests/integration/test_burst_calibration.py``).

Each trace is intentionally minimal — only the fields ``build_trace_map``
+ ``detect_bursts`` consume. We do NOT emit ``patch_created`` nodes
(that would require a real Git anchor pipeline); the burst detector
falls back to synthesizing patch entries from ``file_edit`` nodes,
which is the correct shape for these calibration scenarios.

Run: ``.venv/bin/python tests/integration/harness/burst_calibration_corpus.py``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CORPUS_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "burst_calibration_corpus"
    / "v1"
)


def _agent() -> dict[str, Any]:
    return {
        "name": "claude-code",
        "version": "calibration",
        "model": "anthropic/claude-opus-4-7",
    }


def _step(
    step_index: int,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "role": role,
        "content": content,
        "tool_calls": tool_calls or [],
    }


def _edit_call(call_id: str, file_path: str, old_string: str, new_string: str) -> dict[str, Any]:
    return {
        "tool_call_id": call_id,
        "tool_name": "Edit",
        "input": {
            "file_path": file_path,
            "old_string": old_string,
            "new_string": new_string,
        },
    }


def _record(
    trace_id: str,
    description: str,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "0.3.0",
        "trace_id": trace_id,
        "session_id": f"sess-{trace_id}",
        "agent": _agent(),
        "task": {
            "description": description,
            "source": "user_prompt",
        },
        "steps": steps,
    }


def trace_001_single_burst_clean() -> dict[str, Any]:
    """8 file_edit nodes packed at steps 5..12, one burst.

    Story: agent renames a function across 8 hunks of 2 files in a tight
    sequence. No mid-burst user input, no pauses.
    """
    steps: list[dict[str, Any]] = [
        _step(1, "user", "Please rename `foo` to `bar` across the codebase."),
    ]
    for i, step_idx in enumerate([5, 6, 7, 8, 9, 10, 11, 12], start=1):
        steps.append(
            _step(
                step_idx,
                "agent",
                f"Renaming hunk {i}.",
                tool_calls=[
                    _edit_call(
                        f"tc{i}",
                        f"src/module_{(i % 2) + 1}.py",
                        f"foo_{i}",
                        f"bar_{i}",
                    )
                ],
            )
        )
    return _record(
        "calib-001",
        "Clean 8-hunk rename across two files; one burst.",
        steps,
    )


def trace_002_two_distinct_bursts() -> dict[str, Any]:
    """Two clearly separated bursts: edits at 5..10 and 80..85.

    Story: agent finishes one task (cache rewrite), user opens a second
    unrelated task (logging cleanup) ~70 steps later.
    """
    steps: list[dict[str, Any]] = [
        _step(1, "user", "Refactor the cache layer to use Redis."),
    ]
    for i, step_idx in enumerate([5, 6, 7, 8, 9, 10], start=1):
        steps.append(
            _step(
                step_idx,
                "agent",
                f"Cache rewrite step {i}.",
                tool_calls=[
                    _edit_call(
                        f"tc-cache-{i}",
                        f"src/cache.py",
                        f"old_cache_{i}",
                        f"redis_cache_{i}",
                    )
                ],
            )
        )
    # Long pause + new task
    steps.append(_step(70, "user", "Now strip out the legacy logging."))
    for i, step_idx in enumerate([80, 81, 82, 83, 84, 85], start=1):
        steps.append(
            _step(
                step_idx,
                "agent",
                f"Logging cleanup step {i}.",
                tool_calls=[
                    _edit_call(
                        f"tc-log-{i}",
                        f"src/logging.py",
                        f"legacy_log_{i}",
                        "",
                    )
                ],
            )
        )
    return _record(
        "calib-002",
        "Cache rewrite + logging cleanup; two distinct bursts.",
        steps,
    )


def trace_003_long_burst_with_user_pivot() -> dict[str, Any]:
    """Edits at 5..10 then 20..25, with a non-trigger user pivot at step 14.

    Story: agent starts a refactor, user redirects mid-flow ("actually
    let's also update the type hints"). The default heuristic keeps
    these as one burst (gap 10 ≤ 35); T9 splits them.
    """
    steps: list[dict[str, Any]] = [
        _step(1, "user", "Please update the cache module."),
    ]
    for i, step_idx in enumerate([5, 6, 7, 8, 9, 10], start=1):
        steps.append(
            _step(
                step_idx,
                "agent",
                f"Refactor cache step {i}.",
                tool_calls=[
                    _edit_call(
                        f"tc-pre-{i}",
                        f"src/cache.py",
                        f"foo_{i}",
                        f"bar_{i}",
                    )
                ],
            )
        )
    steps.append(
        _step(14, "user", "Actually, while you're at it, also update the type hints in helpers.py to use modern syntax."),
    )
    for i, step_idx in enumerate([20, 21, 22, 23, 24, 25], start=1):
        steps.append(
            _step(
                step_idx,
                "agent",
                f"Type-hint update step {i}.",
                tool_calls=[
                    _edit_call(
                        f"tc-post-{i}",
                        f"src/helpers.py",
                        f"List[str]_{i}",
                        f"list[str]_{i}",
                    )
                ],
            )
        )
    return _record(
        "calib-003",
        "Long burst with mid-flow user pivot; default 1 burst, T9 splits to 2.",
        steps,
    )


def trace_004_sparse_edits() -> dict[str, Any]:
    """3 edits across 200 steps — sparse session, adaptive gap should widen.

    Story: agent makes 3 widely-spaced edits in a long investigative
    session. With the default gap (35), each edit becomes its own
    burst (3 bursts). Adaptive gap (median ~95 × 4 = 380, clamped to
    100) widens the effective gap to 100, making this 1 burst.
    """
    steps: list[dict[str, Any]] = [
        _step(1, "user", "Investigate the slow query and fix it."),
    ]
    # 3 file_edit nodes at steps 50, 130, 200 — gaps of 80 and 70
    edits = [
        (50, "src/query.py", "slow_query_v1"),
        (130, "src/query.py", "slow_query_v2"),
        (200, "src/query.py", "slow_query_v3"),
    ]
    for i, (step_idx, fp, marker) in enumerate(edits, start=1):
        steps.append(
            _step(
                step_idx,
                "agent",
                f"Edit {i}.",
                tool_calls=[
                    _edit_call(
                        f"tc-sparse-{i}",
                        fp,
                        f"{marker}_old",
                        f"{marker}_new",
                    )
                ],
            )
        )
    return _record(
        "calib-004",
        "3 edits across 200 steps; one sparse burst.",
        steps,
    )


def trace_005_dense_loop() -> dict[str, Any]:
    """30 edits in 50 steps — dense iteration loop, must remain 1 burst.

    Story: agent runs a fix-up loop iterating on a single file. Edits
    are 1-2 steps apart. Naïve adaptive gap (median × 4 = ~5) would
    fragment this dramatically, but our floor at DEFAULT_BURST_GAP
    keeps it as one burst.
    """
    steps: list[dict[str, Any]] = [
        _step(1, "user", "Fix the failing test loop."),
    ]
    step_idx = 5
    for i in range(1, 31):
        steps.append(
            _step(
                step_idx,
                "agent",
                f"Iteration {i}.",
                tool_calls=[
                    _edit_call(
                        f"tc-dense-{i}",
                        "src/looper.py",
                        f"counter_{i}",
                        f"counter_{i + 1}",
                    )
                ],
            )
        )
        # 1-step or 2-step jumps
        step_idx += 1 if i % 2 == 0 else 2
    return _record(
        "calib-005",
        "30 dense edits in ~50 steps; one burst.",
        steps,
    )


TRACES: dict[str, callable] = {
    "trace_001_single_burst_clean": trace_001_single_burst_clean,
    "trace_002_two_distinct_bursts": trace_002_two_distinct_bursts,
    "trace_003_long_burst_with_user_pivot": trace_003_long_burst_with_user_pivot,
    "trace_004_sparse_edits": trace_004_sparse_edits,
    "trace_005_dense_loop": trace_005_dense_loop,
}


def labels() -> dict[str, Any]:
    """Ground-truth burst boundaries.

    ``expected_bursts`` is the list under default (T9 off) settings.
    ``expected_bursts_with_t9`` is the list under T9 on (used only
    where T9 changes the answer; otherwise omitted).
    """
    return {
        "_provenance": {
            "description": "Hand-labeled burst boundaries for the burst calibration corpus v1.",
            "harness": "tests/integration/harness/burst_calibration_corpus.py",
            "scope": "burst-detection only — no commit, anchor, or survival labels.",
        },
        "trace_001_single_burst_clean": {
            "expected_bursts": [
                {"step_range": [5, 12], "intent_summary": "rename foo->bar"},
            ],
        },
        "trace_002_two_distinct_bursts": {
            "expected_bursts": [
                {"step_range": [5, 10], "intent_summary": "cache rewrite"},
                {"step_range": [80, 85], "intent_summary": "logging cleanup"},
            ],
        },
        "trace_003_long_burst_with_user_pivot": {
            "expected_bursts": [
                {"step_range": [5, 25], "intent_summary": "cache + type hints (default merges)"},
            ],
            "expected_bursts_with_t9": [
                {"step_range": [5, 10], "intent_summary": "cache update"},
                {"step_range": [20, 25], "intent_summary": "type-hint update after pivot"},
            ],
        },
        "trace_004_sparse_edits": {
            "expected_bursts": [
                {"step_range": [50, 200], "intent_summary": "sparse investigation edits"},
            ],
        },
        "trace_005_dense_loop": {
            "expected_bursts": [
                {"step_range": [5, 49], "intent_summary": "dense fix-up loop"},
            ],
        },
    }


def main() -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    traces_dir = CORPUS_DIR / "traces"
    traces_dir.mkdir(exist_ok=True)
    for name, fn in TRACES.items():
        record = fn()
        path = traces_dir / f"{name}.jsonl"
        path.write_text(json.dumps(record) + "\n")
        size_kb = path.stat().st_size / 1024
        print(f"wrote {path.relative_to(CORPUS_DIR.parents[2])} ({size_kb:.1f} KB)")
    labels_path = CORPUS_DIR / "labels.json"
    labels_path.write_text(json.dumps(labels(), indent=2) + "\n")
    print(f"wrote {labels_path.relative_to(CORPUS_DIR.parents[2])}")


if __name__ == "__main__":
    main()
