"""Pilot + snapshot tests for the redesigned two-column TUI.

These tests build a synthetic staging dir with a few canned traces, mount the
``OpenTracesApp`` via Textual's ``run_test`` Pilot, and assert both behavior
(panels present, keybindings route correctly) and appearance (SVG snapshot
against the wireframe layout).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opentraces.clients.tui import OpenTracesApp
from opentraces.core.state import StateManager, TraceStatus


def _init_project(project: Path) -> None:
    """Write a minimal opt-in marker so core/config accepts the dir."""
    project.mkdir(parents=True, exist_ok=True)
    (project / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": "tui-test-0000",
        "review_policy": "review",
        "push_policy": "manual",
        "remotes": {"origin": {"url": "alice/opentraces", "visibility": "private"}},
        "active_remote": "origin",
        "agents": ["claude-code"],
    }))


def _make_trace(trace_id: str, description: str, *, steps: list[dict] | None = None,
                ts: str = "2026-01-15T10:00:00Z") -> dict:
    # The default timestamp is intentionally well in the past (> 2h), so the
    # "recently touched" glyph never renders in fixtures that omit ``ts``.
    # Tests that exercise the recently-touched path should pass an explicit
    # recent ``ts``.
    return {
        "trace_id": trace_id,
        "schema_version": "0.2.0",
        "timestamp_start": ts,
        "timestamp_end": ts,
        "agent": {"name": "claude-code", "model": "anthropic/claude-opus-4-6"},
        "task": {"description": description},
        "metrics": {"total_steps": len(steps or []),
                    "total_input_tokens": 1234, "total_output_tokens": 567,
                    "estimated_cost_usd": 0.042},
        "steps": steps or [
            {"role": "user", "content": "fix the parser", "timestamp": ts},
            {"role": "agent", "content": "let me inspect", "timestamp": ts,
             "tool_calls": [{"tool_name": "Read", "input": {"path": "parse.py"}}],
             "observations": [{"content": "200 lines of code", "status": "ok"}]},
            {"role": "agent", "content": "found it, patching", "timestamp": ts,
             "tool_calls": [{"tool_name": "Edit", "input": {"path": "parse.py"}}]},
        ],
    }


@pytest.fixture
def staged_app(tmp_path, monkeypatch):
    """Build a staging dir with one inbox, one staged, one pushed trace."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()

    traces = [
        _make_trace("trace_aaaaaa01", "fix the parser bug",
                    ts="2026-04-15T10:00:00Z"),
        _make_trace("trace_bbbbbb02", "add watcher daemon",
                    ts="2026-04-14T09:30:00Z"),
        _make_trace("trace_cccccc03", "upload to huggingface",
                    ts="2026-04-13T08:15:00Z"),
    ]
    for t in traces:
        (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")

    # Configure state: aaaaaa=inbox (default), bbbbbb=staged, cccccc=pushed.
    # Place state file inside the fake project so get_project_state_path
    # (which keys off cwd) finds it.
    monkeypatch.chdir(project)
    from opentraces.core.config import get_project_state_path
    state = StateManager(state_path=get_project_state_path(project))
    state.set_trace_status("trace_bbbbbb02", TraceStatus.COMMITTED)
    state.set_trace_status("trace_cccccc03", TraceStatus.UPLOADED)

    # Also give the project a remote so the info panel shows the arrow target.
    from opentraces.core.config import save_project_config
    save_project_config(project, {"remote": "alice/opentraces"})

    app = OpenTracesApp(staging_dir=staging, limit=100)
    return app, project, staging


# --- Behavior tests --------------------------------------------------------


@pytest.mark.asyncio
async def test_panels_mount_and_load(staged_app):
    app, _project, _staging = staged_app
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        # Four left panels + right header + stream + keybar all present.
        for sel in ("#info-panel", "#inbox-panel", "#staged-panel",
                    "#pushed-panel", "#trace-panel", "#trace-stream",
                    "#keybar"):
            assert app.query_one(sel) is not None, sel
        # Buckets populated correctly from state.
        assert len(app.by_stage["inbox"]) == 1
        assert len(app.by_stage["staged"]) == 1
        assert len(app.by_stage["pushed"]) == 1
        # Info panel shows project → remote.
        info = str(app.query_one("#info-body").render())
        assert "alice/opentraces" in info


@pytest.mark.asyncio
async def test_no_remote_shows_warning(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    _init_project(project)
    # Strip remote so the info panel falls through to the "no remote" branch.
    (project / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2", "project_id": "tui-test-0000",
        "review_policy": "review", "push_policy": "manual",
        "remotes": {}, "active_remote": None, "agents": ["claude-code"],
    }))
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        info = str(app.query_one("#info-body").render())
        assert "no remote" in info.lower()
        # Pushed panel subtitle nudges user to set a remote.
        panel = app.query_one("#pushed-panel")
        assert "remote" in (panel.border_subtitle or "").lower()


