"""Artifact-preferred + synthetic Pi checkpoint helpers for plan 091."""

from __future__ import annotations

import json
import tarfile
from collections.abc import Mapping
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    _artifact_paths,
    artifact_exists,
    capture_metadata_from_artifact,
    read_state_json,
    restore_from_capture,
)

PI_AUDIT_KEY = "c_captured_pi_session_audit"
_PYTHON = Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python"


def _trace_agent_name(trace: dict) -> str:
    agent = trace.get("agent") or {}
    if isinstance(agent, dict):
        return str(agent.get("name") or "")
    return ""


def _first_pi_trace(state: dict) -> dict | None:
    traces = list((state.get("traces") or {}).values())
    for trace in traces:
        if _trace_agent_name(trace) == "pi":
            return trace
    return traces[0] if traces else None


def _load_trace_record(driver: Driver, box: Box, trace: dict) -> dict:
    path = trace.get("file_path")
    if not isinstance(path, str) or not path:
        return {}
    raw = driver.exec(box, ["cat", path])
    if not raw.ok:
        return {}
    first = next((line for line in raw.stdout.splitlines() if line.strip()), "")
    if not first:
        return {}
    try:
        data = json.loads(first)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _derive_audit_from_box(
    driver: Driver,
    box: Box,
    cap_meta: dict | None,
    *,
    checkpoint_name: str,
    capture_name: str,
) -> dict:
    state_dir, state = read_state_json(driver, box)
    trace = _first_pi_trace(state)
    if not trace:
        raise CheckpointError(
            f"{checkpoint_name} produced no Pi traces; state at "
            f"{state_dir}/state.json contained {len(state.get('traces') or {})} entries"
        )

    record = _load_trace_record(driver, box, trace)
    paths = driver.paths(box)
    project = paths["project"]
    head = driver.exec(box, ["git", "-C", project, "rev-parse", "HEAD"])
    commit_sha = head.stdout.strip() if head.ok else ""
    steps = record.get("steps") if isinstance(record.get("steps"), list) else []
    context_tree_built = bool(
        record.get("context_tree_summary")
        or any(isinstance(step, dict) and step.get("context_node_id") for step in steps)
    )
    step_count = int(trace.get("step_count") or len(steps))
    session_file = (record.get("metadata") or {}).get("session_file") if isinstance(record.get("metadata"), dict) else ""

    return {
        "scenario_name": capture_name,
        "session_id": trace.get("session_id") or "",
        "trace_id": trace.get("trace_id") or "",
        "agent_name": _trace_agent_name(record) or _trace_agent_name(trace),
        "step_count": step_count,
        "commit_sha": commit_sha,
        "state_dir": state_dir,
        "context_tree_built": context_tree_built,
        "transcript_path": session_file or "",
        "capture_metadata": (
            capture_metadata_from_artifact(cap_meta)
            if cap_meta is not None
            else {"source": "synthetic", "harness": "tests/otbox/checkpoints/_captured_pi_common.py", "capture_name": capture_name}
        ),
    }


def _missing_artifact_audit(capture_name: str) -> dict:
    return {
        "scenario_name": capture_name,
        "session_id": "",
        "trace_id": "",
        "agent_name": "pi",
        "step_count": 0,
        "commit_sha": "",
        "context_tree_built": False,
        "capture_metadata": {"source": "missing_artifact", "capture_name": capture_name},
    }


def _check(result, label: str, checkpoint_name: str) -> None:
    if result.ok:
        return
    detail = (result.stderr or result.stdout or "").strip()
    raise CheckpointError(
        f"{checkpoint_name} synthetic step {label!r} failed "
        f"(rc={result.returncode}): {detail[:600] or '<no output>'}"
    )


