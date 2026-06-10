"""`c-legacy-v033` + `c-legacy-v033-upgraded` — plan 085 R2/R3.

otbox's first *previous-version* world. Every other checkpoint seeds a
current (0.4-shaped) box; these two stand up a real opentraces **0.3.3**
world and drive the 0.4 upgrade across it.

``c-legacy-v033``
    Restores the frozen v0.3.3 fixture (built by the real ``v0.3.3`` CLI
    in ``tests/migration/build_legacy_world.py``, committed under
    ``tests/migration/fixtures/legacy_world_v033/``) in place: a git
    project with a schema-``0.3.0`` JSONL trace carrying a populated
    ``outcome.patch``, a 0.3.3-shaped ``config.json`` + ``state.json``,
    and the ``.opentraces.json`` marker. NO bucket, NO trails event log,
    NO context tree, NO local datasets — exactly what a real 0.3.3 user
    had on disk. The restore is byte-for-byte the same materialisation
    ``tests/migration/restore_legacy_world.py`` performs for pytest, just
    driver-mediated into the box so it works on Tier 0 + Tier 1.

    Provides ``legacy_world_restored`` / ``pre_migration_schema`` /
    ``pre_migration_cli`` for the migration-suite precondition vocab
    (plan 085 R4); journeys S1/S5/S8/S10/S11 fork from here and run the
    **0.4 CLI** (``driver.cli_argv`` resolves the in-tree 0.4 entrypoint)
    over the restored 0.3.3 box.

``c-legacy-v033-upgraded``
    Composes on ``c-legacy-v033`` and runs a fresh 0.4 capture inside the
    legacy repo (the ``simple-refactor`` fake-harness session — it owns
    ``src/app.py`` end to end, so it coexists with the legacy root
    ``app.py``). This is the literal upgrade path: an existing 0.3.3 user
    pip-installs 0.4 and keeps working. After the delta the box has the
    legacy 0.3.0 trace AND a new 0.4 trace, with
    ``refs/opentraces/local/events/v1`` + ``refs/notes/opentraces``
    created additively over the pre-existing repo history.

    Provides ``migration_applied`` (+ ``no_data_loss`` /
    ``migration_idempotent``); journeys S9/S12 fork from here.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..drivers.base import Driver
from ..env import REPO_ROOT, Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    harness_interpreter,
    harness_source_with_shebang,
    check as _check_helper,
    encode_claude_path,
    git as _git_helper,
)

# --------------------------------------------------------------------------
# constants pinned to the frozen fixture (PROVENANCE.json)
# --------------------------------------------------------------------------
_FIXTURE = REPO_ROOT / "tests" / "migration" / "fixtures" / "legacy_world_v033"

_BASE = "c-legacy-v033"
_UPGRADED = "c-legacy-v033-upgraded"

PRE_MIGRATION_SCHEMA = "0.3.0"
PRE_MIGRATION_CLI = "0.3.3"
LEGACY_TRACE_ID = "6961102e-e930-4c67-aeff-6335858fcdea"
LEGACY_SESSION_ID = "sess-legacy-v033-refactor"
LEGACY_SLUG = "project-27e5e3e0"

# The 0.4 upgrade capture reuses the simple-refactor corpus — it creates
# + edits its own ``src/app.py``, so it never collides with the legacy
# root ``app.py`` the fixture ships.
_SESSION_NAME = "simple-refactor"
_SESSION_ID = "sess-otbox-simple-refactor"
EDIT_STEP_INDEX = 3
_HARNESS_SRC = REPO_ROOT / "tests" / "otbox" / "fake_harnesses" / "claude"
_SESSIONS_SRC = REPO_ROOT / "tests" / "otbox" / "fixtures" / "sessions"


# --------------------------------------------------------------------------
# c-legacy-v033 — restore the frozen 0.3.3 world
# --------------------------------------------------------------------------
def _check(result, label: str, checkpoint: str = _BASE) -> None:
    _check_helper(result, checkpoint=checkpoint, label=label)


def _git(driver: Driver, box: Box, *args: str, checkpoint: str = _BASE):
    return _git_helper(driver, box, *args, checkpoint=checkpoint)


def _restore_tree(
    driver: Driver, box: Box, src_root: Path, dst_root: str, home: str, project: str,
) -> int:
    """Copy + rehydrate every file under ``src_root`` into ``dst_root``.

    Mirrors ``restore_legacy_world._rehydrate``: the frozen fixture
    scrubbed the original absolute paths to ``$LEGACY_HOME`` /
    ``$LEGACY_PROJECT`` placeholders; we expand them back to the box's
    live paths so the 0.4 CLI resolves state from disk.
    """
    count = 0
    for path in sorted(src_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src_root)
        text = path.read_text(encoding="utf-8")
        text = text.replace("$LEGACY_HOME", home).replace("$LEGACY_PROJECT", project)
        driver.put_text(box, f"{dst_root}/{rel.as_posix()}", text)
        count += 1
    return count


def _legacy_v033_delta(driver: Driver, box: Box) -> None:
    if not _FIXTURE.exists():
        raise CheckpointError(
            f"frozen legacy fixture missing at {_FIXTURE}; rebuild via "
            "tests/migration/build_legacy_world.py"
        )

    paths = driver.paths(box)
    home = paths["home"]
    project = paths["project"]
    opentraces_dir = paths["opentraces_dir"]

    # 1. Restore ~/.opentraces (config.json + projects/<slug>/...) and the
    #    project tree (.opentraces.json marker + app.py + skill packages).
    #    The box.project already carries .testvenv from c-installed-source;
    #    a per-file copy merges the fixture in without disturbing it.
    n_home = _restore_tree(
        driver, box, _FIXTURE / "opentraces_home", opentraces_dir, home, project,
    )
    n_proj = _restore_tree(
        driver, box, _FIXTURE / "project", project, home, project,
    )
    if n_home == 0 or n_proj == 0:
        raise CheckpointError(
            f"legacy fixture restore copied {n_home} home / {n_proj} project "
            "files; expected both non-zero"
        )

    # 2. Keep the build venv out of the restored repo's history, then
    #    re-init git so the 0.4 capture/anchor paths have a real repo to
    #    read (the fixture froze content, not the .git dir).
    driver.put_text(box, f"{project}/.gitignore", ".testvenv/\n")
    _git(driver, box, "init", "-q")
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "restored legacy v0.3.3 world")
    root_commit = _git(driver, box, "rev-parse", "HEAD").stdout.strip()

    box.notes["c_legacy_v033_audit"] = {
        "pre_migration_schema": PRE_MIGRATION_SCHEMA,
        "pre_migration_cli": PRE_MIGRATION_CLI,
        "legacy_trace_id": LEGACY_TRACE_ID,
        "legacy_session_id": LEGACY_SESSION_ID,
        "legacy_slug": LEGACY_SLUG,
        "root_commit_sha": root_commit,
        "home_files_restored": n_home,
        "project_files_restored": n_proj,
    }


# --------------------------------------------------------------------------
# c-legacy-v033-upgraded — a fresh 0.4 capture inside the legacy repo
# --------------------------------------------------------------------------
def _legacy_v033_upgraded_delta(driver: Driver, box: Box) -> None:
    paths = driver.paths(box)
    home = paths["home"]
    project = paths["project"]

    cli = resolve_cli_argv()

    # Record the pre-capture ref state so the journey can prove the 0.4
    # capture was strictly additive (the repo's HEAD + existing refs are
    # untouched; only opentraces refs appear).
    head_before = _git(
        driver, box, "rev-parse", "HEAD", checkpoint=_UPGRADED,
    ).stdout.strip()

    # 1. Install the 0.4 post-commit hook (refs/notes/opentraces). The
    #    project is already opted in (legacy marker), so no `init`.
    _check(
        driver.exec(box, [*cli, "setup", "git"]),
        "opentraces setup git", checkpoint=_UPGRADED,
    )

    # 2. Stage the fake harness + corpus on the box (Tier 1 has no
    #    REPO_ROOT, so the corpus travels with the harness).
    bin_dir = f"{home}/bin"
    sessions_dir = f"{home}/share/otbox/sessions"
    driver.mkdir(box, bin_dir)
    driver.mkdir(box, sessions_dir)
    harness_dst = f"{bin_dir}/claude"
    driver.put_text(box, harness_dst, harness_source_with_shebang(_HARNESS_SRC))
    _check(
        driver.exec(box, ["chmod", "+x", harness_dst]),
        "chmod +x harness", checkpoint=_UPGRADED,
    )
    session_dst = f"{sessions_dir}/{_SESSION_NAME}"
    driver.mkdir(box, session_dst)
    for child in (_SESSIONS_SRC / _SESSION_NAME).iterdir():
        if child.is_file():
            driver.put_text(box, f"{session_dst}/{child.name}", child.read_text())

    # 3. Run the harness — writes the transcript to the canonical Claude
    #    path and applies the deterministic src/app.py edit.
    encoded_project = encode_claude_path(project)
    transcript_path = (
        f"{home}/.claude/projects/{encoded_project}/{_SESSION_ID}.jsonl"
    )
    testvenv_python = f"{project}/.testvenv/bin/python"
    _check(
        driver.exec(box, [harness_interpreter(), harness_dst], env_extra={
            "OTBOX_FAKE_SESSION": _SESSION_NAME,
            "OTBOX_FAKE_SESSIONS_DIR": sessions_dir,
            "OTBOX_PROJECT_DIR": project,
            "OTBOX_TRANSCRIPT_PATH": transcript_path,
        }),
        "fake harness invocation", checkpoint=_UPGRADED,
    )

    # 4. Ingest -> commit -> mature -> index, exactly as
    #    c-captured-real-session does. The Trace Patch events land in
    #    refs/opentraces/local/events/v1; the post-commit hook records
    #    the commit<->trace note.
    _check(
        driver.exec(box, [*cli, "_ingest-session", transcript_path, "--project", project]),
        "_ingest-session", checkpoint=_UPGRADED,
    )
    _git(driver, box, "add", "-A", checkpoint=_UPGRADED)
    _git(driver, box, "commit", "-q", "-m", "Add farewell() helper", checkpoint=_UPGRADED)
    commit_sha = _git(driver, box, "rev-parse", "HEAD", checkpoint=_UPGRADED).stdout.strip()

    _check(
        driver.exec(box, [*cli, "setup", "watcher", "tick", "--project", project, "--json"]),
        "setup watcher tick", checkpoint=_UPGRADED,
    )
    _check(
        driver.exec(box, [
            *cli, "trail", "mature", "--commit", commit_sha, "--project", project, "--json",
        ]),
        "trail mature", checkpoint=_UPGRADED,
    )
    _check(
        driver.exec(box, [*cli, "trace", "index", "rebuild"]),
        "trace index rebuild", checkpoint=_UPGRADED,
    )

    # Resolve the new 0.4 trace_id from the project state.
    new_trace_id = ""
    state_dirs = driver.glob(box, f"{paths['opentraces_dir']}/projects/*")
    for state_dir in state_dirs:
        raw = driver.exec(box, ["cat", f"{state_dir}/state.json"])
        if not raw.ok:
            continue
        try:
            state = json.loads(raw.stdout)
        except json.JSONDecodeError:
            continue
        for trace in (state.get("traces") or {}).values():
            if trace.get("session_id") == _SESSION_ID:
                new_trace_id = trace.get("trace_id") or ""
                break
    if not new_trace_id:
        raise CheckpointError(
            f"{_UPGRADED}: 0.4 capture ingested no trace for session {_SESSION_ID}"
        )

    base_audit = box.notes.get("c_legacy_v033_audit") or {}
    box.notes["c_legacy_v033_upgraded_audit"] = {
        **base_audit,
        "migration_applied": True,
        "head_before_capture": head_before,
        "new_trace_id": new_trace_id,
        "new_session_id": _SESSION_ID,
        "new_commit_sha": commit_sha,
        "edit_step_index": EDIT_STEP_INDEX,
        "transcript_path": transcript_path,
    }


# --------------------------------------------------------------------------
# c-legacy-v033-otel-upgraded — a real-OTLP context capture inside the legacy
# repo, driven deterministically with synthetic OTel envelopes (no real claude,
# no network). The OTLP receiver -> mapper -> emitter chain is exercised in full;
# only the agent that would normally EMIT the OTel is synthetic. This is the
# CI-safe Phase-2 "real-OTLP upgraded checkpoint": a legacy 0.3.3 world that now
# also carries an OTel-captured Context Tree (capture_method=otel).
# --------------------------------------------------------------------------
_OTEL_UPGRADED = "c-legacy-v033-otel-upgraded"
_OTEL_SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"
_OTEL_TRACE_ID = "otel-upgrade-trace-0001"


def _otel_attr(key, value):
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, list):
        return {"key": key, "value": {"arrayValue": {
            "values": [{"stringValue": str(v)} for v in value]}}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _otel_envelopes():
    sid = _OTEL_SESSION_ID
    res = lambda: {"attributes": [_otel_attr("session.id", sid)]}
    span = {"resourceSpans": [{"resource": res(), "scopeSpans": [{"spans": [{
        "name": "claude_code.llm_request",
        "attributes": [
            _otel_attr("session.id", sid),
            _otel_attr("prompt.id", "prompt-otel-0001"),
            _otel_attr("request_id", "req_otel_0001"),
            _otel_attr("gen_ai.system", "anthropic"),
            _otel_attr("gen_ai.request.model", "claude-opus-4-7"),
            _otel_attr("model", "claude-opus-4-7"),
            _otel_attr("input_tokens", 1234),
            _otel_attr("output_tokens", 56),
        ]}]}]}]}
    def _log(attrs):
        return {"resourceLogs": [{"resource": res(), "scopeLogs": [{
            "logRecords": [{"attributes": attrs}]}]}]}
    plugin = _log([
        _otel_attr("event.name", "plugin_loaded"), _otel_attr("session.id", sid),
        _otel_attr("plugin.name", "legacy-upgrade-plugin"), _otel_attr("has_hooks", True),
    ])
    hook = _log([
        _otel_attr("event.name", "hook_registered"), _otel_attr("session.id", sid),
        _otel_attr("hook_event", "PreToolUse"), _otel_attr("hook_matcher", "Bash"),
    ])
    return [("/v1/traces", span), ("/v1/logs", plugin), ("/v1/logs", hook)]


def _legacy_v033_otel_upgraded_delta(driver: Driver, box: Box) -> None:
    """Inject a synthetic-but-real-chain OTel Context Tree capture into the
    restored legacy repo. Local-driver only (Tier 0): writes context_* events to
    the box's refs/opentraces/local/events/v1 via flush_session_to_project."""
    import json as _json
    import socket as _socket
    import tempfile
    import time as _t
    import urllib.request
    from pathlib import Path as _P

    from opentraces.capture.otlp import start_receiver
    from opentraces.capture.otlp.emitter import (
        OTLPCaptureBuffer,
        flush_session_to_project,
    )

    paths = driver.paths(box)
    project = _P(paths["project"])
    if not project.exists():
        raise CheckpointError(
            f"{_OTEL_UPGRADED} requires a host-local box.project (Tier 0); "
            f"got non-local path {project}"
        )

    _s = _socket.socket()
    _s.bind(("127.0.0.1", 0))
    free_port = _s.getsockname()[1]
    _s.close()

    raw_bodies = _P(tempfile.mkdtemp(prefix="otbox-otel-raw-"))
    buffer = OTLPCaptureBuffer()
    receiver = start_receiver(port=free_port, bind="127.0.0.1", raw_bodies_dir=raw_bodies)
    receiver.set_callback(buffer.handle_envelope)
    if not receiver.wait_until_ready(timeout=5.0):
        raise CheckpointError(f"{_OTEL_UPGRADED}: OTLP receiver did not start")
    port = receiver.port
    try:
        for path, env in _otel_envelopes():
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=_json.dumps(env).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status != 200:
                    raise CheckpointError(
                        f"{_OTEL_UPGRADED}: receiver rejected envelope ({resp.status})"
                    )
        deadline = _t.time() + 3.0
        while _t.time() < deadline and _OTEL_SESSION_ID not in buffer.session_ids():
            _t.sleep(0.05)
        if _OTEL_SESSION_ID not in buffer.session_ids():
            raise CheckpointError(f"{_OTEL_UPGRADED}: session never reached buffer")
        report = flush_session_to_project(
            project_dir=project,
            trace_id=_OTEL_TRACE_ID,
            session_id=_OTEL_SESSION_ID,
            buffer=buffer,
        )
        if not report.get("ok") or report.get("layers_count") != 4:
            raise CheckpointError(f"{_OTEL_UPGRADED}: flush report not ok: {report}")
    finally:
        receiver.shutdown()

    base_audit = box.notes.get("c_legacy_v033_audit") or {}
    box.notes["c_legacy_v033_otel_audit"] = {
        **base_audit,
        "migration_applied": True,
        "otel_trace_id": _OTEL_TRACE_ID,
        "otel_session_id": _OTEL_SESSION_ID,
        "otel_layers": report.get("layers_count"),
        "otel_nodes": report.get("nodes_count"),
    }


