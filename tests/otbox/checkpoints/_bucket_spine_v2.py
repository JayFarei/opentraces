"""Bucket-spine-v2 checkpoint family — plan 080 phases B/C/D worlds (issue #42).

Nine deterministic, fully synthetic checkpoints (plus the v1-legacy
migration fixture) that un-quarantine the plan-080 bucket journeys.
Everything composes off ``c-installed-source`` through the proven
fake-claude harness chain (see ``_captured_session.py``), so the family
is default-CI safe, artifact-free, and ``cache=True`` end to end.

Family tree::

    c-installed-source
      └── c-bucket-spine-v2-one-trace-provisional   (ingested, NO commit)
            └── c-bucket-spine-v2-one-trace-anchored (commit + tick + mature)
                  ├── c-bucket-spine-v2-multi-trace-mixed   (2nd corpus session)
                  │     └── c-bucket-spine-v2-pushed-fake-remote (setup+push)
                  │           ├── c-bucket-spine-v2-pushed-fake-remote-blob-dropped
                  │           └── c-bucket-spine-v2-cross-machine
                  ├── c-bucket-spine-v2-crash-injected-w1   (orphan blob)
                  ├── c-bucket-spine-v2-dangling-ref-injected (blob deleted)
                  ├── c-bucket-spine-v2-bucket-only          (project .git gone)
                  └── c-bucket-spine-v1-legacy-fixture       (plan-079 layout)

Honesty notes (what the worlds really contain):

* ``blob-dropped`` does NOT drop blob files. ``ctx show`` resolves layers
  from the canonical event log, never from the bucket blob cache, so the
  only construction that exercises the ``--offline`` rc=4 gate is a node
  whose layer events are absent from the log. The checkpoint appends one
  synthetic ContextNode (pinned id ``_DANGLING_NODE_ID``) referencing four
  layer ids that exist nowhere — the deterministic "layer not locally
  resolvable" shape. The events mirror is NOT resynced afterwards, so
  ``bucket verify`` stays green on this world.
* ``crash-injected-w1`` plants one orphan context blob whose payload
  echoes its path hash (passes verify's content check) but that no event
  references — exactly Resolution G's W1 crash shape. ``bucket verify``
  stays ok (orphans are prune's domain, not verify's); ``bucket prune``
  reports and removes it.
* ``dangling-ref-injected`` deletes one referenced blob file, so
  ``bucket verify --full`` exits 3 with ``dangling_count >= 1``.
* ``bucket-only`` removes the project working tree's ``.git`` entirely:
  no canonical ref, no notes — the §13 self-sufficiency surface must come
  from ``~/.opentraces`` (bucket + state) alone.
* ``cross-machine`` re-verifies projection determinism at build time
  (back-to-back manifests must agree on ``bucket_digest``) before journeys
  run the wipe-and-pull identity chain against the fake remote.
"""

from __future__ import annotations

import json

from ..drivers.base import Driver
from ..env import Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    check as _check_helper,
    encode_claude_path,
    git as _git_helper,
    harness_interpreter,
    harness_source_with_shebang,
    read_state_json,
    synthetic_capture_metadata,
    trace_for_session,
)
from pathlib import Path

_FAMILY = "c-bucket-spine-v2"

# Session corpora (tests/otbox/fixtures/sessions/). Both run in the same
# project so their JSONL-derived system layers content-hash identically
# (same cwd/model/claude_code_version) — the dedup proof the multi-trace
# journeys assert on.
_SESSION_1_NAME = "simple-refactor"
_SESSION_1_ID = "sess-otbox-simple-refactor"
_SESSION_1_COMMIT_SUBJECT = "Add farewell() helper"
_SESSION_2_NAME = "context-tree-linear"
_SESSION_2_ID = "sess-otbox-context-tree-linear"
_SESSION_2_COMMIT_SUBJECT = "Tidy greeting() whitespace"

# The simple-refactor Edit lands on step 3 (1=user, 2=Read, 3=Edit).
_EDIT_STEP_INDEX = 3

# Deterministic fault-injection identities. The dangling node id is
# DERIVED — ContextNode validates node_id against its canonical material —
# so the seed script computes it via build_node (from real build_layer
# layer ids; issue #57) and the checkpoint exposes it through
# ``~/{_NODE_ID_DIR}/dangling_node_id`` for shell-step `$(cat)`.
#
# The node is bound to the MAIN trace (not a synthetic trace id) and gets
# a high step_index: ``build_context_tree_projection`` has a
# variable-shadowing bug (the ``context_tree_reconciled`` branch rebinds
# the ``trace_id`` parameter, so every event after the first reconciled
# trace's reconcile is silently dropped from unscoped projections) — a
# node on any LATER trace would never project. Reported as a product
# defect; this checkpoint stays inside the working surface.
_DANGLING_STEP_INDEX = 99
_ORPHAN_BLOB_HEX = "0f" * 32  # blobs/v1/<slug>/context/0f/<hex>.json.gz

_HARNESS_SRC = Path(__file__).resolve().parents[1] / "fake_harnesses" / "claude"
_SESSIONS_SRC = Path(__file__).resolve().parents[1] / "fixtures" / "sessions"