def _run_synthetic_pi_capture(driver: Driver, box: Box, *, capture_name: str, checkpoint_name: str) -> None:
    """Create a deterministic Pi native session + sidecars and ingest it.

    The fallback keeps tier-0 otbox deterministic while still exercising the
    real Pi parser, bridge sidecar path, Trace Trails, Context Tree, bucket v2,
    and trace-search projection. Live artifacts, when present, replace this
    synthetic path via ``restore_from_capture``.
    """

    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]
    cli = f"{project}/.testvenv/bin/opentraces"
    py = f"{project}/.testvenv/bin/python"

    script = r'''
import json
import os
import subprocess
from pathlib import Path

from opentraces.capture.pi.bridge import record_event
from opentraces.capture.pi.sessions import cwd_slug

project = Path(__PROJECT__)
home = Path(__HOME__)
session_id = "pi-linear-session"

def run(argv, **kwargs):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return subprocess.run(argv, cwd=project, env=env, text=True, capture_output=True, check=True, **kwargs)

(project / "src").mkdir(parents=True, exist_ok=True)
(project / "src" / "app.py").write_text(
    "def greet(name: str) -> str:\n"
    "    return f'Hello, {name}'\n",
    encoding="utf-8",
)
run(["git", "init"])
run(["git", "config", "user.name", "otbox"])
run(["git", "config", "user.email", "otbox@opentraces.local"])
run(["git", "add", "src/app.py"])
run(["git", "commit", "-m", "seed app"])
run([__CLI__, "init", "--agent", "pi", "--start-fresh"])
run([__CLI__, "setup", "git"])

session_dir = home / ".pi" / "agent" / "sessions" / cwd_slug(project)
session_dir.mkdir(parents=True, exist_ok=True)
session_file = session_dir / "20260602T100000_pi-linear-session.jsonl"
rows = [
    {"type":"session","version":3,"id":session_id,"timestamp":"2026-06-02T10:00:00.000Z","cwd":str(project),"pi_version":"1.0.0"},
    {"type":"message","id":"u1","parentId":None,"timestamp":"2026-06-02T10:00:01.000Z","message":{"role":"user","content":"Add a farewell helper and commit it."}},
    {"type":"message","id":"a1","parentId":"u1","timestamp":"2026-06-02T10:00:02.000Z","message":{"role":"assistant","provider":"anthropic","model":"claude-sonnet-4","usage":{"input":100,"output":20,"cacheRead":0,"cacheWrite":0,"totalTokens":120,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"toolUse","content":[{"type":"text","text":"I'll edit src/app.py and commit the change."},{"type":"toolCall","id":"call-edit-1","name":"Edit","arguments":{"file_path":"src/app.py","old_string":"def greet(name: str) -> str:\n    return f'Hello, {name}'\n","new_string":"def greet(name: str) -> str:\n    return f'Hello, {name}'\n\ndef farewell(name: str) -> str:\n    return f'Goodbye, {name}'\n"}}]}},
    {"type":"message","id":"t1","parentId":"a1","timestamp":"2026-06-02T10:00:03.000Z","message":{"role":"toolResult","toolCallId":"call-edit-1","toolName":"Edit","content":[{"type":"text","text":"updated src/app.py"}],"isError":False}},
    {"type":"message","id":"a2","parentId":"t1","timestamp":"2026-06-02T10:00:04.000Z","message":{"role":"assistant","provider":"anthropic","model":"claude-sonnet-4","usage":{"input":120,"output":30,"cacheRead":0,"cacheWrite":0,"totalTokens":150,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"stop","content":[{"type":"text","text":"Committed farewell helper."}]}},
]
session_file.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")
base = {"session_id": session_id, "session_file": str(session_file), "cwd": str(project)}
record_event({**base, "event":"session_start", "sequence":1, "data":{}}, fail_open=False)
record_event({**base, "event":"provider_request", "sequence":2, "data":{"completeness":"full", "system":"You are Pi.", "messages":[{"role":"user","content":"Add a farewell helper and commit it."}], "tools":[{"name":"Edit", "input_schema":{"type":"object"}}]}}, fail_open=False)
record_event({**base, "event":"active_leaf", "sequence":3, "leaf_id":"a2", "data":{}}, fail_open=False)
record_event({**base, "event":"tool_pre", "sequence":4, "tool_call_id":"call-edit-1", "data":{"tool_name":"Edit", "input":{"file_path":"src/app.py", "old_string":"def greet(name: str) -> str:\n    return f'Hello, {name}'\n", "new_string":"def greet(name: str) -> str:\n    return f'Hello, {name}'\n\ndef farewell(name: str) -> str:\n    return f'Goodbye, {name}'\n"}}}, fail_open=False)
(project / "src" / "app.py").write_text(
    "def greet(name: str) -> str:\n"
    "    return f'Hello, {name}'\n\n"
    "def farewell(name: str) -> str:\n"
    "    return f'Goodbye, {name}'\n",
    encoding="utf-8",
)
record_event({**base, "event":"tool_post", "sequence":5, "tool_call_id":"call-edit-1", "data":{"tool_name":"Edit", "input":{"file_path":"src/app.py"}, "result":{"is_error":False, "content":"updated src/app.py"}, "file_path":"src/app.py", "capture_status":"captured", "confidence":"high"}}, fail_open=False)
run(["git", "add", "src/app.py"])
run(["git", "commit", "-m", "pi linear edit"])
head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
record_event({**base, "event":"session_shutdown", "sequence":6, "final":True, "data":{"git":{"head":head, "branch":branch}, "reason":"synthetic_otbox"}}, fail_open=False)
run([__CLI__, "_ingest-session", str(session_file), "--agent", "pi", "--project", str(project)])
run([__CLI__, "setup", "watcher", "tick", "--project", str(project), "--json"])
run([__CLI__, "trail", "mature", "--commit", head, "--project", str(project), "--json"])
run([__CLI__, "bucket", "repair", "--json"])
run([__CLI__, "trace", "index", "rebuild"])
print(json.dumps({"session_file": str(session_file), "head": head, "branch": branch}, sort_keys=True))
'''.replace("__PROJECT__", repr(project)).replace("__HOME__", repr(home)).replace("__CLI__", repr(cli))

    result = driver.exec(box, [py, "-c", script], timeout=180)
    _check(result, "synthetic-pi-capture", checkpoint_name)


