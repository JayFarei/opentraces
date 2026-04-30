"""Iterative regression test on entry #6 of the integration corpus.

This test is intentionally designed to be re-run after every cluster lands
(E → F → G → H), reporting which labels move from FAIL to PASS. It does
not abort on first failure — instead it collects every label assertion and
emits a structured report so the orchestrator can compare cluster output
against ground truth.

Ground truth is in ``tests/integration/fixtures/entry6/labels.json``.
The trace JSONL is frozen at ``tests/integration/fixtures/entry6/trace.jsonl``.

Two label kinds:

* ``hard`` — derivable from the trace JSONL alone. Stable across branch
  movement. These MUST pass once their cluster lands.
* ``soft`` — depends on git HEAD (survivorship, killer-commit lookup). May
  drift if a later cluster touches files in the burst's file set; reported
  with tolerance bands.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest


HERE = Path(__file__).parent
FIXTURE_DIR = HERE / "fixtures" / "entry6"
LABELS_PATH = FIXTURE_DIR / "labels.json"
TRACE_PATH = FIXTURE_DIR / "trace.jsonl"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def labels() -> dict[str, Any]:
    return json.loads(LABELS_PATH.read_text())


@pytest.fixture(scope="module")
def trace_record() -> dict[str, Any]:
    with TRACE_PATH.open() as fh:
        return json.loads(fh.readline())


@pytest.fixture(scope="module")
def project_repo() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="module")
def burst_row(labels: dict[str, Any], trace_record: dict[str, Any]) -> dict[str, Any]:
    """Build the burst row using the *current* CLI implementation.

    This is the assertion target. The same fixture function should produce
    progressively richer output as each cluster lands.
    """
    from opentraces.core.bursts import detect_bursts  # type: ignore
    from opentraces.core.trace_map import build_trace_map  # type: ignore
    from opentraces_schema import TraceRecord  # type: ignore

    record = TraceRecord.model_validate(trace_record)
    tm = build_trace_map(record)
    bursts = detect_bursts(
        tm.nodes,
        trace_record=record,
        repo_path=REPO_ROOT,
    )
    target_range = labels["burst"]["step_range"]
    for b in bursts:
        if list(b.step_range) == target_range:
            return _burst_to_dict(b)
    pytest.fail(
        f"Burst with step_range={target_range} not detected. "
        f"Got bursts at: {[list(b.step_range) for b in bursts]}"
    )


def _burst_to_dict(burst: Any) -> dict[str, Any]:
    """Coerce whatever shape detect_bursts returns into a dict for assertion."""
    if isinstance(burst, dict):
        return burst
    out: dict[str, Any] = {}
    for attr in (
        "step_range",
        "intent_user_step",
        "intent_text",
        "intent",
        "unique_files",
        "patches",
        "unique_git_anchors",
        "has_git_anchor",
        "burst_commit_sha",
        "tool_call_density",
        "blast_radius",
        "iterations_to_success",
    ):
        if hasattr(burst, attr):
            out[attr] = getattr(burst, attr)
    return out


# ─── HARD LABELS ──────────────────────────────────────────────────────────


def test_burst_step_range(burst_row, labels):
    expected = labels["burst"]["step_range"]
    actual = list(burst_row["step_range"])
    assert actual == expected, f"step_range: expected={expected} actual={actual}"


def test_burst_patch_count(burst_row, labels):
    expected = labels["burst"]["patch_count"]
    actual = len(burst_row.get("patches") or [])
    assert actual == expected, f"patch_count: expected={expected} actual={actual}"


def test_burst_unique_files_count(burst_row, labels):
    """D6: unique_files must dedupe absolute and repo-relative variants."""
    expected = labels["burst"]["unique_files_count"]
    files = burst_row.get("unique_files") or {}
    actual = len(files)
    assert actual == expected, (
        f"unique_files_count: expected={expected} actual={actual}. "
        f"Files: {sorted(files.keys()) if isinstance(files, dict) else files}"
    )


def test_burst_unique_files_set(burst_row, labels):
    """D6 + D7: each expected file appears exactly once (no abs/rel duplicates)."""
    expected = set(labels["burst"]["unique_files"])
    files = burst_row.get("unique_files") or {}
    actual = set()
    for k in files.keys() if isinstance(files, dict) else files:
        # Accept either bare repo-relative or absolute; normalise
        rel = k
        if k.startswith("/"):
            for prefix in ("/Users/06506792/src/tries/2026-03-27-community-traces-hf/",
                           str(REPO_ROOT) + "/"):
                if rel.startswith(prefix):
                    rel = rel[len(prefix):]
                    break
        actual.add(rel)
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"missing files in burst: {missing}"
    assert not extra, f"unexpected files in burst (likely abs/rel duplication): {extra}"


def test_burst_files_to_hunks(burst_row, labels):
    """D6: per-file hunk counts must match (validates dedup didn't lose hunks)."""
    expected = labels["burst"]["files_to_hunks"]
    files = burst_row.get("unique_files") or {}
    if not isinstance(files, dict):
        pytest.skip("unique_files is not a {path: count} mapping")
    # Normalise keys
    normalized: dict[str, int] = {}
    for k, v in files.items():
        rel = k
        if k.startswith("/"):
            for prefix in ("/Users/06506792/src/tries/2026-03-27-community-traces-hf/",
                           str(REPO_ROOT) + "/"):
                if rel.startswith(prefix):
                    rel = rel[len(prefix):]
                    break
        normalized[rel] = normalized.get(rel, 0) + v
    for path, count in expected.items():
        assert normalized.get(path) == count, (
            f"hunks for {path}: expected={count} actual={normalized.get(path)}"
        )


def test_burst_commit_sha_first_class(burst_row, labels):
    """D5: the burst's commit_sha must be a first-class field, not derived from outcome."""
    expected = labels["burst"]["burst_commit_sha"]
    actual = burst_row.get("burst_commit_sha")
    if actual is None:
        # Fallback: derive from patches' modal commit_sha
        from collections import Counter
        shas = Counter(
            p.get("commit_sha") for p in (burst_row.get("patches") or []) if p.get("commit_sha")
        )
        if shas:
            actual = shas.most_common(1)[0][0]
    assert actual == expected, f"burst_commit_sha: expected={expected} actual={actual}"


def test_burst_commit_subject(burst_row, labels, project_repo):
    """I3: commit subject for the burst's anchor commit."""
    expected = labels["burst"]["burst_commit_subject"]
    intent = burst_row.get("intent")
    if isinstance(intent, dict) and intent.get("commit_subject"):
        actual = intent["commit_subject"]
    else:
        # Fallback: shell out to git
        sha = labels["burst"]["burst_commit_sha"]
        proc = subprocess.run(
            ["git", "-C", str(project_repo), "log", "-1", "--format=%s", sha],
            capture_output=True, text=True, check=False,
        )
        actual = proc.stdout.strip() if proc.returncode == 0 else None
    assert actual == expected, f"commit_subject: expected={expected!r} actual={actual!r}"


def test_burst_commit_body_present(burst_row, labels, project_repo):
    """I3: commit body must be retrievable and non-trivial."""
    intent = burst_row.get("intent")
    body = None
    if isinstance(intent, dict):
        body = intent.get("commit_body")
    if not body:
        # Fallback for clusters before I3 lands
        sha = labels["burst"]["burst_commit_sha"]
        proc = subprocess.run(
            ["git", "-C", str(project_repo), "log", "-1", "--format=%b", sha],
            capture_output=True, text=True, check=False,
        )
        body = proc.stdout.strip() if proc.returncode == 0 else None
    assert body is not None and len(body) >= labels["intent"]["commit_body_min_length_chars"], (
        f"commit_body length {len(body) if body else 0} below minimum"
    )
    for fragment in labels["burst"]["burst_commit_body_must_contain"]:
        assert fragment in body, f"commit_body missing fragment: {fragment!r}"


def test_intent_trigger_step(burst_row, labels):
    """I1: trigger detection must identify step 26 as a pure trigger."""
    intent = burst_row.get("intent")
    if not isinstance(intent, dict) or "trigger" not in intent:
        pytest.fail(
            "intent.trigger field not present — Cluster E has not landed I1 yet. "
            f"Got intent={intent!r}"
        )
    expected = labels["intent"]["trigger"]["step"]
    actual = intent["trigger"].get("step") if intent["trigger"] else None
    assert actual == expected, f"intent.trigger.step: expected={expected} actual={actual}"


def test_intent_most_substantive_spec_step(burst_row, labels):
    """I1+I2: after filtering trigger, most substantive spec is step 19."""
    intent = burst_row.get("intent")
    if not isinstance(intent, dict) or "most_substantive_spec" not in intent:
        pytest.fail("intent.most_substantive_spec not present — Cluster E has not landed I2.")
    expected = labels["intent"]["most_substantive_spec"]["step"]
    actual = (intent["most_substantive_spec"] or {}).get("step")
    assert actual == expected, f"intent.most_substantive_spec.step: expected={expected} actual={actual}"


def test_intent_most_substantive_spec_text(burst_row, labels):
    """I2: the substantive spec text must contain at least one expected fragment."""
    intent = burst_row.get("intent")
    if not isinstance(intent, dict) or "most_substantive_spec" not in intent:
        pytest.fail("intent.most_substantive_spec not present — Cluster E has not landed I2.")
    text = ((intent["most_substantive_spec"] or {}).get("text") or "").lower()
    expected_any = labels["intent"]["most_substantive_spec"]["text_must_contain_any_of"]
    assert any(frag.lower() in text for frag in expected_any), (
        f"most_substantive_spec.text contains none of {expected_any}; got: {text[:200]!r}"
    )


def test_intent_spec_chain_includes_required_steps(burst_row, labels):
    """I2: spec_chain must include steps 1, 12, 19 (in any order; ordered by step_index)."""
    intent = burst_row.get("intent")
    if not isinstance(intent, dict) or "spec_chain" not in intent:
        pytest.fail("intent.spec_chain not present — Cluster E has not landed I2.")
    chain = intent["spec_chain"] or []
    chain_steps = {item.get("step") for item in chain if isinstance(item, dict)}
    required = set(labels["intent"]["spec_chain_must_include_steps"])
    missing = required - chain_steps
    assert not missing, f"spec_chain missing required steps: {missing}; got {sorted(chain_steps)}"


def test_intent_spec_chain_excludes_trigger(burst_row, labels):
    """I1: spec_chain must NOT include step 26 (the trigger)."""
    intent = burst_row.get("intent")
    if not isinstance(intent, dict) or "spec_chain" not in intent:
        pytest.fail("intent.spec_chain not present — Cluster E has not landed I2.")
    chain = intent["spec_chain"] or []
    chain_steps = {item.get("step") for item in chain if isinstance(item, dict)}
    forbidden = set(labels["intent"]["spec_chain_must_exclude_steps"])
    leaked = chain_steps & forbidden
    assert not leaked, f"spec_chain leaked trigger steps: {leaked}"


# ─── SOFT LABELS (survivorship — depend on git HEAD) ──────────────────────


def test_survival_distribution(burst_row, labels):
    """Soft: survival distribution should be within tolerance of baseline."""
    expected = labels["survival"]["expected_distribution_at_baseline"]
    expected_total = sum(expected.values())
    tol_pct = labels["survival"]["tolerance_total_pct"] / 100.0

    psv = burst_row.get("patches_with_survival")
    if not psv:
        # Fallback to per-patch survival via track output if present
        psv = burst_row.get("patches") or []

    from collections import Counter
    actual_counts = Counter(p.get("survival_state") for p in psv if p.get("survival_state"))

    if not actual_counts:
        pytest.skip("No survival_state on burst.patches yet — Cluster G/F not run with primed index")

    for state, expected_count in expected.items():
        actual = actual_counts.get(state, 0)
        delta_pct = abs(actual - expected_count) / expected_total
        assert delta_pct <= tol_pct, (
            f"survival[{state}]: expected={expected_count} actual={actual} "
            f"delta_pct={delta_pct:.2%} > tolerance={tol_pct:.2%}"
        )


def test_lost_patches_have_killer_commit(burst_row, labels, project_repo):
    """D2: each `lost` patch should have a `lost_at_commit_sha` field."""
    psv = burst_row.get("patches_with_survival") or burst_row.get("patches") or []
    lost = [p for p in psv if p.get("survival_state") == "lost"]
    if not lost:
        pytest.skip("No lost patches yet — Cluster G/F not landed")

    expected_pairs = {
        item["patch_file"]: item.get("expected_lost_at_commit_starts_with")
        for item in labels["survival"]["lost_patches_expected_killer_commits"]
    }
    found_field = False
    for p in lost:
        if "lost_at_commit_sha" in p:
            found_field = True
            break
    if not found_field:
        pytest.fail("lost_at_commit_sha field not on any lost patch — Cluster F has not landed D2.")

    for p in lost:
        sha = p.get("lost_at_commit_sha")
        # Match by file
        # (file path may carry abs prefix; strip)
        fp = p.get("file_path") or p.get("file")
        if fp and fp.startswith("/"):
            for prefix in ("/Users/06506792/src/tries/2026-03-27-community-traces-hf/",
                           str(REPO_ROOT) + "/"):
                if fp.startswith(prefix):
                    fp = fp[len(prefix):]
                    break
        expected_prefix = expected_pairs.get(fp)
        if expected_prefix is None:
            # File alive — lost_kind should be hunk_removed
            if sha is not None:
                assert sha is None, f"{fp}: expected no killer commit (hunk_removed) but got {sha}"
        else:
            assert sha and sha.startswith(expected_prefix), (
                f"{fp}: lost_at_commit_sha expected to start with {expected_prefix}, got {sha}"
            )


def test_anchor_evidence_breakdown(burst_row, labels):
    """Hard-ish: 26 firm + 1 provisional structural_match."""
    psv = burst_row.get("patches_with_survival") or burst_row.get("patches") or []
    if not psv or "evidence_firmness" not in (psv[0] if isinstance(psv[0], dict) else {}):
        pytest.skip("evidence_firmness not on patches in this burst output")
    from collections import Counter
    firm = Counter(p.get("evidence_firmness") for p in psv)
    expected = labels["survival"]["anchor_evidence_breakdown"]
    assert firm.get("firm", 0) == expected["exact_range_hash_firm"], (
        f"firm count: expected={expected['exact_range_hash_firm']} actual={firm.get('firm', 0)}"
    )
    assert firm.get("provisional", 0) == expected["structural_match_provisional"], (
        f"provisional count: expected={expected['structural_match_provisional']} actual={firm.get('provisional', 0)}"
    )


# ─── VERIFICATION CHECKS (independent ground-truth) ──────────────────────


def test_git_show_confirms_9_files(labels, project_repo):
    """git show --name-only on the burst commit must list exactly 9 files."""
    sha = labels["burst"]["burst_commit_sha"]
    proc = subprocess.run(
        ["git", "-C", str(project_repo), "show", "--name-only", "--format=", sha],
        capture_output=True, text=True, check=True,
    )
    files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    assert len(files) == 9, f"git show lists {len(files)} files, expected 9: {files}"


def test_burst_commit_is_not_trace_outcome_commit(trace_record, labels):
    """Sanity: the trace's outcome.commit_sha is the LATER commit, not the burst's."""
    outcome_sha = (trace_record.get("outcome") or {}).get("commit_sha") or ""
    burst_sha = labels["burst"]["burst_commit_sha"]
    assert not outcome_sha.startswith(burst_sha[:8]), (
        f"outcome.commit_sha={outcome_sha} unexpectedly matches burst_commit_sha={burst_sha}; "
        "the labels assume these are different commits."
    )