# Box-relative file journeys read node ids from (shell-step `$(cat ...)`).
_NODE_ID_DIR = ".otbox-bucket-spine"


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_FAMILY, label=label)


def _git(driver: Driver, box: Box, *args: str):
    return _git_helper(driver, box, *args, checkpoint=_FAMILY)


def _cli_json(driver: Driver, box: Box, *args: str, label: str) -> dict:
    cli = resolve_cli_argv()
    result = driver.exec(box, [*cli, *args])
    _check(result, label)
    text = result.stdout
    marker = "---OPENTRACES_JSON---"
    if marker in text:
        text = text.split(marker, 1)[1]
    try:
        payload = json.loads(text.strip())
    except (ValueError, json.JSONDecodeError) as exc:
        raise CheckpointError(
            f"{_FAMILY} step {label!r} emitted malformed JSON: {exc}"
        ) from None
    return payload if isinstance(payload, dict) else {}


def _audit(box: Box) -> dict:
    return box.notes.setdefault("bucket_spine_v2_audit", {})


def _stage_harness(driver: Driver, box: Box) -> tuple[str, str]:
    """Place the fake-claude harness + full corpus on the box (idempotent)."""
    paths = driver.paths(box)
    home = paths["home"]
    bin_dir = f"{home}/bin"
    sessions_dir = f"{home}/share/otbox/sessions"
    driver.mkdir(box, bin_dir)
    driver.mkdir(box, sessions_dir)
    harness_dst = f"{bin_dir}/claude"
    driver.put_text(box, harness_dst, harness_source_with_shebang(_HARNESS_SRC))
    _check(driver.exec(box, ["chmod", "+x", harness_dst]), "chmod +x harness")
    for corpus in (_SESSION_1_NAME, _SESSION_2_NAME):
        session_dst = f"{sessions_dir}/{corpus}"
        driver.mkdir(box, session_dst)
        for child in (_SESSIONS_SRC / corpus).iterdir():
            if child.is_file():
                driver.put_text(box, f"{session_dst}/{child.name}", child.read_text())
    return harness_dst, sessions_dir


def _run_session(
    driver: Driver,
    box: Box,
    *,
    harness_dst: str,
    sessions_dir: str,
    session_name: str,
    session_id: str,
) -> str:
    """Run one harness session and ingest its transcript. Returns the path."""
    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]
    cli = resolve_cli_argv()
    encoded_project = encode_claude_path(project)
    transcript_path = f"{home}/.claude/projects/{encoded_project}/{session_id}.jsonl"
    _check(
        driver.exec(box, [harness_interpreter(), harness_dst], env_extra={
            "OTBOX_FAKE_SESSION": session_name,
            "OTBOX_FAKE_SESSIONS_DIR": sessions_dir,
            "OTBOX_PROJECT_DIR": project,
            "OTBOX_TRANSCRIPT_PATH": transcript_path,
        }),
        f"fake harness invocation ({session_name})",
    )
    _check(
        driver.exec(box, [*cli, "_ingest-session", transcript_path, "--project", project]),
        f"_ingest-session ({session_name})",
    )
    return transcript_path


def _commit_and_mature(driver: Driver, box: Box, subject: str) -> str:
    """Commit the working tree (hook suppressed), tick, replay notes, mature."""
    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]
    cli = resolve_cli_argv()
    no_hooks_dir = f"{home}/empty-hooks"
    driver.mkdir(box, no_hooks_dir)
    _git(driver, box, "add", "-A")
    _git(driver, box, "-c", f"core.hooksPath={no_hooks_dir}", "commit", "-q", "-m", subject)
    commit_sha = _git(driver, box, "rev-parse", "HEAD").stdout.strip()
    _check(
        driver.exec(box, [*cli, "setup", "watcher", "tick", "--project", project, "--json"]),
        "setup watcher tick",
    )
    _check(
        driver.exec(box, [*cli, "_run-post-commit-hook", project]),
        "_run-post-commit-hook (notes replay)",
    )
    _check(
        driver.exec(box, [*cli, "trail", "mature", "--commit", commit_sha,
                          "--project", project, "--json"]),
        "trail mature --commit HEAD",
    )
    return commit_sha


def _bucket_repair(driver: Driver, box: Box) -> dict:
    payload = _cli_json(driver, box, "bucket", "repair", "--json", label="bucket repair")
    repair = payload.get("repair") or {}
    if repair.get("errors"):
        raise CheckpointError(f"{_FAMILY} bucket repair reported errors: {repair['errors']}")
    return repair


def _manifest(driver: Driver, box: Box) -> dict:
    payload = _cli_json(driver, box, "bucket", "manifest", "--json", label="bucket manifest")
    return payload.get("manifest") or {}


def _resolve_trace(driver: Driver, box: Box, session_id: str) -> tuple[str, str]:
    """Return ``(project_slug, trace_id)`` for one ingested session."""
    state_dir, state = read_state_json(driver, box)
    trace_id, _ = trace_for_session(state, session_id)
    if not trace_id:
        raise CheckpointError(
            f"{_FAMILY}: no trace for session {session_id!r} in {state_dir}/state.json"
        )
    return state_dir.rstrip("/").rsplit("/", 1)[-1], trace_id


