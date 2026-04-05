from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest


def _repo_staging_dir() -> Path:
    return Path(__file__).resolve().parents[1] / ".opentraces" / "staging"


def _sample_trace_files(limit: int = 5) -> list[Path]:
    staging = _repo_staging_dir()
    if not staging.exists():
        return []

    selected: list[Path] = []
    for path in sorted(staging.glob("*.jsonl")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        steps = data.get("steps") or []
        if any((step.get("content") or "").strip() for step in steps):
            selected.append(path)
        if len(selected) >= limit:
            break
    return selected


def _sample_task_trace_files(limit: int = 3) -> list[Path]:
    staging = _repo_staging_dir()
    if not staging.exists():
        return []

    selected: list[Path] = []
    for path in sorted(staging.glob("*.jsonl")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        tool_names = [
            tc.get("tool_name") or tc.get("name")
            for step in (data.get("steps") or [])
            for tc in (step.get("tool_calls") or [])
        ]
        if any(name in {"TaskCreate", "TaskUpdate", "delegate_task"} for name in tool_names):
            selected.append(path)
        if len(selected) >= limit:
            break
    return selected


def _sample_todo_trace_files(limit: int = 3) -> list[Path]:
    staging = _repo_staging_dir()
    if not staging.exists():
        return []

    selected: list[Path] = []
    for path in sorted(staging.glob("*.jsonl")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        tool_names = [
            tc.get("tool_name") or tc.get("name")
            for step in (data.get("steps") or [])
            for tc in (step.get("tool_calls") or [])
        ]
        if any(name in {"todo", "TodoWrite"} for name in tool_names):
            selected.append(path)
        if len(selected) >= limit:
            break
    return selected


async def _open_trace_and_capture_svg(
    trace_path: Path,
    target_text: str | None = None,
) -> str:
    from opentraces.clients.tui.app import OpenTracesApp
    from opentraces.clients.tui.screens.trace import TraceScreen

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / ".opentraces" / "staging"
        staging.mkdir(parents=True)
        shutil.copy2(trace_path, staging / trace_path.name)

        app = OpenTracesApp(staging_dir=staging)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            app._exception = None
            if target_text and isinstance(app.screen, TraceScreen):
                screen = app.screen
                for index, step in enumerate(screen._steps()):
                    content = step.get("_content_plain") or step.get("content") or ""
                    if target_text in content:
                        screen._cursor_step = index
                        screen._expanded_step = index
                        screen._sync_cursor()
                        await pilot.pause()
                        app._exception = None
                        break
            return app.export_screenshot()


async def _open_trace_and_capture_selected_block_text(
    trace_path: Path,
    target_text: str,
) -> str:
    from opentraces.clients.tui.app import OpenTracesApp
    from opentraces.clients.tui.screens.trace import TraceScreen
    from opentraces.clients.tui.widgets.step_block import StepBlock
    from textual.widgets import Static

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / ".opentraces" / "staging"
        staging.mkdir(parents=True)
        shutil.copy2(trace_path, staging / trace_path.name)

        app = OpenTracesApp(staging_dir=staging)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            app._exception = None
            if not isinstance(app.screen, TraceScreen):
                return ""

            screen = app.screen
            for index, step in enumerate(screen._steps()):
                content = step.get("_content_plain") or step.get("content") or ""
                if target_text not in content:
                    continue
                screen._cursor_step = index
                screen._expanded_step = index
                screen._sync_cursor()
                await pilot.pause()
                app._exception = None
                block = list(screen.query(StepBlock))[index]
                content_widget = block.query_one(".step-content", Static)
                lines = [
                    "".join(segment.text for segment in content_widget.render_line(line_no))
                    for line_no in range(content_widget.region.height)
                ]
                return "\n".join(lines)
            return ""


@pytest.mark.asyncio
async def test_real_repo_traces_render_visible_transcript_text() -> None:
    trace_files = _sample_trace_files(limit=5)
    if not trace_files:
        pytest.skip("No usable real traces found in .opentraces/staging")

    failures: list[str] = []
    for trace_path in trace_files:
        data = json.loads(trace_path.read_text())
        contents = [
            (step.get("content") or "").strip().replace("\n", " ")
            for step in (data.get("steps") or [])
        ]
        contents = [content for content in contents if content]
        if not contents:
            continue

        svg = await _open_trace_and_capture_svg(trace_path)
        words = [word for word in contents[0].split() if len(word) > 3][:3]
        words = words or contents[0].split()[:1]
        if not any(word in svg for word in words):
            failures.append(f"{trace_path.name}: expected one of {words!r} in opened trace view")

    assert not failures, "\n".join(failures)


@pytest.mark.asyncio
async def test_real_repo_task_traces_render_readable_task_entries() -> None:
    trace_files = _sample_task_trace_files(limit=3)
    if not trace_files:
        pytest.skip("No real task traces found in .opentraces/staging")

    failures: list[str] = []
    for trace_path in trace_files:
        data = json.loads(trace_path.read_text())
        task_words: list[str] = []
        for step in data.get("steps") or []:
            for tool_call in step.get("tool_calls") or []:
                name = tool_call.get("tool_name") or tool_call.get("name")
                inp = tool_call.get("input") or tool_call.get("arguments") or {}
                if name == "TaskCreate":
                    task_words.extend(str(inp.get("subject") or "").split()[:2])
                elif name == "delegate_task":
                    tasks = inp.get("tasks")
                    if not isinstance(tasks, list):
                        tasks = [inp]
                    for task in tasks:
                        if isinstance(task, dict):
                            task_words.extend(str(task.get("goal") or "").split()[:2])
                            break
                if len(task_words) >= 2:
                    break
            if len(task_words) >= 2:
                break

        task_words = [word for word in task_words if len(word) > 3][:3]
        if not task_words:
            continue

        rendered_text = await _open_trace_and_capture_selected_block_text(trace_path, task_words[0])
        if not any(word in rendered_text for word in task_words):
            failures.append(f"{trace_path.name}: expected one of {task_words!r} in task rendering")

    assert not failures, "\n".join(failures)


@pytest.mark.asyncio
async def test_real_repo_todo_traces_render_readable_checklists() -> None:
    trace_files = _sample_todo_trace_files(limit=3)
    if not trace_files:
        pytest.skip("No real todo traces found in .opentraces/staging")

    failures: list[str] = []
    for trace_path in trace_files:
        data = json.loads(trace_path.read_text())
        target_text = ""
        expected_words: list[str] = []
        for step in data.get("steps") or []:
            for tool_call in step.get("tool_calls") or []:
                name = tool_call.get("tool_name") or tool_call.get("name")
                if name not in {"todo", "TodoWrite"}:
                    continue
                todos = (tool_call.get("input") or tool_call.get("arguments") or {}).get("todos") or []
                if not isinstance(todos, list):
                    continue
                for todo in todos:
                    if not isinstance(todo, dict):
                        continue
                    content = str(todo.get("content") or "").strip()
                    if content:
                        target_text = content
                        expected_words = [word for word in content.split() if len(word) > 3][:3]
                        break
                if expected_words:
                    break
            if expected_words:
                break

        if not expected_words:
            continue

        rendered_text = await _open_trace_and_capture_selected_block_text(trace_path, target_text)
        if "\"todos\"" in rendered_text or "{'todos'" in rendered_text:
            failures.append(f"{trace_path.name}: todo block still rendered as raw payload")
            continue
        if "TODOS" not in rendered_text:
            failures.append(f"{trace_path.name}: missing TODOS heading")
            continue
        if not any(word in rendered_text for word in expected_words):
            failures.append(f"{trace_path.name}: expected one of {expected_words!r} in todo rendering")

    assert not failures, "\n".join(failures)
