"""`c-captured-with-secrets` — plan 068 M68-2.

A sibling of ``c-captured-real-session`` that exercises the opentraces
security pipeline end-to-end through a real captured agent session after
explicitly enabling the regex + entropy tools.
The corpus (``session-with-secrets``) has the agent create a
``src/config.py`` whose content carries synthetic but
detector-shaped credentials (fake OpenAI API key, fake GitHub PAT,
hex blob). After the standard capture chain runs the checkpoint
inspects the trace JSONL on disk and records:

  * how many security tools fingerprinted the trace
    (``metadata.security.tools_applied``),
  * whether the secret literal made it through to the saved trace
    bytes (``secret_present_in_disk``),
  * the tool name list.

This is intentionally an audit, not a gate: the checkpoint records
whatever the explicitly enabled pipeline did and the pytest layer asserts
the desired behaviour. That keeps the corpus stable while the registry
and per-tool enabled defaults evolve.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    harness_interpreter,
    harness_source_with_shebang,
    capture_metadata_from_artifact,
    check as _check_helper,
    encode_claude_path,
    git as _git_helper,
    read_state_json,
    restore_from_capture,
    synthetic_capture_metadata,
    trace_for_session,
    traces_by_created_at,
    transcript_path_for_session,
)

_CHECKPOINT_NAME = "c-captured-with-secrets"
# Plan B0 flip: the real-agent artifact produced by
# `make capture-refresh SCENARIO=claude-with-secrets` (scenario-named dir).
_CAPTURE_NAME = "claude-with-secrets"


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_CHECKPOINT_NAME, label=label)


def _git(driver: Driver, box: Box, *args: str):
    return _git_helper(driver, box, *args, checkpoint=_CHECKPOINT_NAME)

_SESSION_NAME = "session-with-secrets"
_SESSION_ID = "sess-otbox-session-with-secrets"

# A canary fragment of the OPENAI_API_KEY literal. We only check for a
# substring so a partial mid-key redaction still reads as "the secret
# survived" — the boundary we care about is "is the bearer literal
# usable as-is".
_SECRET_CANARY = "sk-test-abc123def456"

# tests/otbox/fake_harnesses/claude
_HARNESS_SRC = Path(__file__).resolve().parents[1] / "fake_harnesses" / "claude"
# tests/otbox/fixtures/sessions/
_SESSIONS_SRC = Path(__file__).resolve().parents[1] / "fixtures" / "sessions"


def _security_audit_fields(driver: Driver, box: Box, trace_jsonl: str) -> dict:
    """Scan the on-disk trace JSONL for security-pipeline fingerprints.

    Shared by the synthetic and artifact-restored paths so both audits
    carry the same shape: ``tools_applied``,
    ``security_findings_count``, ``secret_present_in_disk``,
    ``trace_jsonl_readable``.
    """
    trace_raw = driver.exec(box, ["cat", trace_jsonl])
    tools_applied: list[str] = []
    findings_count = 0
    if not trace_raw.ok:
        return {
            "trace_jsonl_readable": False,
            "tools_applied": tools_applied,
            "security_findings_count": findings_count,
            "secret_present_in_disk": False,
        }
    # The file is a single TraceRecord serialized as JSONL — usually
    # one line, but defensively walk every line.
    for line in trace_raw.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        sec = ((obj.get("metadata") or {}).get("security") or {})
        applied = sec.get("tools_applied") or []
        if isinstance(applied, list):
            for name in applied:
                if isinstance(name, str) and name not in tools_applied:
                    tools_applied.append(name)
        tools_map = sec.get("tools") or {}
        if isinstance(tools_map, dict):
            for entry in tools_map.values():
                if isinstance(entry, dict):
                    try:
                        findings_count += int(entry.get("findings_count") or 0)
                    except (TypeError, ValueError):
                        pass
    return {
        "trace_jsonl_readable": True,
        "tools_applied": tools_applied,
        "security_findings_count": findings_count,
        "secret_present_in_disk": _SECRET_CANARY in trace_raw.stdout,
    }


def _derive_secrets_audit_from_restored_box(
    driver: Driver, box: Box, cap_meta: dict,
) -> dict:
    """Re-derive the secrets audit after an artifact restore.

    Plan 072 R2 — same on-disk sources the synthetic path ends on:
    state.json for the trace handle, the trace JSONL for the
    security-pipeline fingerprints. Real-agent artifacts mint UUID
    session ids, so the trace is resolved by capture order when the
    synthetic id is absent.
    """
    state_dir, state = read_state_json(driver, box)
    trace_id, _ = trace_for_session(state, _SESSION_ID)
    session_id = _SESSION_ID
    trace: dict = {}
    if trace_id:
        for entry in (state.get("traces") or {}).values():
            if entry.get("trace_id") == trace_id:
                trace = entry
                break
    else:
        ordered = traces_by_created_at(state)
        if ordered:
            trace = ordered[-1]
            trace_id = trace.get("trace_id")
            session_id = str(trace.get("session_id") or "")
    if not trace_id:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME} artifact restore produced no traces; "
            f"state at {state_dir}/state.json contained "
            f"{len(state.get('traces') or {})} entries"
        )

    trace_jsonl = (
        trace.get("file_path") or f"{state_dir}/traces/{trace_id}.jsonl"
    )
    paths = driver.paths(box)
    project = paths["project"]
    head = driver.exec(box, ["git", "-C", project, "rev-parse", "HEAD"])
    commit_sha = head.stdout.strip() if head.ok else ""
    transcript_path = transcript_path_for_session(driver, box, session_id)

    return {
        "session_id": session_id,
        "session_corpus": str(cap_meta.get("scenario_name") or _SESSION_NAME),
        "trace_id": trace_id,
        "commit_sha": commit_sha,
        "transcript_path": transcript_path,
        "state_dir": state_dir,
        "trace_jsonl_path": trace_jsonl,
        **_security_audit_fields(driver, box, trace_jsonl),
        "secret_canary": _SECRET_CANARY,
        "capture_metadata": capture_metadata_from_artifact(cap_meta),
    }


def _captured_with_secrets_delta(driver: Driver, box: Box) -> None:
    # Plan 072 R3 — artifact-preferred, synthetic-fallback. Contract
    # gate: the artifact must already carry security-pipeline
    # fingerprints (this checkpoint advertises has_security_findings);
    # a capture taken with the security tools off falls back to the
    # synthetic chain, which enables them explicitly before ingest.
    cap_meta = restore_from_capture(
        driver, box, _CAPTURE_NAME, require_security_fingerprints=True,
    )
    if cap_meta is not None:
        box.notes["c_captured_with_secrets_audit"] = (
            _derive_secrets_audit_from_restored_box(driver, box, cap_meta)
        )
        return

    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]

    # 1. Real git project with a seed commit (same shape as
    #    c-captured-real-session).
    driver.mkdir(box, project)
    driver.put_text(
        box, f"{project}/README.md",
        "# otbox captured-with-secrets checkpoint\n\n"
        "Driver-mediated fixture for plan 068 M68-2 — exercises the security pipeline.\n",
    )
    driver.mkdir(box, f"{project}/src")
    driver.put_text(
        box, f"{project}/src/app.py",
        'def greet(name: str) -> str:\n    return f"hello, {name}"\n',
    )
    _git(driver, box, "init", "-q")
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "seed: initial project")

    # 2. opentraces init.
    cli = resolve_cli_argv()
    _check(
        driver.exec(box, [*cli, "init", "--start-fresh", "--agent", "claude-code"]),
        "opentraces init",
    )

    # 3. opentraces setup git, then explicitly opt into the local
    #    deterministic security tools. The shipped default is raw capture;
    #    this checkpoint is the opt-in security-finding fixture.
    _check(
        driver.exec(box, [*cli, "setup", "git"]),
        "opentraces setup git",
    )
    testvenv_python = f"{project}/.testvenv/bin/python"
    _check(
        driver.exec(box, [
            testvenv_python,
            "-c",
            (
                "from opentraces.core.config import load_config, save_config; "
                "cfg = load_config(); "
                "cfg.security.regex.enabled = True; "
                "cfg.security.entropy.enabled = True; "
                "save_config(cfg)"
            ),
        ]),
        "enable regex + entropy security tools",
    )

    # 4. Place the fake harness + corpus on the box.
    bin_dir = f"{home}/bin"
    sessions_dir = f"{home}/share/otbox/sessions"
    driver.mkdir(box, bin_dir)
    driver.mkdir(box, sessions_dir)
    harness_dst = f"{bin_dir}/claude"
    driver.put_text(box, harness_dst, harness_source_with_shebang(_HARNESS_SRC))
    _check(
        driver.exec(box, ["chmod", "+x", harness_dst]),
        "chmod +x harness",
    )

    session_dst = f"{sessions_dir}/{_SESSION_NAME}"
    driver.mkdir(box, session_dst)
    corpus_src = _SESSIONS_SRC / _SESSION_NAME
    for child in corpus_src.iterdir():
        target = f"{session_dst}/{child.name}"
        if child.is_file():
            driver.put_text(box, target, child.read_text())

    # 5. Run the harness.
    encoded_project = encode_claude_path(project)
    transcript_path = (
        f"{home}/.claude/projects/{encoded_project}/{_SESSION_ID}.jsonl"
    )
    _check(
        driver.exec(box, [harness_interpreter(), harness_dst], env_extra={
            "OTBOX_FAKE_SESSION": _SESSION_NAME,
            "OTBOX_FAKE_SESSIONS_DIR": sessions_dir,
            "OTBOX_PROJECT_DIR": project,
            "OTBOX_TRANSCRIPT_PATH": transcript_path,
        }),
        "fake harness invocation",
    )

    # 6. _ingest-session — this is the step that drives the explicitly
    #    enabled security pipeline. The saved JSONL under
    #    ``<state_dir>/traces/<trace_id>.jsonl`` is what we audit
    #    below.
    _check(
        driver.exec(box, [
            *cli, "_ingest-session", transcript_path,
            "--project", project,
        ]),
        "_ingest-session",
    )

    # 7. Commit + post-commit hook.
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "Add config.py with API keys")
    commit_sha = _git(driver, box, "rev-parse", "HEAD").stdout.strip()

    # 8. Watcher tick (informational; we don't gate on it here — the
    #    security pipeline already ran at ingest time).
    driver.exec(box, [
        *cli, "setup", "watcher", "tick", "--project", project, "--json",
    ])

    # Resolve the project's state.json to find the trace_id and the
    # canonical traces dir.
    state_dirs = driver.glob(box, f"{paths['opentraces_dir']}/projects/*")
    if len(state_dirs) != 1:
        raise CheckpointError(
            f"expected exactly one opted-in project after init, "
            f"got {len(state_dirs)} under {paths['opentraces_dir']}/projects"
        )
    state_dir = state_dirs[0]
    state_path = f"{state_dir}/state.json"
    state_raw = driver.exec(box, ["cat", state_path])
    if not state_raw.ok:
        raise CheckpointError(
            f"could not read project state at {state_path}: "
            f"{state_raw.stderr.strip() or '<no stderr>'}"
        )
    try:
        state = json.loads(state_raw.stdout)
    except json.JSONDecodeError as exc:
        raise CheckpointError(
            f"malformed state.json at {state_path}: {exc}"
        ) from None

    trace_id: str | None = None
    for trace in (state.get("traces") or {}).values():
        if trace.get("session_id") == _SESSION_ID:
            trace_id = trace.get("trace_id")
            break
    if not trace_id:
        traces = list((state.get("traces") or {}).values())
        if traces:
            trace_id = traces[0].get("trace_id")
    if not trace_id:
        raise CheckpointError(
            "c-captured-with-secrets ingested no traces; "
            f"state at {state_path} contained "
            f"{len(state.get('traces') or {})} entries"
        )

    # Audit the on-disk trace JSONL. Each trace lives at
    # <state_dir>/traces/<trace_id>.jsonl.
    trace_jsonl = f"{state_dir}/traces/{trace_id}.jsonl"

    box.notes["c_captured_with_secrets_audit"] = {
        "session_id": _SESSION_ID,
        "session_corpus": _SESSION_NAME,
        "trace_id": trace_id,
        "commit_sha": commit_sha,
        "transcript_path": transcript_path,
        "state_dir": state_dir,
        "trace_jsonl_path": trace_jsonl,
        **_security_audit_fields(driver, box, trace_jsonl),
        "secret_canary": _SECRET_CANARY,
        # Plan 072 R4 — provenance marker so consumers can branch on
        # artifact-restored vs synthetic-derived audits.
        "capture_metadata": synthetic_capture_metadata(),
    }


register(
    Checkpoint(
        name="c-captured-with-secrets",
        composed_from="c-installed-source",
        delta=_captured_with_secrets_delta,
        # cache=False: artifact presence/freshness live outside the
        # checkpoint source hash (same rationale as the codex/pi lanes).
        cache=False,
        description=(
            "c-installed-source + a real captured Claude Code session "
            "whose Edit creates src/config.py with synthetic OpenAI / "
            "GitHub / hex secrets after opting into regex + entropy. "
            "Audits the resulting on-disk trace JSONL for security-pipeline "
            "fingerprints and records "
            "whether the secret literal survived ingestion — the seed "
            "for plan 068's security-pipeline UAT journeys."
        ),
        # Plan 069 R3: the captured Edit creates src/config.py, so the
        # patch matures to ``alive_on_path``; the secret-shaped content
        # is what makes this checkpoint distinct.
        provides={
            "captured_traces": 1,
            "survival_states": ["alive_on_path"],
            "branch_commits": 1,
            "has_security_findings": True,
        },
    )
)