def _write_node_id_file(driver: Driver, box: Box, name: str, value: str) -> str:
    home = driver.paths(box)["home"]
    path = f"{home}/{_NODE_ID_DIR}/{name}"
    driver.put_text(box, path, value + "\n")
    return path


# ---------------------------------------------------------------------------
# 1. c-bucket-spine-v2-one-trace-provisional
# ---------------------------------------------------------------------------
def _build_provisional_world(driver: Driver, box: Box) -> None:
    paths = driver.paths(box)
    project = paths["project"]
    cli = resolve_cli_argv()

    # Seed project with one commit so the event log has history.
    driver.mkdir(box, project)
    driver.put_text(
        box, f"{project}/README.md",
        "# otbox bucket-spine-v2 checkpoint\n\n"
        "Deterministic plan-080 bucket world (issue #42 phantom family).\n",
    )
    driver.mkdir(box, f"{project}/src")
    driver.put_text(
        box, f"{project}/src/app.py",
        'def greet(name: str) -> str:\n    return f"hello, {name}"\n',
    )
    _git(driver, box, "init", "-q")
    _git(driver, box, "add", "-A")
    _git(driver, box, "commit", "-q", "-m", "seed: initial project")

    _check(
        driver.exec(box, [*cli, "init", "--start-fresh", "--agent", "claude-code"]),
        "opentraces init",
    )
    _check(driver.exec(box, [*cli, "setup", "git"]), "opentraces setup git")

    harness_dst, sessions_dir = _stage_harness(driver, box)
    transcript_path = _run_session(
        driver, box,
        harness_dst=harness_dst, sessions_dir=sessions_dir,
        session_name=_SESSION_1_NAME, session_id=_SESSION_1_ID,
    )

    # NO commit: the captured Edit stays a provisional Trace Patch.
    # `trace index` first — it runs sync_trace_records_from_local_stores,
    # which mirrors the staged record into the bucket object store that
    # `bucket repair` projects trace.json from.
    _check(driver.exec(box, [*cli, "trace", "index"]), "trace index")
    _bucket_repair(driver, box)

    slug, trace_id = _resolve_trace(driver, box, _SESSION_1_ID)
    manifest = _manifest(driver, box)
    rows = manifest.get("traces") or []
    if manifest.get("schema_version") != "opentraces.bucket.manifest.v2" or not rows:
        raise CheckpointError(
            f"{_FAMILY}: manifest after repair is not a populated v2 manifest "
            f"(schema={manifest.get('schema_version')!r}, traces={len(rows)})"
        )

    seed_sha = _git(driver, box, "rev-parse", "HEAD").stdout.strip()
    # Reuse the runner-exposed audit key so journeys get {trace_id} /
    # {session_id} / {commit_sha} / {step_index} templating for free.
    box.notes["c_captured_session_audit"] = {
        "session_id": _SESSION_1_ID,
        "session_corpus": _SESSION_1_NAME,
        "trace_id": trace_id,
        "edit_step_index": _EDIT_STEP_INDEX,
        "commit_sha": seed_sha,
        "transcript_path": transcript_path,
        "capture_metadata": synthetic_capture_metadata(),
    }
    audit = _audit(box)
    audit.update({
        "project_slug": slug,
        "trace_id": trace_id,
        "session_id": _SESSION_1_ID,
        "harness_dst": harness_dst,
        "sessions_dir": sessions_dir,
        "bucket_digest": manifest.get("bucket_digest"),
    })


def _provisional_delta(driver: Driver, box: Box) -> None:
    _build_provisional_world(driver, box)


# ---------------------------------------------------------------------------
# 2. c-bucket-spine-v2-one-trace-anchored
# ---------------------------------------------------------------------------
def _anchor_first_trace(driver: Driver, box: Box) -> None:
    commit_sha = _commit_and_mature(driver, box, _SESSION_1_COMMIT_SUBJECT)
    cli = resolve_cli_argv()
    # `trace index` syncs the (now anchor-bearing) staged record into the
    # bucket object store BEFORE repair projects trace.json from it.
    _check(driver.exec(box, [*cli, "trace", "index"]), "trace index (post-anchor)")
    _bucket_repair(driver, box)

    manifest = _manifest(driver, box)
    rows = manifest.get("traces") or []
    anchored = sum(int((r.get("summary") or {}).get("anchored_count") or 0) for r in rows)
    if anchored < 1:
        raise CheckpointError(
            f"{_FAMILY}-one-trace-anchored: no anchored patch after commit+mature "
            f"(rows={json.dumps(rows)[:400]})"
        )
    box.notes["c_captured_session_audit"]["commit_sha"] = commit_sha
    audit = _audit(box)
    audit["commit_sha"] = commit_sha
    audit["bucket_digest"] = manifest.get("bucket_digest")

    # Pin the trace's root ContextNode id (the JSONL capture path emits one
    # session-level node at step 0) so journeys can address a
    # fully-resolvable node via a shell `$(cat ...)` step.
    step = _cli_json(
        driver, box, "ctx", "step",
        audit["trace_id"], "0", "--json",
        label="ctx step (root node)",
    )
    node_id = step.get("node_id")
    if not node_id:
        raise CheckpointError(
            f"{_FAMILY}-one-trace-anchored: ctx step returned no node for "
            f"step 0 ({json.dumps(step)[:300]})"
        )
    audit["main_node_id"] = node_id
    audit["main_node_id_path"] = _write_node_id_file(driver, box, "main_node_id", node_id)


