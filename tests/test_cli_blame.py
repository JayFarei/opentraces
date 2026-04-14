"""Unit tests for ``ot blame`` (plan-043 phase 4)."""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from opentraces.cli.blame import blame_cmd
from opentraces.clients.text.graph_renderer import strip_ansi
from opentraces.core.cache import AttributionCache


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _init_git_repo(tmp_path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)
    (tmp_path / "x.txt").write_text("line1\nline2\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True,
    )
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True,
    ).strip()
    # Mint an opentraces marker so AttributionCache can compute a project slug.
    import uuid as _uuid
    from opentraces.core.config import _write_marker
    _write_marker(tmp_path, _uuid.uuid4().hex, {})
    return sha


def _write_basic_attr(cache: AttributionCache, sha: str) -> None:
    cache.write_attribution(sha, {
        "commit_sha": sha,
        "version": 1,
        "coverage": {"attributed": 2, "total": 3, "ratio": 0.6667},
        "traces": [
            {"trace_id": "traceXYZ11111111", "line_count": 2,
             "files": ["x.txt"], "lifecycle": "final"},
        ],
        "files": {
            "x.txt": {
                "total": 3,
                "lines": [
                    {"n": 1, "trace_id": "traceXYZ11111111",
                     "consistency": "attributed"},
                    {"n": 2, "trace_id": "traceXYZ11111111",
                     "consistency": "attributed"},
                    {"n": 3, "trace_id": "", "consistency": "pre-audit"},
                ],
            },
        },
    })


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #

def test_default_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sha = _init_git_repo(tmp_path)
    cache = AttributionCache(tmp_path)
    _write_basic_attr(cache, sha)

    r = CliRunner().invoke(
        blame_cmd, [sha, "--no-color", "--project", str(tmp_path)],
    )
    assert r.exit_code == 0, r.output
    assert "Commit:" in r.output
    assert "Coverage:" in r.output
    assert "67% attributed" in r.output or "66% attributed" in r.output
    assert "traceXYZ" in r.output
    assert "x.txt" in r.output