def _first_trace_record_payload_from_capture(capture_name: str, *, agent_name: str | None = None) -> dict | None:
    archive, _metadata = _artifact_paths(capture_name)
    if not archive.exists():
        return None
    with tarfile.open(archive, "r:gz") as tar:
        members = sorted(
            member
            for member in tar.getmembers()
            if member.isfile()
            and "/home/.opentraces/projects/" in member.name
            and "/traces/" in member.name
            and member.name.endswith(".jsonl")
        )
        for member in members:
            handle = tar.extractfile(member)
            if handle is None:
                continue
            for raw_line in handle:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                if agent_name and _trace_agent_name(payload) != agent_name:
                    continue
                return payload
    return None


def _project_slug_from_restored_box(driver: Driver, box: Box) -> str:
    state_dir, _state = read_state_json(driver, box)
    return Path(state_dir).name


def _inject_trace_record_into_bucket(
    driver: Driver,
    box: Box,
    *,
    project_slug: str,
    record_payload: dict,
) -> dict:
    trace_id = str(record_payload.get("trace_id") or "")
    if not trace_id:
        raise CheckpointError("mixed-agent composite record has no trace_id")
    payload_path = box.root / "tmp" / f"{trace_id}.trace.json"
    driver.put_text(box, str(payload_path), json.dumps(record_payload))
    script = """
import json
from pathlib import Path

from opentraces_schema import TraceRecord
from opentraces.core.bucket_store import bucket_manifest, project_per_trace_exports

record = TraceRecord.model_validate_json(Path(__PAYLOAD__).read_text(encoding="utf-8"))
summary = project_per_trace_exports(
    Path(__PROJECT__),
    project_slug=__PROJECT_SLUG__,
    trace_id=record.trace_id,
    record=record,
)
manifest = bucket_manifest(write=True, include_objects=False)
print(json.dumps({
    "trace_id": record.trace_id,
    "agent_name": record.agent.name if record.agent else "",
    "project_slug": __PROJECT_SLUG__,
    "summary": summary,
    "trace_count": len(manifest.get("traces") or []),
}, sort_keys=True))
""".replace("__PAYLOAD__", repr(str(payload_path))).replace(
        "__PROJECT__", repr(str(box.project))
    ).replace(
        "__PROJECT_SLUG__", repr(project_slug)
    )
    python = str(_PYTHON) if _PYTHON.exists() else "python3"
    result = driver.exec(box, [python, "-c", script])
    if not result.ok:
        raise CheckpointError(
            "failed to inject mixed-agent TraceRecord into bucket: "
            f"{result.stderr.strip() or result.stdout.strip() or '<no output>'}"
        )
    try:
        injected = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CheckpointError(f"mixed-agent injection emitted malformed JSON: {exc}") from None
    return injected if isinstance(injected, dict) else {}