def _anchored_delta(driver: Driver, box: Box) -> None:
    _anchor_first_trace(driver, box)


# ---------------------------------------------------------------------------
# 3. c-bucket-spine-v2-multi-trace-mixed
#
# Built from c-installed-source in ONE box (not composed on the anchored
# snapshot): JSONL-derived layer content embeds the capture cwd, so two
# traces only content-hash-dedup their system layers when both sessions
# ran under the SAME box root. A fork boundary between trace 1 and
# trace 2 would bake two different box paths into the system layers and
# the dedup proof could never hold.
# ---------------------------------------------------------------------------
def _multi_trace_delta(driver: Driver, box: Box) -> None:
    _build_provisional_world(driver, box)
    _anchor_first_trace(driver, box)
    audit = _audit(box)
    transcript_path = _run_session(
        driver, box,
        harness_dst=audit["harness_dst"], sessions_dir=audit["sessions_dir"],
        session_name=_SESSION_2_NAME, session_id=_SESSION_2_ID,
    )
    _commit_and_mature(driver, box, _SESSION_2_COMMIT_SUBJECT)
    cli = resolve_cli_argv()
    _check(driver.exec(box, [*cli, "trace", "index"]), "trace index (multi-trace)")
    _bucket_repair(driver, box)

    _slug, trace_id_2 = _resolve_trace(driver, box, _SESSION_2_ID)
    status = _cli_json(driver, box, "bucket", "status", "--json", label="bucket status")
    bucket = status.get("bucket") or {}
    rows = bucket.get("traces") or []
    if len(rows) < 2:
        raise CheckpointError(
            f"{_FAMILY}-multi-trace-mixed: expected >=2 manifest traces, got {len(rows)}"
        )
    dedup_hits = int((bucket.get("context_trees") or {}).get("dedup_hits") or 0)
    if dedup_hits < 1:
        raise CheckpointError(
            f"{_FAMILY}-multi-trace-mixed: expected cross-trace layer dedup "
            f"(same project/model/version => identical system layers); "
            f"context_trees={json.dumps(bucket.get('context_trees'))[:300]}"
        )
    audit["trace_id_2"] = trace_id_2
    audit["session_id_2"] = _SESSION_2_ID
    audit["transcript_path_2"] = transcript_path
    audit["dedup_hits"] = dedup_hits
    audit["bucket_digest"] = bucket.get("bucket_digest")


# ---------------------------------------------------------------------------
# 4. c-bucket-spine-v2-pushed-fake-remote
# ---------------------------------------------------------------------------
def _pushed_fake_remote_delta(driver: Driver, box: Box) -> None:
    paths = driver.paths(box)
    remote_root = f"{paths['fake_remote']}/bucket-spine"
    driver.mkdir(box, remote_root)
    # Enable the cheap offline security tools so the staged records pass
    # the push eligibility gate (unfiltered_records blocks otherwise; the
    # push-time refresh re-scans them once tools are enabled).
    setup = _cli_json(
        driver, box, "setup", "bucket",
        "--provider", "fake", "--fake-root", remote_root,
        "--enable-security-tool", "regex",
        "--enable-security-tool", "entropy",
        "--no-security-prompt", "--json",
        label="setup bucket --provider fake",
    )
    bucket_cfg = setup.get("bucket") or {}
    if (bucket_cfg.get("remote") or {}).get("provider") not in ("fake", None):
        raise CheckpointError(
            f"{_FAMILY}-pushed-fake-remote: setup bucket did not configure the "
            f"fake provider ({json.dumps(setup)[:300]})"
        )
    push = _cli_json(
        driver, box, "bucket", "remote", "push", "--root", remote_root, "--json",
        label="bucket remote push",
    )
    remote = push.get("remote") or {}
    if remote.get("state") != "pushed":
        raise CheckpointError(
            f"{_FAMILY}-pushed-fake-remote: push state={remote.get('state')!r} "
            f"({json.dumps(remote)[:300]})"
        )
    status = _cli_json(
        driver, box, "bucket", "remote", "status", "--root", remote_root, "--json",
        label="bucket remote status",
    )
    if (status.get("remote") or {}).get("state") != "current":
        raise CheckpointError(
            f"{_FAMILY}-pushed-fake-remote: post-push remote state is "
            f"{(status.get('remote') or {}).get('state')!r}, expected 'current'"
        )
    audit = _audit(box)
    audit["fake_remote_root"] = remote_root
    audit["pushed_digest"] = remote.get("digest")


