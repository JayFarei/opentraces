"""Phase 5 anchor identity tiers — formatter divergence."""
from __future__ import annotations

import difflib as _real_difflib
import subprocess
from pathlib import Path

from opentraces.core.trails import (
    GitObjectID,
    TrailEventDraft,
    append_event_batch,
    explain_trace_step,
    read_events,
    reconcile_commit_anchors,
)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _hash_object(repo: Path, content: str) -> str:
    proc = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=content,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _emit_hook_patch(
    repo: Path,
    *,
    trace_id: str,
    step_index: int,
    file_path: str,
    trace_patch_id: str,
    before_blob: GitObjectID,
    after_blob: GitObjectID,
    authored_text: str,
    affected_range: dict[str, int],
) -> None:
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                generation_index=0,
                step_index=step_index,
                capture_method=["hook_pretooluse", "hook_posttooluse"],
                payload={
                    "trace_patch_id": trace_patch_id,
                    "snapshot_before_id": "snapshot-pre",
                    "snapshot_after_id": "snapshot-post",
                    "file_path": file_path,
                    "affected_range": affected_range,
                    "authored_text": authored_text,
                    "raw_authored_hash": "sha256:fixture",
                    "git_clean_hash": "sha256:fixture",
                    "before_blob_id": before_blob.model_dump(mode="json"),
                    "after_blob_id": after_blob.model_dump(mode="json"),
                    "limitations": [],
                },
            )
        ],
        writer="capture-claude-code",
    )


def test_formatter_divergence_downgrades_firmness_not_identity(
    tmp_path: Path,
) -> None:
    """Plan §Phase 5 edge fixture #7.

    The hook captured the patch with single quotes. A formatter rewrote
    it to double quotes before the commit landed. Exact-range matching
    (whitespace-collapsed substring) fails because the quote characters
    differ. Structural matching (line-level similarity) still recognizes
    the change as the same patch with high confidence, so the anchor
    fires with ``evidence_tier=structural_match`` and downgraded
    ``evidence_firmness=provisional``. Identity (the link from
    Trace Patch to commit) is preserved; only the confidence is reduced.
    """
    _init_repo(tmp_path)
    target = tmp_path / "config.py"
    before_text = "GREETING = 'old'\n"
    target.write_text(before_text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed config"], cwd=tmp_path, check=True
    )
    before_blob = GitObjectID(hex=_hash_object(tmp_path, before_text))

    # Hook captured this exact authored text (single quotes).
    hook_authored = "GREETING = 'hello world'\n"
    after_blob = GitObjectID(hex=_hash_object(tmp_path, hook_authored))
    _emit_hook_patch(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        file_path="config.py",
        trace_patch_id="tracepatch-sha256:fixture-tr1-formatter",
        before_blob=before_blob,
        after_blob=after_blob,
        authored_text=hook_authored,
        affected_range={"start_line": 1, "end_line": 1},
    )

    # Formatter rewrote the same line with double quotes before commit.
    target.write_text('GREETING = "hello world"\n')
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "format quotes"], cwd=tmp_path, check=True
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    created = reconcile_commit_anchors(tmp_path, head)

    # Identity preserved — exactly one anchor.
    assert len(created) == 1
    anchor = created[0]
    assert anchor["trace_patch_id"] == "fixture-tr1-formatter"
    # Firmness downgraded.
    assert anchor["evidence_tier"] == "structural_match"
    assert anchor["evidence_firmness"] == "provisional"
    # Path and range are still recorded so trail sync / explain work.
    assert anchor["path"] == "config.py"
    assert anchor["range"]["start_line"] == 1
    assert anchor["range"]["end_line"] == 1
    # Limitation tag flags the divergence honestly.
    assert "structural_match_below_exact_threshold" in anchor["limitations"]

    # Search event records the algorithms tried, in order.
    events = read_events(tmp_path)
    search_events = [
        e for e in events if e.event_type == "git_anchor_search_completed"
    ]
    assert len(search_events) == 1
    algorithms = search_events[0].payload["algorithms_attempted"]
    assert "exact_range_hash" in algorithms
    assert "structural_match" in algorithms
    assert algorithms.index("exact_range_hash") < algorithms.index(
        "structural_match"
    ), "exact tier must be tried before structural fallback"

    # trail explain surfaces the downgraded firmness so consumers can filter.
    explanation = explain_trace_step(tmp_path, "tr1", 1)
    assert explanation["evidence_tier"] == "structural_match"
    assert explanation["evidence_firmness"] == "provisional"


