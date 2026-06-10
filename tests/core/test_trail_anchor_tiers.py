"""Phase 5 anchor identity tiers — formatter divergence."""
from __future__ import annotations

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