# ---------------------------------------------------------------------------
# 5. c-bucket-spine-v2-pushed-fake-remote-blob-dropped
# ---------------------------------------------------------------------------
def _blob_dropped_delta(driver: Driver, box: Box) -> None:
    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]
    audit = _audit(box)
    main_trace_id = audit["trace_id"]
    project_slug = audit["project_slug"]
    remote_root = audit["fake_remote_root"]

    # Issue #57 re-spec: the pinned ContextNode's four layer ids are REAL
    # content-addressed build_layer outputs. No layer *events* are appended
    # (so the layers miss the event-log projection -> `ctx show --offline`
    # rc=4 before warm), and the layer blobs are written gzip-mtime-0 to
    # the FAKE REMOTE only (`<fake_remote>/bucket-spine/blobs/v1/<slug>/
    # context/<hh>/<hash>.json.gz`) — absent locally. So `ctx show --remote
    # file://<remote>` fetches them (provenance 'remote', warms the local
    # cache), a second call reads the cache (provenance 'cache'), and
    # `--offline` AFTER the warm succeeds rc=0. The events mirror is NOT
    # resynced, so `bucket verify` stays clean (the blobs are not local).
    #
    # The blob payload shape mirrors _build_context_layer_blob in
    # bucket_context_store.py verbatim so DirectoryHubBackend.get_layer_blob
    # rehydrates a valid ContextLayer.
    script = f"""
import json
from pathlib import Path
from opentraces.core.context_tree import build_node
from opentraces.core.context_tree.models import build_layer
from opentraces.core.context_tree.contract import CONTEXT_NODE_OBSERVED
from opentraces.core.bucket_context_store import CONTEXT_LAYER_BLOB_SCHEMA
from opentraces.core.trails.event_log import append_event_batch
from opentraces.core.trails.models import TrailEventDraft
from opentraces.core._bucket_io import _atomic_write_gzip, _canonical_json

project_dir = Path({json.dumps(project)})
trace_id = {json.dumps(main_trace_id)}
project_slug = {json.dumps(project_slug)}
remote_blobs_root = (
    Path({json.dumps(remote_root)}) / "blobs" / "v1" / project_slug / "context"
)

# Four real layers. Content is seeded distinctly per type so each layer_id
# is unique and deterministic across rebuilds.
_seed = "otbox-blob-dropped"
layers = {{
    "system": build_layer(
        layer_type="system",
        content={{"assembled_text": _seed + "-system", "components_present": ["claude_md"]}},
        completeness="approximated",
        capture_method="transcript_reconstruction",
    ),
    "messages": build_layer(
        layer_type="messages",
        content={{"messages": [{{"uuid": _seed + "-m1", "role": "user", "content_hash": "sha256:aa"}}]}},
        completeness="full",
        capture_method="transcript_reconstruction",
    ),
    "tool_registry": build_layer(
        layer_type="tool_registry",
        content={{"tools": [{{"name": "Edit"}}, {{"name": "Read"}}]}},
        completeness="full",
        capture_method="transcript_reconstruction",
    ),
    "runtime_state": build_layer(
        layer_type="runtime_state",
        content={{"cwd": "/otbox/blob-dropped", "permission_mode": "default"}},
        completeness="full",
        capture_method="transcript_reconstruction",
    ),
}}

node = build_node(
    parent_node_id=None,
    branch_type="root",
    trace_id=trace_id,
    transcript_uuid="otbox-blob-dropped-prompt-0001",
    transcript_offset=0,
    step_index={_DANGLING_STEP_INDEX},
    system_layer_id=layers["system"].layer_id,
    messages_layer_id=layers["messages"].layer_id,
    tool_registry_layer_id=layers["tool_registry"].layer_id,
    runtime_state_layer_id=layers["runtime_state"].layer_id,
    capture_completeness="full",
)

# Append ONLY the node event — no layer events (the layers stay
# unresolvable from the local projection until a remote fetch warms them).
drafts = [
    TrailEventDraft(event_type=CONTEXT_NODE_OBSERVED,
                    payload=node.model_dump(mode="json"),
                    trace_id=trace_id, step_index={_DANGLING_STEP_INDEX},
                    capture_method=["transcript_reconstruction"]),
]
append_event_batch(project_dir, drafts, writer="otbox-bucket-spine-fixture")

# Write the four blobs to the FAKE REMOTE only (gzip mtime=0, pretty).
for layer in layers.values():
    blob_payload = {{
        "schema_version": CONTEXT_LAYER_BLOB_SCHEMA,
        "layer_id": layer.layer_id,
        "layer_type": layer.layer_type,
        "capture_method": layer.capture_method,
        "completeness": layer.completeness,
        "content": layer.content,
    }}
    digest_hex = layer.layer_id.split(":", 1)[1]
    blob_path = remote_blobs_root / digest_hex[:2] / (digest_hex + ".json.gz")
    _atomic_write_gzip(blob_path, _canonical_json(blob_payload, pretty=True).encode("utf-8"))

print(json.dumps({{
    "emitted": len(drafts),
    "node_id": node.node_id,
    "system_layer_id": layers["system"].layer_id,
}}))
"""
    script_path = f"{home}/_otbox_blob_dropped_seed.py"
    driver.put_text(box, script_path, script)
    seed = driver.exec(box, [f"{project}/.testvenv/bin/python", script_path], cwd=project)
    _check(seed, "blob-dropped node seed")
    try:
        emitted = json.loads(seed.stdout.strip().splitlines()[-1])
        dangling_node_id = emitted["node_id"]
    except (ValueError, KeyError, IndexError) as exc:
        raise CheckpointError(
            f"{_FAMILY}-blob-dropped: seed emitted no node id ({seed.stdout[:200]})"
        ) from None

    # The dangling node must resolve in the projection (ctx show finds the
    # node, misses the layers locally) — fail the build if not.
    show = _cli_json(
        driver, box, "ctx", "show", dangling_node_id, "--json",
        label="ctx show (dangling node)",
    )
    node = show.get("node") or {}
    if node.get("node_id") != dangling_node_id:
        raise CheckpointError(
            f"{_FAMILY}-blob-dropped: dangling node not projectable "
            f"({json.dumps(show)[:300]})"
        )
    layers = show.get("layers") or {}
    if (layers.get("system") or {}).get("layer_id") is not None:
        raise CheckpointError(
            f"{_FAMILY}-blob-dropped: dangling node unexpectedly resolved a "
            f"system layer locally — the offline rc=4 shape is gone"
        )
    audit["dangling_node_id"] = dangling_node_id
    audit["dangling_trace_id"] = main_trace_id
    audit["dangling_system_layer_id"] = emitted["system_layer_id"]
    _write_node_id_file(driver, box, "dangling_node_id", dangling_node_id)