def test_lines_flag_renders_each_status(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sha = _init_git_repo(tmp_path)
    cache = AttributionCache(tmp_path)
    cache.write_attribution(sha, {
        "commit_sha": sha,
        "version": 1,
        "coverage": {"attributed": 1, "total": 3, "ratio": 0.333},
        "traces": [
            {"trace_id": "traceXYZ11111111", "line_count": 1,
             "files": ["x.txt"], "lifecycle": "provisional"},
        ],
        "files": {
            "x.txt": {
                "total": 3,
                "lines": [
                    {"n": 1, "trace_id": "traceXYZ11111111",
                     "consistency": "attributed"},
                    {"n": 2, "trace_id": "", "consistency": "pre-audit"},
                    {"n": 3, "trace_id": "", "consistency": "missing_from_audit"},
                ],
            },
        },
    })
    r = CliRunner().invoke(
        blame_cmd, [sha, "--lines", "--no-color", "--project", str(tmp_path)],
    )
    assert r.exit_code == 0, r.output
    assert "traceXYZ" in r.output
    assert "\u00b7" in r.output  # pre-audit glyph
    assert "?" in r.output        # missing glyph
    # --no-color means strip_ansi(out) == out
    assert strip_ansi(r.output) == r.output


def test_entities_flag_groups_by_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sha = _init_git_repo(tmp_path)
    cache = AttributionCache(tmp_path)
    _write_basic_attr(cache, sha)

    ep = cache.entity_path(sha)
    ep.parent.mkdir(parents=True, exist_ok=True)
    ep.write_text(json.dumps({
        "entities": [
            {"change_type": "added", "entity_kind": "function",
             "entity_name": "foo", "file": "x.txt",
             "trace_id": "traceXYZ11111111"},
            {"change_type": "modified", "entity_kind": "class",
             "entity_name": "Bar", "file": "x.txt",
             "trace_id": "traceXYZ11111111"},
            {"change_type": "renamed", "entity_kind": "function",
             "entity_name": "qux", "old_entity_name": "baz",
             "file": "x.txt", "trace_id": "traceXYZ11111111"},
            {"change_type": "deleted", "entity_kind": "function",
             "entity_name": "old", "file": "x.txt",
             "trace_id": "traceABC22222222"},
        ],
    }))
    r = CliRunner().invoke(
        blame_cmd, [sha, "--entities", "--no-color", "--project", str(tmp_path)],
    )
    assert r.exit_code == 0, r.output
    assert "+ function foo" in r.output
    assert "~ class Bar" in r.output
    assert "renamed baz \u2192 qux" in r.output
    assert "- function deleted old" in r.output
    assert "traceXYZ" in r.output
    assert "traceABC" in r.output


def test_entities_missing_cache_message(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sha = _init_git_repo(tmp_path)
    cache = AttributionCache(tmp_path)
    _write_basic_attr(cache, sha)
    r = CliRunner().invoke(
        blame_cmd, [sha, "--entities", "--no-color", "--project", str(tmp_path)],
    )
    assert r.exit_code == 0
    assert "entity cache not available" in r.output


def test_json_output_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sha = _init_git_repo(tmp_path)
    cache = AttributionCache(tmp_path)
    _write_basic_attr(cache, sha)
    r = CliRunner().invoke(
        blame_cmd, [sha, "--json", "--project", str(tmp_path)],
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["commit"]["sha"] == sha
    assert "coverage" in payload and "attributed" in payload["coverage"]
    assert payload["coverage"]["total"] == 3
    assert "x.txt" in payload["files"]
    assert len(payload["traces"]) == 1


def test_path_scope_filters_other_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sha = _init_git_repo(tmp_path)
    cache = AttributionCache(tmp_path)
    cache.write_attribution(sha, {
        "commit_sha": sha, "version": 1,
        "coverage": {"attributed": 2, "total": 2, "ratio": 1.0},
        "traces": [
            {"trace_id": "traceFOO11111111", "line_count": 1,
             "files": ["foo.py"], "lifecycle": "final"},
            {"trace_id": "traceBAR22222222", "line_count": 1,
             "files": ["bar.py"], "lifecycle": "final"},
        ],
        "files": {
            "foo.py": {"total": 1, "lines": [
                {"n": 1, "trace_id": "traceFOO11111111",
                 "consistency": "attributed"}]},
            "bar.py": {"total": 1, "lines": [
                {"n": 1, "trace_id": "traceBAR22222222",
                 "consistency": "attributed"}]},
        },
    })
    r = CliRunner().invoke(
        blame_cmd,
        [sha, "foo.py", "--lines", "--no-color", "--project", str(tmp_path)],
    )
    assert r.exit_code == 0, r.output
    assert "foo.py" in r.output
    assert "traceFOO" in r.output
    assert "bar.py" not in r.output
    assert "traceBAR" not in r.output


# --------------------------------------------------------------------------- #
# first-run prompt
# --------------------------------------------------------------------------- #

def _force_isatty(monkeypatch, value: bool) -> None:
    """Patch the blame module's isatty indirection."""
    import opentraces.cli.blame as _b
    monkeypatch.setattr(_b, "_stdin_isatty", lambda: value)


def test_first_run_non_tty_exits_with_guidance(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    sha = _init_git_repo(tmp_path)
    _force_isatty(monkeypatch, False)
    r = CliRunner().invoke(
        blame_cmd, [sha, "--no-color", "--project", str(tmp_path)],
    )
    assert r.exit_code == 1
    assert "backfill" in (r.output or "").lower()


def test_first_run_decision_never_skips_prompt(tmp_path, monkeypatch):
    from opentraces.core.config import set_first_run_backfill_decision
    monkeypatch.setenv("HOME", str(tmp_path))
    sha = _init_git_repo(tmp_path)
    set_first_run_backfill_decision(tmp_path, "never")
    _force_isatty(monkeypatch, True)  # should still skip
    r = CliRunner().invoke(
        blame_cmd, [sha, "--no-color", "--project", str(tmp_path)],
    )
    assert r.exit_code == 1


def test_first_run_tty_n_exits_1(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sha = _init_git_repo(tmp_path)
    _force_isatty(monkeypatch, True)
    r = CliRunner().invoke(
        blame_cmd, [sha, "--no-color", "--project", str(tmp_path)],
        input="n\n",
    )
    assert r.exit_code == 1


def test_first_run_tty_never_persists(tmp_path, monkeypatch):
    from opentraces.core.config import get_first_run_backfill_decision
    monkeypatch.setenv("HOME", str(tmp_path))
    sha = _init_git_repo(tmp_path)
    _force_isatty(monkeypatch, True)
    r = CliRunner().invoke(
        blame_cmd, [sha, "--no-color", "--project", str(tmp_path)],
        input="never\n",
    )
    assert r.exit_code == 1
    assert get_first_run_backfill_decision(tmp_path) == "never"


def test_unknown_sha_exits_2(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _init_git_repo(tmp_path)
    r = CliRunner().invoke(
        blame_cmd, ["deadbeefdeadbeef", "--no-color", "--project", str(tmp_path)],
    )
    assert r.exit_code == 2
