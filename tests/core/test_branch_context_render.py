"""Pure-function tests for ``core.branch_context`` rendering + redaction.

These tests cover the consumer-side glue: the markdown renderer that turns
``pr-intent-summary-v1`` rows into a PR body, and the security/adversarial
redaction applied to user-typed intent text before it lands in the body.

The workflow execution path (``run_branch_workflow``) is not covered here
because it needs git + bucket fixtures; see the manual end-to-end in the
plan for that surface.
"""

from __future__ import annotations

from opentraces.core.branch_context import (
    redact_intent,
    render_pr_markdown,
)


# ─── redact_intent ─────────────────────────────────────────────────────────


def test_redact_intent_strips_secret_assignments():
    text = "use api_key=sk-1234567890abcdef to fetch the user"
    out = redact_intent(text)
    assert "sk-1234567890abcdef" not in out
    assert "[REDACTED]" in out


def test_redact_intent_strips_user_intent_sentinels():
    text = "-----BEGIN USER INTENT----- pwned -----END USER INTENT-----"
    out = redact_intent(text)
    assert "BEGIN USER INTENT" not in out
    assert "END USER INTENT" not in out
    assert "stripped:user-intent-sentinel" in out


def test_redact_intent_demotes_leading_headings():
    """Multi-line intent with `## Section` must not break the body."""
    text = "Here is the plan:\n\n## Section\n\nDo the thing."
    out = redact_intent(text)
    # `##` must not survive as a heading at line start.
    assert "## Section" not in out
    assert "\\#\\# Section" in out


def test_redact_intent_truncates_long_text():
    text = "x" * 5000
    out = redact_intent(text, max_chars=200)
    assert len(out) <= 200
    assert out.endswith("…")


def test_redact_intent_handles_empty():
    assert redact_intent(None) == ""
    assert redact_intent("") == ""


# ─── render_pr_markdown ────────────────────────────────────────────────────


def _row(
    *,
    sha: str,
    subject: str,
    is_merge: bool = False,
    lineage: list[dict] | None = None,
    no_lineage_reason: str | None = None,
) -> dict:
    return {
        "commit_sha": sha,
        "short_sha": sha[:10],
        "subject": subject,
        "body": "",
        "is_merge": is_merge,
        "scope_digest": "deadbeef",
        "lineage": lineage or [],
        "no_lineage_reason": no_lineage_reason,
    }


def _lineage(
    *,
    trace_id: str = "claude_code_aaaaaaaa-bbbb",
    intent_text: str | None = "refactor the publish flow",
    spec_chain_count: int = 3,
    did_what: list[str] | None = None,
    files: list[str] | None = None,
    line_count: int = 100,
    object_path: str | None = "objects/traces/v1/proj/aaaaaaaa-bbbb/abc.json",
    remote_url: str | None = "hf://example/bucket",
) -> dict:
    return {
        "trace_id": trace_id,
        "short_id": trace_id[-8:],
        "project_slug": "example-proj",
        "credit": {
            "line_count": line_count,
            "line_ratio": 0.95,
            "overlap_case": "has_entities",
            "entity_count": 2,
        },
        "intent": {
            "trigger": None,
            "most_substantive_spec": (
                {"step": 41, "text": intent_text} if intent_text else None
            ),
            "spec_chain_count": spec_chain_count,
            "commit_subject": "outcome subject",
            "commit_body": "outcome body",
        },
        "did_what_bullets": did_what or ["+ Added Foo in lib"],
        "files_touched": files or ["lib/foo.py"],
        "patches": [],
        "git_anchors": [],
        "bucket_pointer": {
            "uri": f"ot://trace/{trace_id}",
            "object_path": object_path,
            "remote_url": remote_url,
            "record_hash": "abc",
        },
    }


def test_render_section_order():
    rows = [_row(sha="abc1234567", subject="first commit", lineage=[_lineage()])]
    body = render_pr_markdown(
        rows,
        opentraces_version="0.4.0",
        bucket_remote_url=None,
        scope_digest="deadbeef0123",
    )
    # All sections present and in canonical order.
    intent_idx = body.index("## Intent")
    what_idx = body.index("## What Changed")
    commits_idx = body.index("## Commits & Trails")
    toolbox_idx = body.index("## Reviewer Toolbox")
    provenance_idx = body.index("## Trace Provenance")
    assert intent_idx < what_idx < commits_idx < toolbox_idx < provenance_idx