register(
    Checkpoint(
        name=_BASE,
        composed_from="c-installed-source",
        delta=_legacy_v033_delta,
        cache=True,
        description=(
            "c-installed-source + a restored real opentraces 0.3.3 world "
            "(schema 0.3.0 JSONL trace with a populated outcome.patch, "
            "0.3.3 config/state, .opentraces.json marker; NO bucket/trails/"
            "context-tree/datasets). otbox's first previous-version world — "
            "the seed every 0.3.3->0.4 migration journey forks from."
        ),
        provides={
            "legacy_world_restored": True,
            "pre_migration_schema": PRE_MIGRATION_SCHEMA,
            "pre_migration_cli": PRE_MIGRATION_CLI,
            "captured_traces": 1,
        },
    )
)

register(
    Checkpoint(
        name=_UPGRADED,
        composed_from=_BASE,
        delta=_legacy_v033_upgraded_delta,
        cache=True,
        description=(
            "c-legacy-v033 + a fresh 0.4 capture driven inside the legacy "
            "repo (the upgrade-in-place path). The box holds the legacy "
            "0.3.0 trace AND a new 0.4 trace, with refs/opentraces events + "
            "notes created additively over the pre-existing repo history."
        ),
        provides={
            "legacy_world_restored": True,
            "migration_applied": True,
            "no_data_loss": True,
            "migration_idempotent": True,
            "pre_migration_schema": PRE_MIGRATION_SCHEMA,
            "pre_migration_cli": PRE_MIGRATION_CLI,
            "captured_traces": 2,
        },
    )
)

register(
    Checkpoint(
        name=_OTEL_UPGRADED,
        composed_from=_BASE,
        delta=_legacy_v033_otel_upgraded_delta,
        cache=True,
        description=(
            "c-legacy-v033 + a deterministic OTLP Context Tree capture driven "
            "through the real receiver/mapper/emitter chain with synthetic OTel "
            "envelopes (no real claude, no network). The legacy 0.3.3 world now "
            "also carries an OTel-captured context tree (capture_method=otel) on "
            "refs/opentraces/local/events/v1, additively over the legacy trace. "
            "The CI-safe Phase-2 real-OTLP upgraded checkpoint; the real-claude "
            "outcome gate stays the opt-in manual-UAT step."
        ),
        provides={
            "legacy_world_restored": True,
            "migration_applied": True,
            "otel_captures_present": True,
            "pre_migration_schema": PRE_MIGRATION_SCHEMA,
            "pre_migration_cli": PRE_MIGRATION_CLI,
        },
    )
)