@pytest.mark.asyncio
async def test_space_moves_inbox_to_staged(staged_app):
    app, _project, _staging = staged_app
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        # Focus inbox, the first trace is aaaaaa01 (inbox).
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        # aaaaaa01 should now be in staged.
        assert any(t["trace_id"] == "trace_aaaaaa01"
                   for t in app.by_stage["staged"])
        assert not any(t["trace_id"] == "trace_aaaaaa01"
                       for t in app.by_stage["inbox"])


@pytest.mark.asyncio
async def test_visibility_badge_renders(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    from opentraces.core.config import save_project_config
    save_project_config(project, {"remote": "alice/opentraces",
                                  "visibility": "private"})
    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        info = str(app.query_one("#info-body").render())
        assert "alice/opentraces" in info
        assert "private" in info


@pytest.mark.asyncio
async def test_selection_style_is_persistent(staged_app):
    """Selected row keeps its background even when the list isn't focused."""
    app, _, _ = staged_app
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        # Focus inbox, navigate, then move focus away to the trace stream.
        await pilot.press("2")
        await pilot.pause()
        inbox = app.query_one("#inbox-list")
        selected_before = [c for c in inbox.children
                           if c.has_class("-highlight") or c.has_class("-selected")]
        assert selected_before, "inbox should have a highlighted row"
        await pilot.press("5")  # focus trace stream
        await pilot.pause()
        selected_after = [c for c in inbox.children
                          if c.has_class("-highlight") or c.has_class("-selected")]
        assert selected_after, "highlight must persist when focus moves away"


@pytest.mark.asyncio
async def test_single_active_selection_across_stages(staged_app):
    """Only one stage list carries a highlighted row at any time."""
    app, _, _ = staged_app
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        # Start by focusing inbox, then move focus to staged.
        await pilot.press("2")
        await pilot.pause()
        inbox = app.query_one("#inbox-list")
        staged = app.query_one("#staged-list")
        assert inbox.index is not None
        await pilot.press("3")
        await pilot.pause()
        assert staged.index is not None, "staged should gain a highlight"
        assert inbox.index is None, "inbox highlight should clear when focus moves"
        # Preview should reflect the staged trace now.
        assert app._current_trace is not None
        assert app._current_trace["trace_id"] == "trace_bbbbbb02"


@pytest.mark.asyncio
async def test_preview_scrolls_to_top_on_select(tmp_path, monkeypatch):
    """A freshly previewed trace starts at the top of the stream, not the tail."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    # A trace with enough steps that the stream definitely overflows the
    # viewport and would otherwise auto-scroll to the bottom.
    long_steps = []
    for i in range(30):
        long_steps.append({"role": "user",
                           "content": f"question {i}\n" * 4,
                           "timestamp": "2026-04-15T10:00:00Z"})
        long_steps.append({"role": "agent",
                           "content": f"answer {i}\n" * 4,
                           "timestamp": "2026-04-15T10:00:00Z"})
    t = _make_trace("trace_long_0001", "a long chat", steps=long_steps)
    (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")

    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        stream = app.query_one("#trace-stream")
        assert stream.scroll_y == 0, "preview should start at the top"


@pytest.mark.asyncio
async def test_bracket_keys_page_preview_from_inbox(tmp_path, monkeypatch):
    """``]`` and ``[`` page through the trace preview without requiring
    focus on the preview pane — you should be able to skim a long trace
    while still navigating the inbox list."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    long_steps = []
    for i in range(40):
        long_steps.append({"role": "user",
                           "content": f"line {i}\n" * 6,
                           "timestamp": "2026-04-15T10:00:00Z"})
        long_steps.append({"role": "agent",
                           "content": f"reply {i}\n" * 6,
                           "timestamp": "2026-04-15T10:00:00Z"})
    t = _make_trace("trace_pageable_001", "long", steps=long_steps)
    (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")

    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        await pilot.press("2")  # focus inbox, preview already populated
        await pilot.pause()
        stream = app.query_one("#trace-stream")
        inbox = app.query_one("#inbox-list")
        assert app.focused is inbox, "inbox should hold focus"
        assert stream.scroll_y == 0
        await pilot.press("right_square_bracket")
        await pilot.pause()
        first_jump = stream.scroll_y
        assert first_jump > 0, "] should scroll preview down"
        assert app.focused is inbox, "focus must stay on inbox"
        await pilot.press("right_square_bracket")
        await pilot.pause()
        assert stream.scroll_y > first_jump, "] should keep paging down"
        await pilot.press("left_square_bracket")
        await pilot.pause()
        assert stream.scroll_y < first_jump * 2, "[ should page back up"


@pytest.mark.asyncio
async def test_header_in_sums_input_and_cache_read(tmp_path, monkeypatch):
    """The trace header's ``in`` figure shows input + cache_read — the
    honest "tokens the model saw on your behalf" total. Previously we
    showed only the new/uncached input, which was suspiciously tiny for
    Claude Code sessions because almost all context comes through the
    prompt cache. A trailing ``(N% cached)`` tag surfaces the cache
    share so the user knows it's not all fresh input."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    t = _make_trace("trace_tokens_001", "token check")
    t["metrics"] = {
        "total_steps": 1,
        "total_input_tokens": 12345,
        "total_output_tokens": 6789,
        "total_cache_read_tokens": 999111,
        "total_cache_creation_tokens": 22222,
        "estimated_cost_usd": 1.23,
    }
    (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")
    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(180, 30)) as pilot:
        await pilot.pause()
        stream = app.query_one("#trace-stream")
        header = "".join(seg.text for strip in stream.lines[:2] for seg in strip)
        # in = 12345 + 999111 = 1,011,456.
        assert "1,011,456" in header, header
        assert "6,789" in header, header
        # Cache share renders as "(99% cached)" — 999111 / 1011456 ≈ 98.78%.
        assert "cached" in header, header


@pytest.mark.asyncio
async def test_blocked_trace_shows_in_inbox_with_red_dot(tmp_path, monkeypatch):
    """Blocked traces surface in the Inbox with a red dot and a callout
    explaining the verdict — they don't silently disappear."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    t = _make_trace("trace_blocked_001", "contains a password")
    t["metadata"] = {"llm_review": {
        "status": "complete",
        "shareable": "no",
        "missed_sensitive_data": "yes",
        "summary": "contains a password in step 4",
        "flagged_parts": [{"text": "password=hunter2", "reason": "credential"}],
    }}
    (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")
    from opentraces.core.config import get_project_state_path
    sm = StateManager(state_path=get_project_state_path(project))
    sm.block_trace("trace_blocked_001", "llm-review: contains a password")

    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        # Trace appears in Inbox bucket (not silently filtered out).
        assert any(tr["trace_id"] == "trace_blocked_001"
                   for tr in app.by_stage["inbox"])
        assert "trace_blocked_001" in app._blocked_ids
        # Callout at top of preview surfaces the reason.
        stream = app.query_one("#trace-stream")
        head = " ".join(seg.text for strip in stream.lines[:4] for seg in strip)
        assert "Blocked by LLM review" in head
        assert "password" in head


@pytest.mark.asyncio
async def test_security_info_surfaces_auto_redactions(tmp_path, monkeypatch):
    """Regression for a misleading "no findings" display. The capture
    pipeline writes ``security.flags_reviewed`` and ``redactions_applied``
    at the top level of the trace; the modal must surface those counts
    so the user doesn't assume the scanner did nothing."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    t = _make_trace("trace_redacted_001", "lots of auto-redactions")
    t["security"] = {
        "scanned": True,
        "flags_reviewed": 436,
        "redactions_applied": 45,
        "classifier_version": "0.4.0",
    }
    (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")

    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        text = str(app.screen.query_one("#security-modal-text").render())
        assert "45 auto-redacted" in text, text
        assert "436 reviewed" in text, text


@pytest.mark.asyncio
async def test_trufflehog_block_labels_correctly(tmp_path, monkeypatch):
    """A trace blocked by TruffleHog must say so — not "Blocked by LLM review".
    The ground truth is ``state.block_reason`` (TH writes its reason
    there, not into ``metadata.security``), so the preview callout
    and the modal's TruffleHog row both need to read from it."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    t = _make_trace("trace_th_blocked_001", "has a box api key")
    # Modern capture leaves metadata.security null when TH blocked inline.
    t["security"] = {"scanned": True, "flags_reviewed": 10,
                     "redactions_applied": 0, "classifier_version": "0.4.0"}
    (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")
    from opentraces.core.config import get_project_state_path
    sm = StateManager(state_path=get_project_state_path(project))
    sm.block_trace("trace_th_blocked_001", "TruffleHog: 1 finding(s) (Box)")

    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        # Preview callout — tier and detail come from block_reason.
        stream = app.query_one("#trace-stream")
        head = " ".join(seg.text for strip in stream.lines[:3] for seg in strip)
        assert "Blocked by TruffleHog" in head, head
        assert "Box" in head, head
        assert "Blocked by LLM review" not in head, head
        # Security modal — TruffleHog row surfaces the detail, LLM stays pending.
        await pilot.press("i")
        await pilot.pause()
        body = str(app.screen.query_one("#security-modal-text").render())
        assert "TruffleHog" in body
        # The dim detail with the vendor should appear on the TH row.
        assert "Box" in body
        # LLM review still correctly reads as not run.
        assert "LLM review" in body and "not run" in body


@pytest.mark.asyncio
async def test_i_key_toggles_security_modal(staged_app):
    """Pressing ``i`` a second time closes the modal instead of stacking a copy."""
    app, _, _ = staged_app
    from opentraces.clients.tui import SecurityInfoModal
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, SecurityInfoModal)
        await pilot.press("i")
        await pilot.pause()
        assert not isinstance(app.screen, SecurityInfoModal)


@pytest.mark.asyncio
async def test_security_info_modal_opens_with_pipeline(staged_app):
    """``i`` opens a modal that lays out the 4-tier pipeline."""
    app, _, _ = staged_app
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        # Modal screens live on top of the default screen, so query
        # through ``app.screen`` (the current top screen) rather than
        # ``app.query_one`` (which only hits the default).
        body = app.screen.query_one("#security-modal-text")
        text = str(body.render())
        # Every tier is named so the user can see pass/pending at a glance.
        for label in ("Regex / entropy", "TruffleHog",
                      "Manual review", "LLM review"):
            assert label in text, text


@pytest.mark.asyncio
async def test_help_overlay_is_centered(staged_app):
    """Help popup centers on screen rather than anchoring to top-left."""
    app, _, _ = staged_app
    width, height = 140, 40
    async with app.run_test(size=(width, height)) as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        card = app.query_one("#help-card")
        # Card should be visible and centered ± 1 cell of horizontal center.
        cx = card.region.x + card.region.width // 2
        screen_cx = width // 2
        assert abs(cx - screen_cx) <= 1, f"card center {cx} ≠ screen center {screen_cx}"
        # And vertically inside the viewport (not flush against the top).
        assert 1 < card.region.y, f"card flush at top: y={card.region.y}"


@pytest.mark.asyncio
async def test_discard_is_deferred_and_undoable(tmp_path, monkeypatch):
    """Discard hides the trace but keeps the file. Undo restores it."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    t = _make_trace("trace_doomed_001", "doomed")
    file_path = staging / f"{t['trace_id']}.jsonl"
    file_path.write_text(json.dumps(t) + "\n")
    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert any(tr["trace_id"] == "trace_doomed_001" for tr in app.by_stage["inbox"])
        await pilot.press("d")
        await pilot.pause()
        # Hidden from view but file untouched on disk.
        assert all(tr["trace_id"] != "trace_doomed_001" for tr in app.by_stage["inbox"])
        assert file_path.exists(), "discard must defer file deletion until quit"
        assert app._pending_deletes.get("trace_doomed_001") == file_path
        # Undo brings it back.
        await pilot.press("u")
        await pilot.pause()
        assert any(tr["trace_id"] == "trace_doomed_001" for tr in app.by_stage["inbox"])
        assert "trace_doomed_001" not in app._pending_deletes
        assert app._undo_stack == []


@pytest.mark.asyncio
async def test_quit_flushes_pending_discards(tmp_path, monkeypatch):
    """``q`` writes the queued discards to disk; the JSONL is gone after."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    t = _make_trace("trace_doomed_002", "doomed")
    file_path = staging / f"{t['trace_id']}.jsonl"
    file_path.write_text(json.dumps(t) + "\n")
    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert file_path.exists(), "still on disk before quit"
        # Drive the same path quit takes — exercise the flush helper
        # directly so we don't have to wait for the test pilot to tear down.
        flushed = app._flush_pending_deletes()
        assert flushed == 1
        assert not file_path.exists(), "file must be gone after flush"


@pytest.mark.asyncio
async def test_undo_after_stage_toggle_with_persisted_prior_status(
    tmp_path, monkeypatch,
):
    """Regression: StateManager.get_trace returns a dataclass whose
    ``status`` is actually a bare string (dataclasses don't coerce on
    init). If we snapshot that raw value and hand it back to
    ``set_trace_status`` — which calls ``status.value`` — the undo
    would crash with ``'str' object has no attribute 'value'``. Pin the
    coercion in ``_snapshot_status``."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    t = _make_trace("trace_stageable_001", "stage me")
    (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")
    # Persist a prior status via the same StateManager path the live
    # app uses — this is what forces the round-trip to a plain string.
    from opentraces.core.config import get_project_state_path
    sm = StateManager(state_path=get_project_state_path(project))
    sm.set_trace_status("trace_stageable_001", TraceStatus.COMMITTED)
    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        # The trace is currently staged. Space moves it back to inbox…
        await pilot.press("3")  # focus Staged
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        # …now undo — previously exploded on ``status.value``.
        await pilot.press("u")
        await pilot.pause()
        # Trace landed back in Staged.
        assert any(tr["trace_id"] == "trace_stageable_001"
                   for tr in app.by_stage["staged"])


@pytest.mark.asyncio
async def test_push_result_moves_trace_from_staged_to_pushed(
    tmp_path, monkeypatch,
):
    """After ``opentraces push`` writes UPLOADED into state.json, the
    TUI must pick up the change and move the trace into the Pushed
    bucket. We simulate the subprocess write by mutating state.json
    through a second StateManager instance, then call the same
    rehydrate helper the push modal's after_run callback uses."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    t = _make_trace("trace_to_push_0001", "will be pushed")
    (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")
    from opentraces.core.config import get_project_state_path
    sm = StateManager(state_path=get_project_state_path(project))
    sm.set_trace_status("trace_to_push_0001", TraceStatus.COMMITTED)

    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert any(tr["trace_id"] == "trace_to_push_0001"
                   for tr in app.by_stage["staged"])
        # Simulate ``opentraces push`` writing through its own
        # StateManager instance to the same state.json on disk.
        external = StateManager(state_path=get_project_state_path(project))
        external.set_trace_status("trace_to_push_0001", TraceStatus.UPLOADED)
        # The TUI's in-memory state is still stale.
        from opentraces.core.inbox import get_stage
        assert get_stage(app.state, "trace_to_push_0001") == "staged"
        app._rehydrate_after_external_write()
        await pilot.pause()
        # Now the trace appears in the Pushed bucket.
        assert any(tr["trace_id"] == "trace_to_push_0001"
                   for tr in app.by_stage["pushed"])
        assert all(tr["trace_id"] != "trace_to_push_0001"
                   for tr in app.by_stage["staged"])


@pytest.mark.asyncio
async def test_reject_is_undoable(staged_app):
    """``action_reject`` then ``u`` puts the trace back where it came from.

    The ``r`` shortcut was rebound to ``refresh`` (plan B.6); the reject
    action is still exposed via ``action_reject`` and remains
    undoable, just no longer surfaced in the default keymap.
    """
    app, _, _ = staged_app
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("2")  # focus inbox
        await pilot.pause()
        target = app.by_stage["inbox"][0]
        target_id = target["trace_id"]
        app.action_reject()
        await pilot.pause()
        # Rejected traces are filtered out of the visible buckets.
        assert all(tr["trace_id"] != target_id for tr in app.by_stage["inbox"])
        await pilot.press("u")
        await pilot.pause()
        assert any(tr["trace_id"] == target_id for tr in app.by_stage["inbox"])


@pytest.mark.asyncio
async def test_user_vs_agent_body_colors_actually_render(tmp_path, monkeypatch):
    """Regression guard: Rich markup must use Rich color names (``cyan``,
    ``bright_magenta``), not Textual CSS keywords (``ansi_cyan``) — the
    latter are silently dropped by the Rich parser, leaving body text
    uncolored. We inspect the RichLog's rendered Strip segments to prove
    colors land on the right spans."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    t = _make_trace("trace_color_0001", "color check", steps=[
        {"role": "user", "content": "user-line-probe",
         "timestamp": "2026-04-15T14:32:00Z"},
        {"role": "agent", "content": "agent-line-probe",
         "timestamp": "2026-04-15T14:32:01Z"},
    ])
    (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")
    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        stream = app.query_one("#trace-stream")
        # Gather (text, color_name) tuples from every segment.
        colors: dict[str, str] = {}
        for strip in stream.lines:
            for seg in strip:
                if seg.style and seg.style.color is not None:
                    colors[seg.text] = seg.style.color.name
        # Header banners carry the bright variants.
        assert colors.get("── User ──") == "bright_cyan", colors
        assert colors.get("── Agent ──") == "bright_magenta", colors
        # User body text is cyan; agent body uses the terminal default (no
        # explicit color) so we only assert the user case here.
        assert colors.get("user-line-probe") == "cyan", colors


@pytest.mark.asyncio
async def test_view_mode_toggle(staged_app):
    app, _, _ = staged_app
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert app._view_mode == "conversation"
        await pilot.press("a")
        await pilot.pause()
        assert app._view_mode == "full"
        await pilot.press("a")
        await pilot.pause()
        assert app._view_mode == "conversation"


@pytest.mark.asyncio
async def test_counter_updates_on_navigate(tmp_path, monkeypatch):
    """With multiple inbox traces, navigating updates the i/N subtitle."""
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)
    for i in range(3):
        t = _make_trace(f"trace_inbox_{i:02d}", f"task {i}",
                        ts=f"2026-04-1{i}T10:00:00Z")
        (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")

    app = OpenTracesApp(staging_dir=staging)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        panel = app.query_one("#inbox-panel")
        assert panel.border_subtitle.endswith("/ 3")
        await pilot.press("j")
        await pilot.pause()
        assert "2 / 3" in panel.border_subtitle


# --- Visual snapshot -------------------------------------------------------


@pytest.mark.skipif(os.environ.get("SKIP_SNAPSHOT") == "1",
                    reason="snapshot skipped")
def test_snapshot_initial_layout(snap_compare, tmp_path, monkeypatch):
    """SVG snapshot of the two-column layout on first mount.

    Run with ``--snapshot-update`` to regenerate the baseline.
    """
    project = tmp_path / "proj"
    _init_project(project)
    staging = project / "traces"
    staging.mkdir()
    monkeypatch.chdir(project)

    traces = [
        _make_trace("trace_aaaaaa01", "fix the parser bug",
                    ts="2026-04-15T10:00:00Z"),
        _make_trace("trace_bbbbbb02", "add watcher daemon",
                    ts="2026-04-14T09:30:00Z"),
        _make_trace("trace_cccccc03", "upload to huggingface",
                    ts="2026-04-13T08:15:00Z"),
    ]
    for t in traces:
        (staging / f"{t['trace_id']}.jsonl").write_text(json.dumps(t) + "\n")

    from opentraces.core.config import get_project_state_path, save_project_config
    state = StateManager(state_path=get_project_state_path(project))
    state.set_trace_status("trace_bbbbbb02", TraceStatus.COMMITTED)
    state.set_trace_status("trace_cccccc03", TraceStatus.UPLOADED)
    save_project_config(project, {"remote": "alice/opentraces"})

    app = OpenTracesApp(staging_dir=staging)
    assert snap_compare(app, terminal_size=(140, 40))