# ---------------------------------------------------------------------------
# 6. c-bucket-spine-v2-cross-machine
# ---------------------------------------------------------------------------
def _cross_machine_delta(driver: Driver, box: Box) -> None:
    # Determinism gate at build time: two back-to-back manifest
    # materializations must agree on bucket_digest (Resolution H), or the
    # wipe-and-pull identity chain the journey runs cannot hold.
    first = _manifest(driver, box)
    second = _manifest(driver, box)
    if not first.get("bucket_digest") or first.get("bucket_digest") != second.get("bucket_digest"):
        raise CheckpointError(
            f"{_FAMILY}-cross-machine: bucket_digest unstable across re-projection "
            f"({first.get('bucket_digest')!r} != {second.get('bucket_digest')!r})"
        )
    audit = _audit(box)
    audit["bucket_digest"] = first.get("bucket_digest")


# ---------------------------------------------------------------------------
# 7. c-bucket-spine-v2-crash-injected-w1
# ---------------------------------------------------------------------------
def _crash_injected_delta(driver: Driver, box: Box) -> None:
    audit = _audit(box)
    slug = audit["project_slug"]
    home = driver.paths(box)["home"]
    project = driver.paths(box)["project"]

    # Plant the W1 orphan: a valid content-addressed blob no event
    # references. Written through the product's own deterministic-gzip
    # writer so the byte shape matches real blobs.
    script = f"""
import json
from opentraces.core.bucket_layout import blobs_v1_context_path
from opentraces.core._bucket_io import _atomic_write_bytes, _gzip_deterministic

layer_id = "sha256:{_ORPHAN_BLOB_HEX}"
payload = {{
    "layer_id": layer_id,
    "layer_type": "messages",
    "capture_method": ["transcript_reconstruction"],
    "completeness": "stub",
    "content": {{"note": "otbox crash-injected W1 orphan (no event references this)"}},
}}
path = blobs_v1_context_path({json.dumps(slug)}, layer_id)
data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
_atomic_write_bytes(path, _gzip_deterministic(data))
print(json.dumps({{"orphan_blob": str(path)}}))
"""
    script_path = f"{home}/_otbox_orphan_seed.py"
    driver.put_text(box, script_path, script)
    _check(
        driver.exec(box, [f"{project}/.testvenv/bin/python", script_path], cwd=project),
        "orphan blob seed",
    )

    verify = _cli_json(driver, box, "bucket", "verify", "--full", "--json", label="bucket verify")
    if not verify.get("ok"):
        raise CheckpointError(
            f"{_FAMILY}-crash-injected-w1: verify must stay ok with a W1 orphan "
            f"(errors={json.dumps(verify.get('errors'))[:300]})"
        )
    dry = _cli_json(driver, box, "bucket", "prune", "--dry-run", "--json", label="bucket prune --dry-run")
    if int(dry.get("would_delete") or 0) < 1:
        raise CheckpointError(
            f"{_FAMILY}-crash-injected-w1: prune --dry-run sees no orphan "
            f"({json.dumps(dry)[:300]})"
        )
    audit["orphan_layer_id"] = f"sha256:{_ORPHAN_BLOB_HEX}"