def test_format_then_commit_records_provisional_firmness(tmp_path: Path) -> None:
    """Plan §Verification Strategy fixture: format-then-commit.

    The hook captured the patch with the agent's intended style. A
    formatter ran in pre-commit (or as part of a Makefile target) and
    rewrote the file with a structurally-different style. The commit
    contains the formatted version. The substrate must still anchor
    via the structural fallback with provisional firmness, so trace
    maturity reflects the divergence honestly.
    """
    _init_repo(tmp_path)
    target = tmp_path / "config.py"
    before_text = "GREETING = 'old'\n"
    target.write_text(before_text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed config"], cwd=tmp_path, check=True
    )
    before_blob = GitObjectID(hex=_hash_object(tmp_path, before_text))

    # Hook captured the agent's authored patch (single quotes,
    # single-line statement).
    hook_authored = "GREETING = 'hello world'\n"
    after_blob = GitObjectID(hex=_hash_object(tmp_path, hook_authored))
    _emit_hook_patch(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        file_path="config.py",
        trace_patch_id="tracepatch-sha256:fixture-format-then-commit",
        before_blob=before_blob,
        after_blob=after_blob,
        authored_text=hook_authored,
        affected_range={"start_line": 1, "end_line": 1},
    )

    # A formatter ran AFTER the hook and BEFORE the commit, rewriting
    # quote style. The committed blob is the formatter's output.
    target.write_text('GREETING = "hello world"\n')
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "format-then-commit"], cwd=tmp_path, check=True
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    created = reconcile_commit_anchors(tmp_path, head)
    assert len(created) == 1
    anchor = created[0]
    assert anchor["evidence_tier"] == "structural_match"
    assert anchor["evidence_firmness"] == "provisional"
    assert "structural_match_below_exact_threshold" in anchor["limitations"]