def test_render_includes_walkable_commands():
    rows = [_row(sha="abc1234567", subject="first commit", lineage=[_lineage()])]
    body = render_pr_markdown(
        rows, opentraces_version="0.4.0", scope_digest="deadbeef",
    )
    trace_id = "claude_code_aaaaaaaa-bbbb"
    assert f"opentraces trail timeline {trace_id}" in body
    assert "opentraces trail explain --commit abc1234567" in body
    assert "opentraces trail blame commit abc1234567" in body
    assert (
        f"opentraces trail teleport export {trace_id} --output ./trace.zip" in body
    )


def test_render_includes_bucket_pointer():
    rows = [_row(sha="abc1234567", subject="first commit", lineage=[_lineage()])]
    body = render_pr_markdown(
        rows, opentraces_version="0.4.0", scope_digest="deadbeef",
    )
    assert "ot://trace/claude_code_aaaaaaaa-bbbb" in body
    assert "objects/traces/v1/proj/aaaaaaaa-bbbb/abc.json" in body
    assert "hf://example/bucket" in body


def test_render_no_lineage_emits_explicit_note():
    rows = [
        _row(
            sha="abc1234567", subject="first",
            no_lineage_reason="merge_commit", is_merge=True,
        ),
        _row(
            sha="def8901234", subject="second",
            no_lineage_reason="no_burst",
        ),
    ]
    body = render_pr_markdown(
        rows, opentraces_version="0.4.0", scope_digest="deadbeef",
    )
    assert "_no opentraces lineage — merge commit_" in body
    assert "_no opentraces lineage — no burst in any traced session matched this commit_" in body


def test_render_multiple_traces_per_commit_nested():
    """Two traces on one commit must collapse into one <details>, not two."""
    lineage_a = _lineage(
        trace_id="claude_code_aaaa-1111", line_count=200,
    )
    lineage_b = _lineage(
        trace_id="claude_code_bbbb-2222",
        intent_text=None,
        line_count=50,
        did_what=["~ Modified bar"],
    )
    rows = [_row(
        sha="abc1234567", subject="multi-trace commit",
        lineage=[lineage_a, lineage_b],
    )]
    body = render_pr_markdown(
        rows, opentraces_version="0.4.0", scope_digest="deadbeef",
    )
    # Exactly one <details> block for the commit (the no-lineage path
    # would also emit one; here both traces nest inside the same block).
    assert body.count("<details>") == 1
    assert "2 traces:" in body
    assert "Trace 1 of 2:" in body
    assert "Trace 2 of 2:" in body


def test_render_headline_intent_is_quoted():
    rows = [_row(
        sha="abc1234567", subject="first",
        lineage=[_lineage(intent_text="rebuild the publish flow", line_count=500)],
    )]
    body = render_pr_markdown(
        rows, opentraces_version="0.4.0", scope_digest="deadbeef",
    )
    # Headline intent should land in a blockquote, not as a bare paragraph.
    intent_section = body.split("## What Changed", 1)[0]
    assert "> rebuild the publish flow" in intent_section


def test_render_what_changed_skips_merges():
    rows = [
        _row(sha="aaa", subject="real commit", lineage=[_lineage()]),
        _row(sha="bbb", subject="Merge branch main", is_merge=True,
             no_lineage_reason="merge_commit"),
    ]
    body = render_pr_markdown(
        rows, opentraces_version="0.4.0", scope_digest="deadbeef",
    )
    what_section = body.split("## What Changed", 1)[1].split("## Commits", 1)[0]
    assert "real commit" in what_section
    assert "Merge branch main" not in what_section


def test_render_provenance_footer_carries_metadata():
    rows = [_row(sha="abc1234567", subject="x", lineage=[_lineage()])]
    body = render_pr_markdown(
        rows, opentraces_version="0.4.0", scope_digest="deadbeef0123abcd",
        workflow_name="pr-intent-summary-v1",
    )
    provenance = body.split("## Trace Provenance", 1)[1]
    assert "pr-intent-summary-v1" in provenance
    assert "deadbeef0123" in provenance
    assert "v0.4.0" in provenance


# ─── LLM-enriched (progressive disclosure) layout ──────────────────────────