# ---------------------------------------------------------------------------
# 8. c-bucket-spine-v2-dangling-ref-injected
# ---------------------------------------------------------------------------
def _dangling_ref_delta(driver: Driver, box: Box) -> None:
    audit = _audit(box)
    home = driver.paths(box)["home"]
    project = driver.paths(box)["project"]

    # Delete ONE referenced context blob from disk without touching the
    # event log, mirror, or per-trace envelopes — the dangling-ref shape.
    script = f"""
import json
from opentraces.core.bucket_layout import blobs_v1_root

blobs = sorted(blobs_v1_root().glob("*/context/*/*.json.gz"))
if not blobs:
    raise SystemExit("no context blobs to delete")
target = blobs[0]
target.unlink()
print(json.dumps({{"deleted_blob": str(target)}}))
"""
    script_path = f"{home}/_otbox_dangling_seed.py"
    driver.put_text(box, script_path, script)
    _check(
        driver.exec(box, [f"{project}/.testvenv/bin/python", script_path], cwd=project),
        "dangling-ref blob delete",
    )

    cli = resolve_cli_argv()
    verify_run = driver.exec(box, [*cli, "bucket", "verify", "--full", "--json"])
    if verify_run.returncode != 3:
        raise CheckpointError(
            f"{_FAMILY}-dangling-ref-injected: verify rc={verify_run.returncode}, "
            f"expected 3 (dangling ref must trip the gate)"
        )
    try:
        verify = json.loads(verify_run.stdout.strip())
    except (ValueError, json.JSONDecodeError):
        verify = {}
    if int(verify.get("verify", {}).get("dangling_count") or 0) < 1:
        raise CheckpointError(
            f"{_FAMILY}-dangling-ref-injected: verify reported no dangling refs "
            f"({verify_run.stdout[:300]})"
        )
    audit["dangling_injected"] = True


# ---------------------------------------------------------------------------
# 9. c-bucket-spine-v2-bucket-only
# ---------------------------------------------------------------------------
def _bucket_only_delta(driver: Driver, box: Box) -> None:
    project = driver.paths(box)["project"]
    # Drop the canonical Git side entirely: the §13 self-sufficiency claim
    # is that ~/.opentraces (bucket + state) answers every read on its own.
    _check(driver.exec(box, ["rm", "-rf", f"{project}/.git"]), "drop project .git")

    cli = resolve_cli_argv()
    status = _cli_json(driver, box, "bucket", "status", "--json", label="bucket status (no git)")
    rows = (status.get("bucket") or {}).get("traces") or []
    if not rows:
        raise CheckpointError(
            f"{_FAMILY}-bucket-only: manifest lost its traces after .git removal"
        )
    verify_run = driver.exec(box, [*cli, "bucket", "verify", "--full", "--json"])
    if verify_run.returncode != 0:
        raise CheckpointError(
            f"{_FAMILY}-bucket-only: verify rc={verify_run.returncode} on the "
            f"bucket-only world: {verify_run.stdout[:300]}"
        )


# ---------------------------------------------------------------------------
# 10. c-bucket-spine-v1-legacy-fixture
# ---------------------------------------------------------------------------
def _v1_legacy_delta(driver: Driver, box: Box) -> None:
    audit = _audit(box)
    slug = audit["project_slug"]
    trace_id = audit["trace_id"]
    opentraces_dir = driver.paths(box)["opentraces_dir"]
    bucket = f"{opentraces_dir}/bucket"

    # Devolve the v2 bucket into the plan-079 layout shape
    # (`bucket/contexts/v1/...` present; `traces/v1` + `events/v1` absent)
    # so `_detect_bucket_layout` reports "v1_plan79". contexts/v1 heads are
    # already written by the projector, so only the v2 spine moves aside.
    for v2_dir in ("traces", "events", "blobs"):
        _check(driver.exec(box, ["rm", "-rf", f"{bucket}/{v2_dir}"]), f"drop bucket/{v2_dir}")
    for stale in ("manifest.json", "sync_state.json"):
        driver.exec(box, ["rm", "-f", f"{bucket}/{stale}"])
    if not driver.path_exists(box, f"{bucket}/contexts/v1/{slug}/{trace_id}/head.json"):
        raise CheckpointError(
            f"{_FAMILY}-v1-legacy-fixture: expected plan-079 head.json under "
            f"bucket/contexts/v1/{slug}/{trace_id}/"
        )
    if driver.path_exists(box, f"{bucket}/traces/v1"):
        raise CheckpointError(f"{_FAMILY}-v1-legacy-fixture: traces/v1 survived the devolve")


# ---------------------------------------------------------------------------
# registrations
# ---------------------------------------------------------------------------
register(
    Checkpoint(
        name="c-bucket-spine-v2-one-trace-provisional",
        composed_from="c-installed-source",
        delta=_provisional_delta,
        cache=True,
        description=(
            "c-installed-source + one ingested fake-claude session with NO "
            "commit: provisional Trace Patches, context tree events, and a "
            "repaired plan-080 v2 bucket (traces/v1 + blobs/v1 + events/v1 "
            "+ manifest v2)."
        ),
        provides={"captured_traces": 1, "context_tree_built": True},
    )
)

