"""Unit tests for the semantic ANSI palette."""
from __future__ import annotations

import io

import pytest

from opentraces.clients.text.colors import (
    RESET,
    Role,
    coverage_role,
    detect_color,
    paint,
)


def test_paint_wraps_with_ansi_when_enabled():
    out = paint(Role.COMMIT_ID, "abc", use_color=True)
    assert out.startswith("\x1b[")
    assert out.endswith(RESET)
    assert "abc" in out


def test_commit_id_is_yellow_bold():
    out = paint(Role.COMMIT_ID, "c:deadbee", use_color=True)
    assert "\x1b[1;33m" in out


def test_trace_name_is_cyan_bold():
    out = paint(Role.TRACE_NAME, "built-teenage-milestone", use_color=True)
    assert "\x1b[1;36m" in out


def test_line_count_is_blue():
    out = paint(Role.LINE_COUNT, "[42 lines]", use_color=True)
    assert "\x1b[34m" in out


def test_turn_count_is_green():
    out = paint(Role.TURN_COUNT, "12 turns", use_color=True)
    assert "\x1b[32m" in out


def test_entity_count_is_bright_blue():
    out = paint(Role.ENTITY_COUNT, "8 entities", use_color=True)
    assert "\x1b[94m" in out


def test_paint_plain_when_disabled():
    assert paint(Role.COMMIT_ID, "abc", use_color=False) == "abc"


def test_paint_empty_text_returns_empty():
    assert paint(Role.ADDED, "", use_color=True) == ""


@pytest.mark.parametrize("role", list(Role))
def test_every_role_has_a_code(role: Role):
    out = paint(role, "x", use_color=True)
    # Every role wraps x in an ANSI sequence.
    assert out != "x"
    assert RESET in out


@pytest.mark.parametrize("pct,expected", [
    (100.0, Role.COVERAGE_GOOD),
    (75.0, Role.COVERAGE_GOOD),
    (74.9, Role.COVERAGE_PARTIAL),
    (50.0, Role.COVERAGE_PARTIAL),
    (49.9, Role.COVERAGE_POOR),
    (0.0, Role.COVERAGE_POOR),
])
def test_coverage_role_thresholds(pct: float, expected: Role):
    assert coverage_role(pct) is expected


def test_detect_color_honours_no_color_flag():
    s = io.StringIO()
    assert detect_color(True, stream=s) is False


def test_detect_color_honours_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")

    class _FakeTTY:
        def isatty(self):
            return True

    assert detect_color(False, stream=_FakeTTY()) is False


def test_detect_color_non_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert detect_color(False, stream=io.StringIO()) is False


def test_detect_color_tty_true(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)

    class _FakeTTY:
        def isatty(self):
            return True

    assert detect_color(False, stream=_FakeTTY()) is True


# ---------------------------------------------------------------------------
# render_handle (plan-043 follow-up patch)
# ---------------------------------------------------------------------------

from opentraces.clients.text.colors import render_handle


def test_render_handle_no_color_plain():
    out = render_handle("t", "b73af9c812345", use_color=False)
    assert out == "t:b73af9c8"


def test_render_handle_color_has_three_styled_parts():
    out = render_handle("t", "b73af9c812345", use_color=True)
    # 3 escape sequences: prefix, shortcut, tail. Each ends with reset.
    assert out.count("\x1b[0m") == 3
    # Layout still readable underneath.
    plain = out.replace("\x1b[2;90m", "").replace("\x1b[1;4;95m", "")
    plain = plain.replace("\x1b[35m", "").replace("\x1b[1;35m", "")
    plain = plain.replace("\x1b[0m", "")
    assert "t:" in plain and "b7" in plain and "3af9c8" in plain


def test_render_handle_commit_uses_yellow():
    out = render_handle("c", "2508ec1abcd", use_color=True)
    # Bright-yellow underline+bold for the shortcut.
    assert "\x1b[1;4;93m" in out


def test_render_handle_short_id_no_tail():
    out = render_handle("t", "ab", use_color=False)
    assert out == "t:ab"