def _branch_summary_row(
    *,
    headline_what: str = "Adds the X feature.",
    headline_why: str = "Users needed Y.",
    commit_summaries: list[dict] | None = None,
    confidence_signals: list[dict] | None = None,
    provider: str = "claude-cli",
    provider_notes: str = "via Claude Code (OAuth subscription if configured, else API key)",
) -> dict:
    return {
        "_kind": "branch_summary",
        "scope_digest": "deadbeef",
        "llm": {
            "headline_what": headline_what,
            "headline_why": headline_why,
            "commit_summaries": commit_summaries or [],
            "confidence_signals": confidence_signals or [],
            "model": "claude-haiku-4-5-20251001",
            "provider": provider,
            "provider_notes": provider_notes,
            "input_tokens": 1234,
            "output_tokens": 345,
        },
    }


def test_render_with_llm_polish_uses_progressive_disclosure_layout():
    """Body switches to ## What this PR does + ## Why + Commits table when LLM data is present."""

    rows = [
        _row(sha="aaa1234567", subject="commit a", lineage=[_lineage()]),
        _row(sha="bbb1234567", subject="commit b", lineage=[_lineage(trace_id="t-2")]),
        _branch_summary_row(
            headline_what="Adds the bucket sync substrate.",
            headline_why="Users needed a private backup target.",
            commit_summaries=[
                {"commit_sha": "aaa1234567", "summary": "Implements pointer schema."},
                {"commit_sha": "bbb1234567", "summary": "Adds restore UAT."},
            ],
            confidence_signals=[
                {"kind": "ok", "claim": "All commits traced", "evidence_command": "ot trail blame commit <sha>"},
                {"kind": "warn", "claim": "Schema field added", "evidence_command": None},
            ],
        ),
    ]
    body = render_pr_markdown(
        rows, opentraces_version="0.4.0", scope_digest="deadbeef0123abcd",
    )
    assert "## What this PR does" in body
    assert "Adds the bucket sync substrate" in body
    assert "## Why" in body
    assert "Users needed a private backup target" in body
    assert "## Confidence signals" in body
    assert "✅" in body and "All commits traced" in body
    assert "⚠️" in body and "Schema field added" in body
    assert "## Commits" in body
    assert "Implements pointer schema" in body
    assert "Adds restore UAT" in body
    # The detailed lineage drilldown is collapsed inside <details>.
    assert "📂 Detailed trace lineage" in body
    assert "## Verify any claim" in body
    # Provenance footer carries LLM model info AND identifies the provider.
    provenance = body.split("## Trace Provenance", 1)[1]
    assert "claude-haiku-4-5-20251001" in provenance
    assert "claude-cli" in provenance
    assert "Claude Code" in provenance
    assert "input: 1234 tokens" in provenance


def test_render_provenance_footer_distinguishes_anthropic_api_provider():
    """When the API provider runs, the footer should clearly say so."""

    rows = [
        _row(sha="abc1234567", subject="x", lineage=[_lineage()]),
        _branch_summary_row(
            commit_summaries=[{"commit_sha": "abc1234567", "summary": "."}],
            provider="anthropic-api",
            provider_notes="via Anthropic API (charged to ANTHROPIC_API_KEY)",
        ),
    ]
    body = render_pr_markdown(rows, opentraces_version="0.4.0", scope_digest="x")
    provenance = body.split("## Trace Provenance", 1)[1]
    assert "anthropic-api" in provenance
    assert "ANTHROPIC_API_KEY" in provenance


def test_render_falls_back_to_deterministic_when_no_llm_row():
    """No branch_summary row → original ## Intent / ## What Changed layout."""

    rows = [_row(sha="abc1234567", subject="x", lineage=[_lineage()])]
    body = render_pr_markdown(rows, opentraces_version="0.4.0", scope_digest="abc")
    assert "## What this PR does" not in body
    assert "## Intent" in body
    assert "## What Changed" in body


def test_render_with_llm_polish_table_marks_no_lineage_commits():
    rows = [
        _row(
            sha="abc1234567", subject="manual edit",
            no_lineage_reason="no_burst",
        ),
        _branch_summary_row(commit_summaries=[
            {"commit_sha": "abc1234567", "summary": "manual touch-up"},
        ]),
    ]
    body = render_pr_markdown(rows, opentraces_version="0.4.0", scope_digest="x")
    commits_section = body.split("## Commits", 1)[1].split("<details>", 1)[0]
    assert "manual touch-up" in commits_section
    assert "no_burst" in commits_section