register(
    Checkpoint(
        name="c-bucket-spine-v2-one-trace-anchored",
        composed_from="c-bucket-spine-v2-one-trace-provisional",
        delta=_anchored_delta,
        cache=True,
        description=(
            "Provisional world + hook-suppressed commit + watcher tick + "
            "notes replay + trail mature: the captured patch is anchored "
            "(manifest anchored_count >= 1) and the bucket re-repaired."
        ),
        provides={
            "captured_traces": 1,
            "context_tree_built": True,
            "branch_commits": 2,
        },
    )
)

register(
    Checkpoint(
        name="c-bucket-spine-v2-multi-trace-mixed",
        composed_from="c-installed-source",
        delta=_multi_trace_delta,
        cache=True,
        description=(
            "Full anchored world rebuilt in ONE box plus a second corpus "
            "session (context-tree-linear) in the SAME project: two traces "
            "whose JSONL-derived system layers content-hash identically "
            "(dedup_hits >= 1 verified at build time; layer content embeds "
            "the capture cwd, so both sessions must share a box)."
        ),
        provides={
            "captured_traces": 2,
            "context_tree_built": True,
            "branch_commits": 3,
        },
    )
)

register(
    Checkpoint(
        name="c-bucket-spine-v2-pushed-fake-remote",
        composed_from="c-bucket-spine-v2-multi-trace-mixed",
        delta=_pushed_fake_remote_delta,
        cache=True,
        description=(
            "Multi-trace world + `setup bucket --provider fake` against "
            "{fake_remote}/bucket-spine + one clean `bucket remote push` "
            "(post-push state verified 'current')."
        ),
        provides={
            "captured_traces": 2,
            "context_tree_built": True,
            "bucket_spine_v2_layout": True,
            "pushed_to_fake_remote": True,
        },
    )
)

register(
    Checkpoint(
        name="c-bucket-spine-v2-pushed-fake-remote-blob-dropped",
        composed_from="c-bucket-spine-v2-pushed-fake-remote",
        delta=_blob_dropped_delta,
        cache=True,
        description=(
            "Pushed world + one synthetic ContextNode whose four layer ids "
            "are REAL build_layer outputs (issue #57) with NO layer events in "
            "the log and their blobs written gzip-mtime-0 to the FAKE REMOTE "
            "ONLY (absent locally). The deterministic shape behind both the "
            "`ctx show --offline` rc=4 gate AND the remote->cache lazy-fetch "
            "provenance arm. Events mirror untouched, verify clean."
        ),
        provides={
            "captured_traces": 2,
            "context_tree_built": True,
            "bucket_spine_v2_layout": True,
            "pushed_to_fake_remote": True,
            "local_blobs_dropped": True,
        },
    )
)

register(
    Checkpoint(
        name="c-bucket-spine-v2-cross-machine",
        composed_from="c-bucket-spine-v2-pushed-fake-remote",
        delta=_cross_machine_delta,
        cache=True,
        description=(
            "Pushed world re-verified for projection determinism "
            "(back-to-back manifests agree on bucket_digest) — the base for "
            "the wipe-and-pull cross-machine identity journey."
        ),
        provides={"captured_traces": 2, "context_tree_built": True},
    )
)

register(
    Checkpoint(
        name="c-bucket-spine-v2-crash-injected-w1",
        composed_from="c-bucket-spine-v2-one-trace-anchored",
        delta=_crash_injected_delta,
        cache=True,
        description=(
            "Anchored world + one planted W1 orphan blob (valid "
            "content-addressed payload, zero event references). verify "
            "stays ok; prune --dry-run sees >=1 orphan (build-verified)."
        ),
        provides={"captured_traces": 1, "context_tree_built": True},
    )
)

register(
    Checkpoint(
        name="c-bucket-spine-v2-dangling-ref-injected",
        composed_from="c-bucket-spine-v2-one-trace-anchored",
        delta=_dangling_ref_delta,
        cache=True,
        description=(
            "Anchored world with ONE referenced context blob deleted from "
            "disk: `bucket verify --full` exits 3 with dangling_count >= 1 "
            "(build-verified)."
        ),
        provides={"captured_traces": 1, "context_tree_built": True},
    )
)

register(
    Checkpoint(
        name="c-bucket-spine-v2-bucket-only",
        composed_from="c-bucket-spine-v2-one-trace-anchored",
        delta=_bucket_only_delta,
        cache=True,
        description=(
            "Anchored world with the project's .git REMOVED: no canonical "
            "event ref, no notes. The plan-080 §13 self-sufficiency surface "
            "(bucket + ~/.opentraces state) must answer every read alone."
        ),
        provides={"captured_traces": 1, "context_tree_built": True},
    )
)

register(
    Checkpoint(
        name="c-bucket-spine-v1-legacy-fixture",
        composed_from="c-bucket-spine-v2-one-trace-anchored",
        delta=_v1_legacy_delta,
        cache=True,
        description=(
            "Anchored world devolved to the plan-079 bucket layout "
            "(bucket/contexts/v1 present, traces/v1 + events/v1 + blobs/v1 "
            "+ manifest removed) so `setup bucket --migrate` has a real "
            "v1_plan79 world to upgrade."
        ),
        provides={"captured_traces": 1, "context_tree_built": True},
    )
)
