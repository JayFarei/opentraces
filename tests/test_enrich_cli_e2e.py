"""End-to-end CLI test: `opentraces enrich` on a real trace file (plan 038 phases 3+4).

Stubs the model client via an executable shell script; exercises the
post-processor runner via a stub processor binary.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from opentraces_schema import Agent, Task, TraceRecord


def _make_executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch):
    """Isolate global + project config under tmp_path so the test
    doesn't touch ~/.opentraces."""
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(proj)
    return tmp_path, home, proj


def _write_trace(path: Path) -> None:
    rec = TraceRecord(
        trace_id="t-cli",
        session_id="s-cli",
        task=Task(description="demo"),
        agent=Agent(name="claude-code", model="stub/model"),
    )
    path.write_text(rec.model_dump_json(indent=2))


def test_enrich_with_no_intent_flag_only_runs_processors(tmp_path, cli_env):
    trace_file = cli_env[2] / "trace.json"
    _write_trace(trace_file)

    stub_body = r"""
import json, sys
data = json.loads(sys.stdin.read())
data.setdefault('metadata', {})['touched_by'] = 'stub'
sys.stdout.write(json.dumps(data))
"""
    stub = _make_executable(tmp_path / "stub.py", stub_body)

    # Write a project config with a single enrich-stage processor
    proj_cfg_dir = cli_env[2] / ".opentraces"
    proj_cfg_dir.mkdir()
    (proj_cfg_dir / "config.json").write_text(json.dumps({
        "post_processors": [{
            "name": "stub",
            "command": str(stub),
            "args": [],
            "env": {},
            "when": "enrich",
        }],
    }))

    # Call the CLI with --no-intent so we don't need a real model.
    result = subprocess.run(
        [sys.executable, "-m", "opentraces", "enrich", "--no-intent", str(trace_file)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(trace_file.read_text())
    assert out["metadata"]["touched_by"] == "stub"
    # Intent remained unset because --no-intent was passed
    assert out.get("intent") is None


def test_enrich_strict_fails_on_missing_binary(tmp_path, cli_env):
    trace_file = cli_env[2] / "trace.json"
    _write_trace(trace_file)

    proj_cfg_dir = cli_env[2] / ".opentraces"
    proj_cfg_dir.mkdir()
    (proj_cfg_dir / "config.json").write_text(json.dumps({
        "post_processors": [{
            "name": "ghost",
            "command": "/no/such/binary/ever",
            "when": "enrich",
        }],
    }))

    result = subprocess.run(
        [sys.executable, "-m", "opentraces", "enrich", "--no-intent", "--strict", str(trace_file)],
        capture_output=True, text=True, timeout=60,
    )
    # Strict: missing binary is promoted to a hard error
    assert result.returncode != 0


def test_doctor_lists_configured_processors(tmp_path, cli_env):
    stub = _make_executable(tmp_path / "present.py", "import sys; sys.stdout.write(sys.stdin.read())")
    proj_cfg_dir = cli_env[2] / ".opentraces"
    proj_cfg_dir.mkdir()
    (proj_cfg_dir / "config.json").write_text(json.dumps({
        "post_processors": [
            {"name": "visible", "command": str(stub), "when": "enrich"},
            {"name": "gone", "command": "/no/such/bin", "when": "push"},
        ],
    }))

    result = subprocess.run(
        [sys.executable, "-m", "opentraces", "--json", "doctor"],
        capture_output=True, text=True, timeout=30,
    )
    # Find the JSON chunk
    _, sep, json_out = result.stdout.partition("---OPENTRACES_JSON---")
    doc = json.loads(json_out or result.stdout)
    procs = {p["name"]: p for p in doc["doctor"]["post_processors"]}
    assert procs["visible"]["status"] == "detected"
    assert procs["gone"]["status"] == "missing"
    assert doc["doctor"]["intent"]["mode"] == "on"