def test_unrelated_lines_do_not_anchor_via_structural_match(tmp_path: Path) -> None:
    """The structural fallback must not fabricate anchors.

    With a low-similarity hunk (the commit content has no meaningful
    overlap with the hook's authored text), reconcile_commit_anchors
    must record ``result=unknown`` rather than a low-confidence anchor.
    Orphan honesty trumps recall.
    """
    _init_repo(tmp_path)
    before_blob = GitObjectID(hex=_hash_object(tmp_path, "old\n"))
    after_blob = GitObjectID(hex=_hash_object(tmp_path, "new\n"))
    _emit_hook_patch(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        file_path="phantom.py",
        trace_patch_id="tracepatch-sha256:fixture-phantom",
        before_blob=before_blob,
        after_blob=after_blob,
        authored_text="completely_unique_authored_token_xyzzy\n",
        affected_range={"start_line": 1, "end_line": 1},
    )

    (tmp_path / "phantom.py").write_text("totally_different_content_qqqq\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "different commit"], cwd=tmp_path, check=True
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    created = reconcile_commit_anchors(tmp_path, head)
    assert created == []

    events = read_events(tmp_path)
    search_events = [
        e for e in events if e.event_type == "git_anchor_search_completed"
    ]
    assert len(search_events) == 1
    assert search_events[0].payload["results"][0]["result"] == "unknown"
    assert not any(e.event_type == "git_anchor_created" for e in events)


def test_before_blob_guard_rejects_revert_commit_as_landing(tmp_path: Path) -> None:
    """#32: a commit whose target-file blob equals the patch's pre-edit blob
    cannot be the patch's landing commit.

    A plain ``git revert`` lands a commit that restores the pre-edit content.
    Its diff re-introduces the OLD lines, which the structural matcher (line
    similarity >= 0.85) would otherwise mis-anchor — leaving the patch wrongly
    "alive_transformed" on the revert commit instead of resolving to
    ``reverted``. The before-blob guard rejects any match whose resolved blob
    equals ``before_blob_id``: no anchor is created and the search records
    ``result=unknown`` so the revert commit never claims the patch.

    Without the guard this test fails (an anchor on the revert commit is
    created via the structural fallback).
    """
    _init_repo(tmp_path)
    target = tmp_path / "greet.py"
    # The pre-edit content. Made deliberately close to the authored text so the
    # structural matcher WOULD fire on the revert (which re-adds this content)
    # if the guard were absent — this is what makes the guard load-bearing.
    before_text = "GREETING = 'hello world AAA'\n"
    target.write_text(before_text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed greet"], cwd=tmp_path, check=True
    )
    before_blob = GitObjectID(hex=_hash_object(tmp_path, before_text))

    # The agent's authored edit (one token differs from before_text -> the two
    # are > 0.85 similar under SequenceMatcher).
    hook_authored = "GREETING = 'hello world BBB'\n"
    after_blob = GitObjectID(hex=_hash_object(tmp_path, hook_authored))
    _emit_hook_patch(
        tmp_path,
        trace_id="tr1",
        step_index=1,
        file_path="greet.py",
        trace_patch_id="tracepatch-sha256:fixture-revert-guard",
        before_blob=before_blob,
        after_blob=after_blob,
        authored_text=hook_authored,
        affected_range={"start_line": 1, "end_line": 1},
    )

    # The landing commit applies the authored edit. This anchors normally.
    target.write_text(hook_authored)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "land greet"], cwd=tmp_path, check=True
    )
    landing = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    created_landing = reconcile_commit_anchors(tmp_path, landing)
    assert len(created_landing) == 1, "landing commit must anchor the patch"

    # Now git-revert the landing commit. The revert restores before_text, so
    # the revert commit's `greet.py` blob == before_blob_id, and its diff
    # re-adds the OLD (before) lines (> 0.85 similar to the authored text).
    subprocess.run(
        ["git", "revert", "--no-edit", landing], cwd=tmp_path, check=True
    )
    revert = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    # Sanity: the revert commit's target blob really is the pre-edit blob.
    revert_blob = subprocess.check_output(
        ["git", "rev-parse", f"{revert}:greet.py"], cwd=tmp_path, text=True
    ).strip()
    assert revert_blob == before_blob.hex

    created_revert = reconcile_commit_anchors(tmp_path, revert)
    # The guard rejects the revert commit as a landing commit: no anchor.
    assert created_revert == [], (
        "before-blob guard must reject the revert commit as the patch's "
        "landing commit"
    )

    events = read_events(tmp_path)
    revert_searches = [
        e
        for e in events
        if e.event_type == "git_anchor_search_completed"
        and (e.payload.get("search_head") or {}).get("hex") == revert
    ]
    assert len(revert_searches) == 1
    results = revert_searches[0].payload["results"]
    assert any(r["result"] == "unknown" for r in results)
    # No anchor was created on the revert commit.
    assert not any(
        e.event_type == "git_anchor_created"
        and (e.payload.get("commit_id") or {}).get("hex") == revert
        for e in events
    )


# --------------------------------------------------------------------------- #
# pkg-44 #44 — compute-gate equivalence in the structural matcher.
#
# These pin that the length-bound + quick_ratio gates are BEHAVIOR-PRESERVING:
# a real near-equal-length divergence still anchors with the SAME similarity,
# while a length-mismatched pair is rejected WITHOUT ever building a
# SequenceMatcher (the gate that rejected 100% of the 1,077 pairs in the
# 30-minute-hang incident).
# --------------------------------------------------------------------------- #


_REAL_SEQUENCE_MATCHER = _real_difflib.SequenceMatcher


