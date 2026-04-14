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
