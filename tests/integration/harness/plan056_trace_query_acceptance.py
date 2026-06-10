"""Plan 56 local Trace Index / Map / Query acceptance harness.

Runs a seeded, no-network proof of the M1 retrieval flow:

    python tests/integration/harness/plan056_trace_query_acceptance.py --mode local --json
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

from click.testing import CliRunner


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
SCHEMA_SRC = REPO_ROOT / "packages" / "opentraces-schema" / "src"
for path in (str(SRC), str(SCHEMA_SRC), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

TRANSCRIPT_MARKERS = [
    "return parse_good(token)",
    "FAILED tests/test_parser.py::test_parser_regression",
    "PRIVATE ACCEPTANCE TRANSCRIPT BLOCK",
]


@contextlib.contextmanager
def _temporary_opentraces_home(home: Path) -> Iterator[None]:
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)

    from opentraces.core import config as config_mod
    from opentraces.core import paths as paths_mod

    names = ("OPENTRACES_DIR", "CONFIG_PATH", "CREDENTIALS_PATH", "PROJECTS_DIR")
    saved = {
        mod: {name: getattr(mod, name) for name in names}
        for mod in (paths_mod, config_mod)
    }
    opentraces_dir = home / ".opentraces"
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    for mod in (paths_mod, config_mod):
        mod.OPENTRACES_DIR = opentraces_dir
        mod.CONFIG_PATH = opentraces_dir / "config.json"
        mod.CREDENTIALS_PATH = opentraces_dir / "credentials"
        mod.PROJECTS_DIR = projects_dir
    try:
        yield
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        for mod, values in saved.items():
            for name, value in values.items():
                setattr(mod, name, value)


def _enroll_project(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps(
            {
                "marker_version": "2",
                "project_id": "plan056acceptance1234567890abcdef",
            }
        )
    )


def _write_project_trace(project_dir: Path) -> None:
    from opentraces.core.config import get_project_traces_dir
    from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord

    record = TraceRecord(
        trace_id="trace-plan056-acceptance",
        session_id="session-plan056-acceptance",
        generation_index=3,
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Bug fix failing parser test acceptance"},
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Use grill-me to fix the parser bug and prove the failing test passes.",
            ),
            Step(
                step_index=2,
                role="agent",
                content="Plan:\n- reproduce failure\n- patch parser\n- rerun pytest",
                reasoning_content=("PRIVATE ACCEPTANCE TRANSCRIPT BLOCK " * 3000).strip(),
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-skill",
                        tool_name="Skill",
                        input={"name": "grill-me"},
                    ),
                    ToolCall(
                        tool_call_id="tc-fail",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_parser.py"},
                    ),
                ],
                observations=[
                    Observation(source_call_id="tc-skill", content="loaded"),
                    Observation(
                        source_call_id="tc-fail",
                        content="FAILED tests/test_parser.py::test_parser_regression",
                        output_summary="1 failed",
                    ),
                ],
            ),
            Step(
                step_index=3,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-edit",
                        tool_name="Edit",
                        input={
                            "file_path": "src/parser.py",
                            "old_string": "return parse_bad(token)",
                            "new_string": "return parse_good(token)",
                        },
                    )
                ],
            ),
            Step(
                step_index=4,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-pass",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_parser.py"},
                    )
                ],
                observations=[
                    Observation(
                        source_call_id="tc-pass",
                        content="tests/test_parser.py . 1 passed",
                        output_summary="1 passed",
                    )
                ],
            ),
            Step(step_index=5, role="agent", content="Done; pytest passes."),
        ],
        outcome={"success": True, "committed": True},
    )
    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(record.model_dump_json() + "\n")

    distractor = TraceRecord(
        trace_id="trace-plan056-acceptance-distractor",
        session_id="session-plan056-acceptance-distractor",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Bug fix failing parser test discussion only"},
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Read a note that says bug fix failing test, but do not edit or test.",
            ),
            Step(
                step_index=2,
                role="agent",
                content="This was only a lexical discussion with no patch or verification.",
            ),
        ],
        outcome={"success": False, "committed": False},
    )
    (traces_dir / f"{distractor.trace_id}.jsonl").write_text(
        distractor.model_dump_json() + "\n"
    )


def _invoke(runner: CliRunner, argv: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    from opentraces.cli import main

    result = runner.invoke(main, argv)
    payload = json.loads(result.output) if result.output.strip().startswith("{") else {}
    transcript = {
        "argv": "ot " + " ".join(argv),
        "exit_code": result.exit_code,
        "output_bytes": len(result.output.encode()),
    }
    if "candidates" in payload:
        transcript["candidate_count"] = len(payload["candidates"])
        if payload["candidates"]:
            transcript["selected_trace_id"] = payload["candidates"][0]["trace_id"]
            transcript["selected_unit_id"] = payload["candidates"][0]["unit_id"]
    if "map" in payload:
        transcript["node_count"] = len(payload["map"].get("nodes", []))
    if "trace" in payload:
        transcript["trace_id"] = payload["trace"].get("trace_id")
    if result.exit_code != 0:
        transcript["error_preview"] = result.output[:240]
    return transcript, payload


def _index_digest(index_path: Path) -> str:
    with sqlite3.connect(index_path) as conn:
        rows = {
            "units": list(
                conn.execute(
                    "select unit_id, unit_type, trace_id, title_text from units order by unit_id"
                )
            ),
            "nodes": list(
                conn.execute(
                    "select node_id, trace_id, ordinal from trace_map_nodes order by node_id"
                )
            ),
        }
    encoded = json.dumps(rows, sort_keys=True, default=list).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_local() -> dict[str, Any]:
    from opentraces.core.trace_index import default_index_path, rebuild_index

    with tempfile.TemporaryDirectory(prefix="opentraces-plan056-") as tmp:
        root = Path(tmp)
        home = root / "home"
        project = root / "demo"
        with _temporary_opentraces_home(home):
            _enroll_project(project)
            _write_project_trace(project)
            runner = CliRunner()

            # Search is read-only: build the snapshot (and heal the legacy
            # index) once, the way an operator would, before any query.
            from opentraces.cli import main

            index_result = runner.invoke(main, ["trace", "index"])
            assert index_result.exit_code == 0, index_result.output

            command_transcripts: list[dict[str, Any]] = []
            skill_transcript, skill_payload = _invoke(
                runner,
                [
                    "trace",
                    "query",
                    "--skill",
                    "grill-me",
                    "--json",
                ],
            )
            command_transcripts.append(skill_transcript)

            broad_transcript, broad_payload = _invoke(
                runner,
                [
                    "trace",
                    "query",
                    "--lex",
                    "bug fix failing test",
                    "--json",
                ],
            )
            command_transcripts.append(broad_transcript)

            signal_transcript, signal_payload = _invoke(
                runner,
                [
                    "trace",
                    "query",
                    "--lex",
                    "bug fix failing test",
                    "--signal",
                    "tested_successful_fix_candidate",
                    "--json",
                ],
            )
            command_transcripts.append(signal_transcript)

            packet = signal_payload["candidates"][0]
            map_transcript, map_payload = _invoke(
                runner,
                [
                    "trace",
                    "map",
                    packet["trace_id"],
                    "--candidate",
                    packet["unit_id"],
                    "--max-steps",
                    "40",
                    "--json",
                ],
            )
            command_transcripts.append(map_transcript)

            get_transcript, get_payload = _invoke(
                runner,
                ["trace", "get", packet["trace_id"], "--json"],
            )
            command_transcripts.append(get_transcript)

            packet_bytes = len(json.dumps(packet, sort_keys=True).encode())
            slice_bytes = len(json.dumps(map_payload["map"], sort_keys=True).encode())
            full_trace_bytes = len(json.dumps(get_payload["trace"], sort_keys=True).encode())
            first_digest = _index_digest(default_index_path())
            rebuild_index()
            second_digest = _index_digest(default_index_path())

            summary_text = json.dumps(
                {
                    "skill": skill_payload,
                    "broad": broad_payload,
                    "signal": signal_payload,
                    "map": map_payload,
                    "commands": command_transcripts,
                },
                sort_keys=True,
            )
            no_transcript_text = not any(marker in summary_text for marker in TRANSCRIPT_MARKERS)

            return {
                "status": "ok",
                "mode": "local",
                "command_transcripts": command_transcripts,
                "candidate_counts": {
                    "skill": len(skill_payload["candidates"]),
                    "broad_lexical": len(broad_payload["candidates"]),
                    "signal_gated": len(signal_payload["candidates"]),
                },
                "signal_gating_reduced_candidates": (
                    len(broad_payload["candidates"]) > len(signal_payload["candidates"])
                ),
                "selected_ids": {
                    "trace_id": packet["trace_id"],
                    "unit_id": packet["unit_id"],
                    "map_node_refs": packet["map_node_refs"],
                },
                "byte_ratios": {
                    "packet_bytes": packet_bytes,
                    "slice_bytes": slice_bytes,
                    "full_trace_bytes": full_trace_bytes,
                    "full_to_packet": round(full_trace_bytes / max(packet_bytes, 1), 3),
                    "full_to_slice": round(full_trace_bytes / max(slice_bytes, 1), 3),
                },
                "index_rebuild_digests": {
                    "first": first_digest,
                    "second": second_digest,
                    "equivalent": first_digest == second_digest,
                },
                "no_transcript_text": no_transcript_text,
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["local"], default="local")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = run_local()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Plan 56 acceptance: {payload['status']}")
        print(f"  signal candidates: {payload['candidate_counts']['signal_gated']}")
        print(f"  selected trace: {payload['selected_ids']['trace_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
