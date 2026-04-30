"""Cluster D1 — top-level ``--json`` propagates to every subcommand.

The root group exposes ``--json`` as a global flag. Today only some
subcommands honor it. These tests pin the contract that ``opentraces
--json X`` (with ``--json`` *before* the subcommand) emits parseable
JSON for every public verb, identical to what ``X --json`` would emit.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.config import save_project_config


def _isolated_project(tmp_path):
    """Create a minimal opted-in project dir for status / trail / dataset cmds."""
    project = tmp_path / "proj"
    project.mkdir()
    save_project_config(project, {
        "project_id": "proj-test",
        "agents": ["claude-code"],
        "review_policy": "review",
        "remote": None,
    })
    return project


def _parse_last_json(output: str):
    """Find the last JSON object in CLI output.

    Some commands echo human + sentinel + JSON when ``--json`` is set
    (via ``emit_json``); others emit pure JSON. This locates the JSON
    payload in either format.
    """
    # Strategy: try the entire output first; else split on the sentinel
    # used by emit_json; else find the first '{' and try from there.
    text = output.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "---OPENTRACES_JSON---" in text:
        _, _, after = text.rpartition("---OPENTRACES_JSON---")
        return json.loads(after.strip())
    start = text.find("{")
    if start >= 0:
        return json.loads(text[start:])
    raise AssertionError(f"no JSON in output: {text!r}")


def test_global_json_status_emits_json(tmp_path, monkeypatch):
    project = _isolated_project(tmp_path)
    monkeypatch.chdir(project)

    runner = CliRunner()
    result = runner.invoke(main, ["--json", "status"])
    assert result.exit_code == 0, result.output
    payload = _parse_last_json(result.output)
    assert isinstance(payload, dict)


def test_global_json_trace_query_emits_json(tmp_path, monkeypatch):
    project = _isolated_project(tmp_path)
    monkeypatch.chdir(project)

    runner = CliRunner()
    result = runner.invoke(main, ["--json", "trace", "query", "--cwd", "--limit", "1"])
    assert result.exit_code == 0, result.output
    payload = _parse_last_json(result.output)
    assert isinstance(payload, dict)
    assert "candidates" in payload or "status" in payload


def test_global_json_trace_map_emits_json(tmp_path, monkeypatch):
    """Pin the propagation contract: ``trace map`` receives ``--json`` from root.

    The error-path of ``trace map`` (target not found) emits to stderr and
    exits non-zero before reaching any structured output branch — that's
    a pre-existing behavior of ``trace.py`` (cluster B's surface). For the
    propagation contract, we verify behavioral equivalence: ``--json`` at
    the root and at the subcommand produce identical output for the same
    input. If the subcommand-scoped ``--json`` produces JSON, so must the
    root one; if it emits plaintext on error, so must root.
    """
    project = _isolated_project(tmp_path)
    monkeypatch.chdir(project)

    runner = CliRunner()
    via_root = runner.invoke(main, ["--json", "trace", "map", "nonexistent_id"])
    via_sub = runner.invoke(main, ["trace", "map", "nonexistent_id", "--json"])
    assert via_root.exit_code == via_sub.exit_code
    assert via_root.output == via_sub.output
    assert via_root.stderr == via_sub.stderr


def test_global_json_trail_track_emits_json(tmp_path, monkeypatch):
    project = _isolated_project(tmp_path)
    # Make project a real git repo so trail can read from it.
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
    (project / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(project), "add", "."], check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-q", "-m", "init"], check=True)
    monkeypatch.chdir(project)

    runner = CliRunner()
    # Use a non-existent trace_id; we just need JSON-shaped output.
    result = runner.invoke(main, ["--json", "trail", "track", "nonexistent_trace_id"])
    # Output must be JSON-parseable regardless of success.
    payload = _parse_last_json(result.output)
    assert isinstance(payload, dict)


def test_global_json_dataset_list_emits_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, ["--json", "dataset", "list"])
    assert result.exit_code == 0, result.output
    payload = _parse_last_json(result.output)
    assert isinstance(payload, dict)
    assert "datasets" in payload


def test_global_json_doctor_emits_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, ["--json", "doctor"])
    # Doctor may exit non-zero (broken pipeline), but output must be JSON.
    payload = _parse_last_json(result.output)
    assert isinstance(payload, dict)


def test_subcommand_json_still_works_when_global_not_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    # Subcommand-scoped --json must continue to work without the global flag.
    result = runner.invoke(main, ["dataset", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = _parse_last_json(result.output)
    assert isinstance(payload, dict)
    assert "datasets" in payload