def _repair_bucket_projection(driver: Driver, box: Box) -> None:
    result = driver.exec(box, [*driver.cli_argv(box), "bucket", "repair", "--json"])
    if result.ok:
        return
    raise CheckpointError(
        "failed to repair bucket projection before mixed-agent injection: "
        f"{result.stderr.strip() or result.stdout.strip() or '<no output>'}"
    )


def _bucket_verify_full(driver: Driver, box: Box) -> None:
    result = driver.exec(box, [*driver.cli_argv(box), "bucket", "verify", "--full", "--json"])
    if result.ok:
        return
    raise CheckpointError(
        "mixed-agent bucket verify --full failed: "
        f"{result.stderr.strip() or result.stdout.strip() or '<no output>'}"
    )


def register_pi_capture_checkpoint(
    *,
    name: str,
    capture_name: str,
    description: str,
    extra_provides: Mapping[str, object] | None = None,
    synthetic: bool = True,
) -> None:
    """Register an artifact-preferred Pi checkpoint.

    Unlike the Codex lane, Pi v1 includes a deterministic synthetic fallback
    for the linear/provider/branch fixtures so tier-0 otbox can exercise the
    real parser and bucket path without a live Pi binary.
    """

    def _captured_pi_delta(driver: Driver, box: Box) -> None:
        cap_meta = restore_from_capture(driver, box, capture_name)
        if cap_meta is None:
            if not synthetic:
                box.notes[PI_AUDIT_KEY] = _missing_artifact_audit(capture_name)
                return
            _run_synthetic_pi_capture(driver, box, capture_name=capture_name, checkpoint_name=name)
            cap_meta = None
        box.notes[PI_AUDIT_KEY] = _derive_audit_from_box(
            driver,
            box,
            cap_meta,
            checkpoint_name=name,
            capture_name=capture_name,
        )

    provides: dict[str, object] | None = None
    if synthetic or artifact_exists(capture_name):
        provides = {
            "captured_traces": 1,
            "context_tree_built": True,
            "survival_states": ["alive_on_path"],
            "branch_commits": 1,
            "agents": ["pi"],
        }
        if extra_provides:
            provides.update(extra_provides)

    register(
        Checkpoint(
            name=name,
            composed_from="c-installed-source",
            delta=_captured_pi_delta,
            cache=False,
            description=description,
            provides=provides,
        )
    )