class _CountingSequenceMatcher:
    """Wraps difflib.SequenceMatcher, counting constructions and ratio() calls.

    Used to prove the compute gates short-circuit BEFORE the expensive
    O(n*m) ratio() (and even before constructing the matcher). Holds a
    reference to the GENUINE class captured at import time so monkeypatching
    ``difflib.SequenceMatcher`` with this wrapper does not recurse.
    """

    constructed = 0
    ratio_calls = 0
    quick_ratio_calls = 0

    def __init__(self, *args, **kwargs):
        type(self).constructed += 1
        self._inner = _REAL_SEQUENCE_MATCHER(*args, **kwargs)

    def ratio(self):
        type(self).ratio_calls += 1
        return self._inner.ratio()

    def quick_ratio(self):
        type(self).quick_ratio_calls += 1
        return self._inner.quick_ratio()

    def real_quick_ratio(self):
        return self._inner.real_quick_ratio()

    @classmethod
    def reset(cls):
        cls.constructed = 0
        cls.ratio_calls = 0
        cls.quick_ratio_calls = 0


def _hunks_for(added_text: str, path: str = "config.py") -> dict:
    from opentraces.enrichment.attribution import _norm

    return {
        path: [
            {
                "added_text": added_text,
                "added_start": 1,
                "added_end": added_text.count("\n") or 1,
                "_norm_added": _norm(added_text),
            }
        ]
    }


def test_near_equal_length_pair_still_anchors_structural(monkeypatch):
    """A near-equal-length divergence (quote rewrite) that scores >= 0.85 must
    still anchor as structural_match with the SAME similarity value the
    un-gated matcher would have produced. Gate is behavior-preserving on the
    accept path."""
    from opentraces.core.trails import anchors as A

    authored = "GREETING = 'hello world AAAA'\n"
    added = 'GREETING = "hello world AAAA"\n'  # two chars differ; ~0.93 ratio
    patch = {"file_path": "config.py", "authored_text": authored}
    hunks = _hunks_for(added)

    # Baseline similarity from the raw matcher (no gates).
    import difflib

    raw_score = round(
        difflib.SequenceMatcher(None, authored, added, autojunk=False).ratio(), 4
    )
    assert raw_score >= A.STRUCTURAL_MATCH_THRESHOLD

    _CountingSequenceMatcher.reset()
    monkeypatch.setattr(A.difflib, "SequenceMatcher", _CountingSequenceMatcher)
    result = A._find_structural_anchor(patch, hunks)

    assert result is not None
    assert result["similarity"] == raw_score, (
        "gated structural match must report the identical similarity value"
    )
    # The length gate passed, so the matcher was built and ratio() computed.
    assert _CountingSequenceMatcher.constructed == 1
    assert _CountingSequenceMatcher.ratio_calls == 1


def test_tiny_patch_vs_huge_hunk_skipped_without_matcher(monkeypatch):
    """A short authored line against a huge hunk has a length ratio far below
    0.85, so it CANNOT score >= 0.85 (difflib bound:
    ratio() <= real_quick_ratio() == length ratio). The gate must reject it
    using pure arithmetic — NO SequenceMatcher is ever constructed."""
    from opentraces.core.trails import anchors as A

    authored = "x = 1\n"  # 6 chars
    added = ("y = 2  # padding line that makes this hunk enormous\n" * 40)
    patch = {"file_path": "config.py", "authored_text": authored}
    hunks = _hunks_for(added)

    _CountingSequenceMatcher.reset()
    monkeypatch.setattr(A.difflib, "SequenceMatcher", _CountingSequenceMatcher)
    result = A._find_structural_anchor(patch, hunks)

    assert result is None
    assert _CountingSequenceMatcher.constructed == 0, (
        "length-bound gate must reject before constructing any SequenceMatcher"
    )
    assert _CountingSequenceMatcher.ratio_calls == 0


