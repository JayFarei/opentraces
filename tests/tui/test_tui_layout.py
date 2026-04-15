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
                ts: str = "2026-04-15T10:00:00Z") -> dict:
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