def register_mixed_agent_pi_bucket_checkpoint(
    *,
    name: str,
    pi_capture_name: str,
    codex_capture_name: str,
    claude_capture_name: str,
    description: str,
) -> None:
    """Register a Claude/Codex/Pi mixed bucket checkpoint.

    Default path restores/synthesizes the Pi capture, then injects frozen
    Claude/Codex TraceRecords when artifacts are available. If those artifacts
    are absent, deterministic lightweight TraceRecords are injected instead so
    the mixed-agent bucket manifest and ``bucket verify --full`` are still
    exercised offline.
    """

    def _fallback_record(agent_name: str, trace_id: str, *, version: str, model: str) -> dict:
        return {
            "trace_id": trace_id,
            "session_id": f"session-{trace_id}",
            "schema_version": "0.5.0",
            "timestamp_start": "2026-06-02T10:00:00Z",
            "task": {"description": f"Synthetic {agent_name} bucket parity trace"},
            "agent": {"name": agent_name, "version": version, "model": model},
            "steps": [
                {"step_index": 1, "role": "user", "content": "Prepare mixed-agent bucket parity."},
                {"step_index": 2, "role": "agent", "content": "Recorded bucket parity evidence."},
            ],
        }

    def _mixed_delta(driver: Driver, box: Box) -> None:
        cap_meta = restore_from_capture(driver, box, pi_capture_name)
        if cap_meta is None:
            _run_synthetic_pi_capture(driver, box, capture_name=pi_capture_name, checkpoint_name=name)
            cap_meta = None
        pi_audit = _derive_audit_from_box(
            driver,
            box,
            cap_meta,
            checkpoint_name=name,
            capture_name=pi_capture_name,
        )
        project_slug = _project_slug_from_restored_box(driver, box)
        _repair_bucket_projection(driver, box)

        claude_payload = _first_trace_record_payload_from_capture(claude_capture_name, agent_name="claude-code") or _fallback_record(
            "claude-code", "trace-claude-synthetic", version="2.1.143", model="anthropic/claude-sonnet-4-20250514"
        )
        codex_payload = _first_trace_record_payload_from_capture(codex_capture_name, agent_name="codex-cli") or _fallback_record(
            "codex-cli", "trace-codex-synthetic", version="0.31.0", model="openai/gpt-5-codex"
        )
        injected_claude = _inject_trace_record_into_bucket(
            driver, box, project_slug=project_slug, record_payload=claude_payload
        )
        injected_codex = _inject_trace_record_into_bucket(
            driver, box, project_slug=project_slug, record_payload=codex_payload
        )
        _bucket_verify_full(driver, box)
        box.notes[PI_AUDIT_KEY] = {
            **pi_audit,
            "scenario_name": "mixed-agent-bucket-parity-pi",
            "mixed_agent_trace_ids": {
                "pi": pi_audit.get("trace_id") or "",
                "claude-code": injected_claude.get("trace_id") or "",
                "codex-cli": injected_codex.get("trace_id") or "",
            },
            "mixed_agent_project_slug": project_slug,
            "mixed_agent_trace_count": injected_codex.get("trace_count"),
            "capture_metadata": {
                "source": "composite_artifacts_or_synthetic",
                "pi": pi_audit.get("capture_metadata") or {},
                "claude_capture_name": claude_capture_name,
                "codex_capture_name": codex_capture_name,
            },
        }

    register(
        Checkpoint(
            name=name,
            composed_from="c-installed-source",
            delta=_mixed_delta,
            cache=False,
            description=description,
            provides={"captured_traces": 3, "context_tree_built": True, "agents": ["claude-code", "codex-cli", "pi"]},
        )
    )


def register_pi_full_parity_checkpoint(
    *,
    name: str,
    capture_names: list[str],
    representative_capture_name: str,
    description: str,
) -> None:
    def _full_delta(driver: Driver, box: Box) -> None:
        cap_meta = restore_from_capture(driver, box, representative_capture_name)
        if cap_meta is None:
            _run_synthetic_pi_capture(driver, box, capture_name=representative_capture_name, checkpoint_name=name)
            cap_meta = None
        audit = _derive_audit_from_box(
            driver,
            box,
            cap_meta,
            checkpoint_name=name,
            capture_name=representative_capture_name,
        )
        box.notes[PI_AUDIT_KEY] = {
            **audit,
            "scenario_name": "pi-full-parity-latest",
            "aggregate_capture_names": list(capture_names),
            "capture_metadata": {
                "source": "aggregate_artifacts_or_synthetic",
                "representative": audit.get("capture_metadata") or {},
                "capture_count": len(capture_names),
            },
        }

    register(
        Checkpoint(
            name=name,
            composed_from="c-installed-source",
            delta=_full_delta,
            cache=False,
            description=description,
            provides={"captured_traces": 1, "context_tree_built": True, "agents": ["pi"]},
        )
    )