def test_length_gate_pass_but_quick_ratio_fail_skips_full_ratio(monkeypatch):
    """A pair whose lengths are close (passes the arithmetic gate) but whose
    content has little real overlap fails quick_ratio() (the O(n) upper bound)
    and must NOT pay for the full O(n*m) ratio()."""
    from opentraces.core.trails import anchors as A

    # Equal length, fully disjoint character multisets -> length ratio == 1.0
    # (passes arithmetic gate) but quick_ratio() == 0.0 (< 0.85).
    authored = "aaaaaaaaaaaaaaaaaaaaaaaa\n"
    added = "bbbbbbbbbbbbbbbbbbbbbbbb\n"
    assert len(authored) == len(added)
    patch = {"file_path": "config.py", "authored_text": authored}
    hunks = _hunks_for(added)

    _CountingSequenceMatcher.reset()
    monkeypatch.setattr(A.difflib, "SequenceMatcher", _CountingSequenceMatcher)
    result = A._find_structural_anchor(patch, hunks)

    assert result is None
    # Matcher built once for the quick_ratio probe...
    assert _CountingSequenceMatcher.constructed == 1
    assert _CountingSequenceMatcher.quick_ratio_calls == 1
    # ...but the full ratio() was never computed.
    assert _CountingSequenceMatcher.ratio_calls == 0


def test_reconcile_memoizes_patch_id_and_oid_subprocesses(tmp_path, monkeypatch):
    """A reconcile creating multiple anchors on one commit must invoke
    ``git patch-id`` exactly ONCE (loop-invariant per (repo, commit)) and
    ``git rev-parse <commit>:<path>`` exactly once per DISTINCT path — not once
    per anchor."""
    from opentraces.core.trails import anchors as A

    _init_repo(tmp_path)

    # Three patches landing in the SAME file (one distinct path) plus one in a
    # second file -> 4 anchors over 2 distinct paths.
    files = {"a.py": "", "b.py": ""}
    for fn in files:
        (tmp_path / fn).write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed files"], cwd=tmp_path,
                   check=True)

    specs = [
        ("a.py", "ALPHA = 1\n"),
        ("a.py", "BETA = 2\n"),
        ("a.py", "GAMMA = 3\n"),
        ("b.py", "DELTA = 4\n"),
    ]
    new_content = {
        "a.py": "ALPHA = 1\nBETA = 2\nGAMMA = 3\n",
        "b.py": "DELTA = 4\n",
    }
    for i, (fp, authored) in enumerate(specs):
        after_blob = GitObjectID(hex=_hash_object(tmp_path, authored))
        _emit_hook_patch(
            tmp_path,
            trace_id=f"tr{i}",
            step_index=i + 1,
            file_path=fp,
            trace_patch_id=f"tracepatch-sha256:fixture-mem-{i}",
            before_blob=GitObjectID(hex=_hash_object(tmp_path, "seed\n")),
            after_blob=after_blob,
            authored_text=authored,
            affected_range={"start_line": 1, "end_line": 1},
        )
    for fn, content in new_content.items():
        (tmp_path / fn).write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "land four"], cwd=tmp_path,
                   check=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    counts = {"patch_id": 0, "rev_parse_blob": {}}
    real_run_git_until = A.run_git_until

    def _counting_run_git_until(args, *a, **k):
        try:
            if isinstance(args, (list, tuple)):
                if "patch-id" in args:
                    counts["patch_id"] += 1
                if "rev-parse" in args:
                    # rev-parse of a <commit>:<path> blob ref.
                    for tok in args:
                        if isinstance(tok, str) and tok.startswith(f"{head}:"):
                            path = tok.split(":", 1)[1]
                            counts["rev_parse_blob"][path] = (
                                counts["rev_parse_blob"].get(path, 0) + 1
                            )
        except Exception:
            pass
        return real_run_git_until(args, *a, **k)

    monkeypatch.setattr(A, "run_git_until", _counting_run_git_until)
    created = A.reconcile_commit_anchors(tmp_path, head)

    assert len(created) == 4, "all four patches must anchor"
    assert counts["patch_id"] == 1, (
        "git patch-id must run exactly once per reconcile (loop-invariant)"
    )
    # One rev-parse blob lookup per DISTINCT path, not per anchor.
    assert counts["rev_parse_blob"] == {"a.py": 1, "b.py": 1}, (
        f"oid cache must collapse per-path: {counts['rev_parse_blob']}"
    )
