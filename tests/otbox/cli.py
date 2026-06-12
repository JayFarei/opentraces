"""otbox command-line interface.

Lifecycle vocabulary borrowed from crabbox, opentraces-specific:

    otbox up [--from SNAP] [--seed NAME]   provision (or restore) a box
    otbox seed <scenario>                  materialize a seeded world
    otbox snapshot <name>                  freeze a box to a tar archive
    otbox restore <name>                   fork a fresh box from a snapshot
    otbox run -- <cmd...>                  run a command inside a box
    otbox journey <name>                   run a catalogue journey
    otbox artifacts                        bundle run evidence for a PR
    otbox status / list                    inspect boxes and snapshots
    otbox down [--all]                     tear a box down (zero residue)

Every command accepts ``--json`` for stable machine-readable output
(spec R8 — agent-usable).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from . import __version__
from .artifacts import collect_artifacts
from .drivers import available_drivers, get_driver
from .env import (
    Box,
    BoxNotFound,
    ensure_state_root,
    get_current_box_id,
    list_box_ids,
    list_snapshots,
    new_box_id,
    resolve_box,
    set_current_box_id,
)
from .journey import (
    JourneyResult,
    available_journeys,
    journey_path,
    run_journey,
)
from .seed import available_seeds, run_seed
from .snapshot import (
    delete_snapshot,
    load_snapshot,
    restore_snapshot,
    snapshot_exists,
)


# --------------------------------------------------------------------------
# output helpers
# --------------------------------------------------------------------------
class OtboxError(Exception):
    pass


def _emit(payload: dict, *, json_mode: bool, human: str) -> None:
    if json_mode:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(human)


def _journey_result_path(box: Box, name: str) -> Path:
    return box.logs / f"journey-{name}.json"


def _persist_journey(box: Box, result: JourneyResult) -> Path:
    box.logs.mkdir(parents=True, exist_ok=True)
    path = _journey_result_path(box, result.name)
    path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    return path


def _load_journey_results(box: Box) -> list[JourneyResult]:
    results: list[JourneyResult] = []
    if not box.logs.exists():
        return results
    for path in sorted(box.logs.glob("journey-*.json")):
        data = json.loads(path.read_text())
        results.append(_journey_from_dict(data))
    return results


def _journey_from_checkpoints(name: str) -> list[str]:
    doc = tomllib.loads(journey_path(name).read_text())
    return list(doc.get("from_checkpoints") or [])


def _resolve_box_for_journey(args: argparse.Namespace) -> tuple[Box, Any, dict]:
    """Resolve the execution box for a direct ``otbox journey`` run."""

    if args.box:
        box = resolve_box(args.box)
        return box, get_driver(box.driver), {}

    checkpoints = _journey_from_checkpoints(args.name)
    if len(checkpoints) == 1:
        from .checkpoints import resolve_checkpoint

        driver = get_driver("local")
        cp_result = resolve_checkpoint(driver, checkpoints[0])
        set_current_box_id(cp_result.box.box_id)
        return cp_result.box, driver, {
            "base_checkpoint": cp_result.name,
            "checkpoint_cache_hit": cp_result.cache_hit,
            "checkpoint_snapshot": cp_result.snapshot_name,
        }

    box = resolve_box(None)
    return box, get_driver(box.driver), {}


def _capture_agent_name(agent: str | None) -> str | None:
    if agent in {"codex", "codex-cli"}:
        return "codex-cli"
    if agent in {"claude", "claude-code"}:
        return "claude-code"
    if agent == "pi":
        return "pi"
    return agent


def _capture_refresh_template_context(box: Box) -> dict[str, str]:
    """Template vars available to PTY capture-refresh scenario turns.

    Captured-session checkpoints record their minted trace/session ids in
    ``box.notes``. Exposing those ids to the PTY scenario layer lets live TUI
    smoke tests drive positive slash commands such as ``/ot-trace {trace_id}``
    against a bucket trace that actually exists, instead of only exercising
    negative/error rendering paths.
    """
    context: dict[str, str] = {}
    for audit_key in (
        "c_captured_pi_session_audit",
        "c_captured_codex_session_audit",
        "c_captured_session_audit",
    ):
        audit = box.notes.get(audit_key) or {}
        if not isinstance(audit, dict):
            continue
        for field in ("trace_id", "session_id", "commit_sha", "transcript_path"):
            value = audit.get(field)
            if value and field not in context:
                context[field] = str(value)
    return context


def _expand_capture_refresh_turns(turns, context: dict[str, str]):
    """Replace ``{trace_id}``-style placeholders in scenario turns.

    Uses ``dataclasses.replace`` so every Turn field OUTSIDE the expanded
    string fields (``verify_command``/``verify_regex``/
    ``expect_revert_commit``/``fresh_session`` today, anything added later)
    rides through untouched — rebuilding the Turn field-by-field silently
    stripped the issue #49 per-turn contract before it reached the drive.
    """
    import dataclasses

    def expand(value: str) -> str:
        expanded = value
        for key, replacement in context.items():
            expanded = expanded.replace("{" + key + "}", replacement)
        return expanded

    return [
        dataclasses.replace(
            turn,
            prompt=expand(turn.prompt),
            expect_regex=expand(turn.expect_regex),
            verify_regex=(
                expand(turn.verify_regex)
                if turn.verify_regex is not None
                else None
            ),
            verify_command=(
                [expand(arg) for arg in turn.verify_command]
                if turn.verify_command is not None
                else None
            ),
        )
        for turn in turns
    ]


def _postprocess_capture_box(driver, box: Box, agent: str | None) -> list[dict]:
    """Synchronously materialize traces before capture-refresh snapshots.

    Agent Stop hooks are intentionally fire-and-forget in production, but
    capture artifacts are acceptance evidence. They need the derived trace
    state, bucket exports, context events, and search index to exist before
    the box is archived.
    """
    normalized = _capture_agent_name(agent)
    if normalized not in {"codex-cli", "claude-code", "pi"}:
        return []

    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]
    cli = f"{project}/.testvenv/bin/opentraces"
    reports: list[dict] = []

    if normalized == "codex-cli":
        session_files = driver.glob(
            box,
            f"{home}/.codex/sessions/[0-9][0-9][0-9][0-9]/"
            "[0-9][0-9]/[0-9][0-9]/rollout-*.jsonl",
        )
    elif normalized == "pi":
        session_files = driver.glob(box, f"{home}/.pi/agent/sessions/--*--/*.jsonl")
    else:
        session_files = driver.glob(box, f"{home}/.claude/projects/*/*.jsonl")

    for session_file in session_files:
        result = driver.exec(
            box,
            [
                cli,
                "_ingest-session",
                session_file,
                "--agent",
                normalized,
                "--project",
                project,
            ],
            cwd=project,
            timeout=30,
        )
        reports.append({
            "step": "ingest-session",
            "session_file": session_file,
            "ok": result.ok,
            "returncode": result.returncode,
            "stderr": result.stderr.strip()[:300],
        })
        if not result.ok:
            raise OtboxError(
                "post-capture ingest failed for "
                f"{session_file}: {result.stderr.strip() or result.stdout.strip()}"
            )

    if normalized == "codex-cli" and not session_files:
        raise OtboxError("post-capture ingest found no Codex rollout JSONL files")
    if normalized == "pi" and not session_files:
        raise OtboxError("post-capture ingest found no Pi session JSONL files")

    tick = driver.exec(
        box,
        [cli, "setup", "watcher", "tick", "--project", project, "--json"],
        cwd=project,
        timeout=30,
    )
    reports.append({
        "step": "watcher-tick",
        "ok": tick.ok,
        "returncode": tick.returncode,
        "stderr": tick.stderr.strip()[:300],
    })
    if not tick.ok:
        raise OtboxError(
            f"post-capture watcher tick failed: {tick.stderr.strip() or tick.stdout.strip()}"
        )

    head = driver.exec(box, ["git", "-C", project, "rev-parse", "HEAD"], timeout=10)
    if head.ok and head.stdout.strip():
        mature = driver.exec(
            box,
            [
                cli,
                "trail",
                "mature",
                "--commit",
                head.stdout.strip(),
                "--project",
                project,
                "--json",
            ],
            cwd=project,
            timeout=30,
        )
        reports.append({
            "step": "trail-mature",
            "ok": mature.ok,
            "returncode": mature.returncode,
            "stderr": mature.stderr.strip()[:300],
        })
        if not mature.ok:
            raise OtboxError(
                "post-capture trail mature failed: "
                f"{mature.stderr.strip() or mature.stdout.strip()}"
            )

    index = driver.exec(
        box,
        [cli, "trace", "index", "rebuild"],
        cwd=project,
        timeout=30,
    )
    reports.append({
        "step": "trace-index-rebuild",
        "ok": index.ok,
        "returncode": index.returncode,
        "stderr": index.stderr.strip()[:300],
    })
    if not index.ok:
        raise OtboxError(
            f"post-capture trace index rebuild failed: {index.stderr.strip() or index.stdout.strip()}"
        )

    return reports


# The literal body marker a real `git revert` writes — the same string
# sync.py::_find_revert_commit + _captured_with_revert.py grep for.
_REVERT_BODY_MARKER = "This reverts commit "


def _capture_box_world_facts(driver, box: Box) -> dict:
    """Cheap world-shape facts of a LIVE capture box (issue #49 gate).

    Returns ``{"trace_count": int, "security_fingerprinted": bool,
    "has_revert_commit": bool}`` — the live-box analogue of the archive
    ``peek_artifact_world`` peek the captured-session checkpoints run on
    restore. Reads state.json trace counts across every opted-in project,
    inspects each TraceRecord for ``metadata.security.tools_applied``, and
    scans the project git log for the revert body marker.
    """
    paths = driver.paths(box)
    opentraces_dir = paths["opentraces_dir"]
    project = paths["project"]

    trace_count = 0
    security_fingerprinted = False
    state_dirs = [
        path for path in driver.glob(box, f"{opentraces_dir}/projects/*")
        if driver.exec(box, ["test", "-f", f"{path}/state.json"]).ok
    ]
    for state_dir in state_dirs:
        raw = driver.exec(box, ["cat", f"{state_dir}/state.json"])
        if not raw.ok:
            continue
        try:
            state = json.loads(raw.stdout)
        except json.JSONDecodeError:
            continue
        traces = (state.get("traces") or {}) if isinstance(state, dict) else {}
        trace_count += len(traces)
        for trace in traces.values():
            if security_fingerprinted or not isinstance(trace, dict):
                continue
            file_path = trace.get("file_path")
            if not isinstance(file_path, str) or not file_path:
                continue
            rec_raw = driver.exec(box, ["cat", file_path])
            if not rec_raw.ok:
                continue
            first = next(
                (line for line in rec_raw.stdout.splitlines() if line.strip()), ""
            )
            if not first:
                continue
            try:
                record = json.loads(first)
            except json.JSONDecodeError:
                continue
            sec = (record.get("metadata") or {}).get("security") or {}
            if sec.get("tools_applied"):
                security_fingerprinted = True

    has_revert_commit = False
    scan = driver.exec(
        box,
        ["git", "-C", project, "log", "-n", "50", "--format=%B"],
        timeout=30,
    )
    if scan.ok and _REVERT_BODY_MARKER in (scan.stdout or ""):
        has_revert_commit = True

    return {
        "trace_count": trace_count,
        "security_fingerprinted": security_fingerprinted,
        "has_revert_commit": has_revert_commit,
    }


def _evaluate_capture_floors(facts: dict, capture) -> list[str]:
    """Return the list of violated pre-snapshot contract floors.

    ``facts`` is a ``_capture_box_world_facts`` dict; ``capture`` is the
    scenario's :class:`CaptureSpec`. Each returned string names the
    violated floor so the gate's JSON payload + human message can point
    the operator straight at the unmet requirement. An empty list means
    every floor is satisfied. Mirrors the ``restore_from_capture`` peek
    rejection logic so the capture side and the restore side agree on the
    same contract.
    """
    violations: list[str] = []
    have_traces = int(facts.get("trace_count") or 0)
    if have_traces < capture.min_traces:
        violations.append(
            f"min_traces: captured {have_traces} trace(s) < required "
            f"{capture.min_traces}"
        )
    if capture.require_security_fingerprints and not facts.get(
        "security_fingerprinted"
    ):
        violations.append(
            "require_security_fingerprints: no captured trace carries "
            "metadata.security.tools_applied"
        )
    if capture.require_revert_commit and not facts.get("has_revert_commit"):
        violations.append(
            "require_revert_commit: no commit carries the "
            f"{_REVERT_BODY_MARKER!r} body marker"
        )
    return violations


def _scrub_capture_box_before_snapshot(driver, box: Box, agent: str | None) -> list[dict]:
    """Remove live-agent credentials that are needed to run but not to replay."""

    normalized = _capture_agent_name(agent)
    if normalized != "pi":
        return []
    home = driver.paths(box)["home"]
    targets = [f"{home}/.pi/agent/auth.json"]
    reports: list[dict] = []
    for target in targets:
        result = driver.exec(box, ["rm", "-f", target], timeout=10)
        reports.append({
            "step": "scrub-secret",
            "path": target,
            "ok": result.ok,
            "returncode": result.returncode,
        })
        if not result.ok:
            raise OtboxError(f"capture secret scrub failed for {target}: {result.stderr.strip()}")
    return reports


def _journey_from_dict(data: dict) -> JourneyResult:
    from .journey import AssertionResult, StepResult
    from .drivers.base import ExecResult

    def _exec(d: dict | None) -> ExecResult | None:
        if d is None:
            return None
        return ExecResult(
            argv=d["argv"],
            returncode=d["returncode"],
            stdout=d["stdout"],
            stderr=d["stderr"],
            duration_s=d["duration_s"],
            cwd=d.get("cwd", ""),
            timed_out=d.get("timed_out", False),
        )

    r = JourneyResult(
        name=data["name"],
        description=data["description"],
        lane=data["lane"],
        tier=data["tier"],
        seed=data.get("seed"),
        box_id=data["box_id"],
        verdict=data["verdict"],
        reason=data.get("reason", ""),
    )
    r.steps = [
        StepResult(
            index=s["index"],
            step_id=s["step_id"],
            type=s["type"],
            detail=s["detail"],
            result=_exec(s["result"]),
            ok=s["ok"],
            message=s.get("message", ""),
        )
        for s in data.get("steps", [])
    ]
    r.assertions = [
        AssertionResult(
            index=a["index"],
            kind=a["kind"],
            ok=a["ok"],
            message=a["message"],
            spec=a["spec"],
        )
        for a in data.get("assertions", [])
    ]
    return r


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_up(args: argparse.Namespace) -> int:
    ensure_state_root()
    if args.from_snapshot:
        if not snapshot_exists(args.from_snapshot):
            raise OtboxError(f"no snapshot named {args.from_snapshot!r}")
        # Restore into the driver requested by `up --driver`, not the
        # driver the snapshot was taken from — interchange invariant.
        driver = get_driver(args.driver)
        if hasattr(driver, "restore"):
            box, info = driver.restore(args.from_snapshot, box_id=args.id)
        else:
            box, info = restore_snapshot(args.from_snapshot, box_id=args.id)
        # provision is idempotent for any driver that needs setup beyond
        # whatever restore() already did.
        driver.provision(box)
        box.save()
        set_current_box_id(box.box_id)
        _emit(
            {"action": "up", "restored": True, **box.to_dict(), "restore": info},
            json_mode=args.json,
            human=(
                f"up: box {box.box_id} restored from snapshot {args.from_snapshot!r} "
                f"in {info['restore_duration_s']}s "
                f"(seed={box.seed}, paths_rewritten={info['paths_rewritten']})"
            ),
        )
        return 0

    box = Box(box_id=args.id or new_box_id(), driver=args.driver)
    driver = get_driver(box.driver)
    driver.provision(box)
    box.status = "provisioned"
    box.save()
    set_current_box_id(box.box_id)

    seed_report = None
    if args.seed:
        seed_report = run_seed(driver, box, args.seed)

    _emit(
        {"action": "up", "restored": False, **box.to_dict(), "seed_report": seed_report},
        json_mode=args.json,
        human=(
            f"up: box {box.box_id} provisioned (driver={box.driver})"
            + (f", seeded {args.seed!r}" if args.seed else "")
        ),
    )
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    box = resolve_box(args.box)
    driver = get_driver(box.driver)
    report = run_seed(driver, box, args.scenario)
    _emit(
        {"action": "seed", "box_id": box.box_id, "report": report},
        json_mode=args.json,
        human=(
            f"seed: box {box.box_id} seeded {args.scenario!r} "
            f"({len(report.get('trace_ids', []))} traces, "
            f"state_dir={report.get('state_dir')})"
        ),
    )
    return 0


_OTBOX_YAML_TEMPLATE = """\
# otbox per-repo config (plan 061 R7). Edit to taste.
#
# All fields are optional. Commands fall back to their built-in
# defaults when a field is missing.

# Default driver for `otbox up` / `otbox warmup`. One of: local, docker, remote.
driver: local

# Default Tier 1 target (only used when driver == remote).
# Override at the shell with OT_OTBOX_SSH_TARGET. Example:
# target: gabrieles-laptop-1
#
# remote:
#   target: user@hostname
#   port: 22
#   key: ~/.ssh/id_ed25519

# Default seed scenario for `otbox up --seed`.
seed: smoke

# rsync excludes for `otbox sync` (added on top of the built-in defaults).
sync:
  exclude:
    - .vercel/
    - .next/

# Environment variables the box may set on top of the isolated env.
# Listed names get forwarded to remote `env VAR=val` blocks.
env:
  allow:
    - CI
    - NODE_OPTIONS

# Remote prerequisites — otbox refuses to provision a remote target
# that does not meet these (it never installs anything on someone
# else's machine).
prerequisites:
  remote:
    - python3.10+
    - git
    - rsync
"""

# The agent-facing skill ships at install time via `./otbox init`. The
# canonical source-of-truth lives at ``tests/otbox/templates/SKILL.md``
# so the file is diff-able + reviewable as normal markdown rather than
# an opaque string literal here.
_OTBOX_SKILL_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "SKILL.md"


def cmd_init(args: argparse.Namespace) -> int:
    """Write ``.otbox.yaml`` and the agent SKILL into the current repo."""
    from .env import REPO_ROOT

    repo_root = REPO_ROOT
    otbox_yaml = repo_root / ".otbox.yaml"
    skill_dir = repo_root / ".agents" / "skills" / "otbox"
    skill_md = skill_dir / "SKILL.md"

    written: list[str] = []
    skipped: list[str] = []

    skill_body = _OTBOX_SKILL_TEMPLATE_PATH.read_text(encoding="utf-8")
    for path, body in ((otbox_yaml, _OTBOX_YAML_TEMPLATE),
                       (skill_md, skill_body)):
        if path.exists() and not args.force:
            skipped.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        written.append(str(path))

    _emit(
        {"action": "init", "written": written, "skipped": skipped,
         "repo_root": str(repo_root)},
        json_mode=args.json,
        human=(
            f"init: wrote {len(written)} file(s), skipped {len(skipped)} existing"
            + (f"\n  -> {chr(10).join('  ' + w for w in written)}" if written else "")
            + (f"\n  skipped: {', '.join(skipped)}" if skipped else "")
        ),
    )
    return 0


def cmd_warmup(args: argparse.Namespace) -> int:
    """Provision a Tier 1 box (no seed) — the iteration-loop entry point."""
    args.driver = "remote"
    args.from_snapshot = None
    args.seed = None
    return cmd_up(args)


def cmd_sync(args: argparse.Namespace) -> int:
    box = resolve_box(args.box)
    driver = get_driver(box.driver)
    result = driver.sync(box, full_resync=args.full_resync)
    _emit(
        {"action": "sync", "box_id": box.box_id, "result": result.to_dict()},
        json_mode=args.json,
        human=(
            f"sync: box {box.box_id} rc={result.returncode} "
            f"({result.duration_s:.2f}s)" +
            ("" if result.ok else f"\n  stderr: {result.stderr.strip()[:300]}")
        ),
    )
    return 0 if result.ok else 1


def cmd_ssh(args: argparse.Namespace) -> int:
    """Drop the developer into the remote box (Tier 1) or the local project (Tier 0)."""
    box = resolve_box(args.box)
    if box.driver == "remote":
        target = box.notes.get("remote_target")
        remote_root = box.notes.get("remote_root")
        if not target or not remote_root:
            raise OtboxError(f"box {box.box_id} has no remote_target — provision first")
        cwd = remote_root if args.root else f"{remote_root}/project"
        argv = ["ssh"]
        port = os.environ.get("OT_OTBOX_SSH_PORT")
        key = os.environ.get("OT_OTBOX_SSH_KEY")
        if port:
            argv += ["-p", port]
        if key:
            argv += ["-i", key]
        argv += ["-t", target, f"cd {cwd} && exec ${{SHELL:-/bin/sh}} -l"]
        if args.json:
            _emit({"action": "ssh", "argv": argv, "cwd": cwd},
                  json_mode=True, human="")
            return 0
        os.execvp(argv[0], argv)
    cwd = str(box.root if args.root else box.project)
    if args.json:
        _emit({"action": "ssh", "argv": ["cd", cwd], "cwd": cwd},
              json_mode=True, human="")
        return 0
    os.execvp("sh", ["sh", "-c", f"cd {cwd} && exec ${{SHELL:-/bin/sh}} -l"])
    return 0  # unreachable


def cmd_snapshot(args: argparse.Namespace) -> int:
    box = resolve_box(args.box)
    driver = get_driver(box.driver)
    info = driver.snapshot(box, args.name, overwrite=args.overwrite)
    _emit(
        {"action": "snapshot", **info.to_dict()},
        json_mode=args.json,
        human=(
            f"snapshot: {args.name!r} created from box {box.box_id} "
            f"({info.size_bytes} bytes, seed={info.seed})"
        ),
    )
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    if not snapshot_exists(args.name):
        raise OtboxError(f"no snapshot named {args.name!r}")
    driver = get_driver(args.driver)
    if hasattr(driver, "restore"):
        box, info = driver.restore(args.name, box_id=args.id)
    else:
        box, info = restore_snapshot(args.name, box_id=args.id)
    driver.provision(box)
    box.save()
    set_current_box_id(box.box_id)
    _emit(
        {"action": "restore", **box.to_dict(), "restore": info},
        json_mode=args.json,
        human=(
            f"restore: box {box.box_id} from snapshot {args.name!r} "
            f"in {info['restore_duration_s']}s (seed={box.seed})"
        ),
    )
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    if args.all:
        torn: list[str] = []
        for box_id in list_box_ids():
            box = Box.load(box_id)
            get_driver(box.driver).teardown(box)
            torn.append(box_id)
        set_current_box_id(None)
        _emit(
            {"action": "down", "torn_down": torn},
            json_mode=args.json,
            human=f"down: tore down {len(torn)} box(es): {', '.join(torn) or '(none)'}",
        )
        return 0

    box = resolve_box(args.box)
    get_driver(box.driver).teardown(box)
    if get_current_box_id() == box.box_id:
        set_current_box_id(None)
    _emit(
        {"action": "down", "torn_down": [box.box_id]},
        json_mode=args.json,
        human=f"down: box {box.box_id} torn down (zero host residue)",
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    box = resolve_box(args.box)
    if not args.command:
        raise OtboxError("nothing to run — pass a command after `--`")
    driver = get_driver(box.driver)
    result = driver.exec(box, args.command)
    if args.json:
        print(json.dumps({"action": "run", "box_id": box.box_id, **result.to_dict()}, indent=2, sort_keys=True))
    else:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
    return result.returncode


def cmd_journey(args: argparse.Namespace) -> int:
    box, driver, resolution = _resolve_box_for_journey(args)
    result = run_journey(driver, box, args.name)
    _persist_journey(box, result)

    bundle = None
    if args.artifacts:
        bundle = str(collect_artifacts(box, [result], run_label=result.name))

    transcript_path = None
    if getattr(args, "save_transcript", None) and result.verdict == "PASS":
        from pathlib import Path as _Path

        from .journey import save_transcript as _save_transcript

        transcript_path = str(
            _save_transcript(result, _Path(args.save_transcript), fixture_name=result.name)
        )

    payload = {"action": "journey", **result.to_dict()}
    if resolution:
        payload["resolution"] = resolution
    if bundle:
        payload["artifacts"] = bundle
    if transcript_path:
        payload["transcript"] = transcript_path
    human_lines = [f"journey: {result.name} -> {result.verdict}"]
    if resolution:
        cache = "cache hit" if resolution["checkpoint_cache_hit"] else "cold build"
        human_lines.append(f"  base: {resolution['base_checkpoint']} ({cache})")
    if result.reason:
        human_lines.append(f"  reason: {result.reason}")
    for s in result.steps:
        human_lines.append(f"  step[{s.index}] {s.step_id} ({s.type}): {'OK' if s.ok else 'FAIL - ' + s.message}")
    for a in result.assertions:
        human_lines.append(f"  assert[{a.index}] {a.kind}: {'OK' if a.ok else 'FAIL - ' + a.message}")
    if bundle:
        human_lines.append(f"  artifacts: {bundle}")
    if transcript_path:
        human_lines.append(f"  transcript: {transcript_path}")
    _emit(payload, json_mode=args.json, human="\n".join(human_lines))
    return 0 if result.verdict in ("PASS", "SKIP") else 1


def cmd_ledger(args: argparse.Namespace) -> int:
    """Compact a matrix report into the executed-evidence run ledger."""
    import json as _json

    from .ledger import build_ledger, ledger_summary

    src = Path(args.from_matrix)
    kills: dict[str, str] = {}
    if src.is_dir():
        # per-journey verdict records written by the pytest lane
        # (OTBOX_LEDGER_DIR) — the matrix runner still skips legacy
        # seed-based journeys, so pytest is the complete evidence source.
        rows = []
        for p in sorted(src.glob("*.json")):
            if p.name.endswith(".kill.json"):
                rec = _json.loads(p.read_text())
                kills[rec["journey"]] = rec["content_hash"]
            else:
                rows.append(_json.loads(p.read_text()))
        report = {"rows": rows}
    else:
        report = _json.loads(src.read_text())
    ledger = build_ledger(report, source=args.source, kills=kills)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(ledger.to_dict(), indent=2, sort_keys=True) + "\n")
    summary = ledger_summary(ledger)
    reds = [r for r in ledger.rows if r.red]
    human = [
        f"ledger: {summary['rows']} rows, {summary['executed']} executed, "
        f"{summary['red']} red — tiers {summary['effective_tiers']}",
        f"  wrote {out}",
    ]
    for r in reds[:20]:
        human.append(f"  [RED] {r.journey} ({r.ci_lane}): {r.red_reason}")
    _emit(
        {"action": "ledger", "path": str(out), **summary},
        json_mode=args.json,
        human="\n".join(human),
    )
    if args.fail_on_red and reds:
        return 1
    return 0


def cmd_fetch_captures(args: argparse.Namespace) -> int:
    """Fetch manifested real-agent capture artifacts from the GitHub release."""
    from .captures_manifest import MANIFEST_PATH, fetch_all, fetch_scenario, load_manifest

    captures_root = Path(__file__).parent / "captures"
    doc = load_manifest()
    if not doc:
        raise OtboxError(f"no captures manifest at {MANIFEST_PATH}")
    if args.scenario:
        fetched = [args.scenario]
        fetch_scenario(args.scenario, captures_root)
    else:
        fetched = fetch_all(captures_root)
    _emit(
        {
            "action": "fetch-captures",
            "release_tag": doc.get("release_tag"),
            "fetched": fetched,
            "captures_root": str(captures_root),
        },
        json_mode=args.json,
        human=f"fetched {len(fetched)} scenario(s) from {doc.get('release_tag')}",
    )
    return 0


def cmd_artifacts(args: argparse.Namespace) -> int:
    box = resolve_box(args.box)
    results = _load_journey_results(box)
    if not results:
        raise OtboxError(
            f"box {box.box_id} has no journey results to bundle — run `otbox journey` first"
        )
    out = collect_artifacts(box, results, run_label=args.label)
    _emit(
        {
            "action": "artifacts",
            "box_id": box.box_id,
            "bundle": str(out),
            "journeys": [r.name for r in results],
            "verdicts": {r.name: r.verdict for r in results},
        },
        json_mode=args.json,
        human=f"artifacts: bundled {len(results)} journey result(s) -> {out}",
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    try:
        box = resolve_box(args.box)
    except BoxNotFound as exc:
        raise OtboxError(str(exc)) from None
    payload = {
        "action": "status",
        **box.to_dict(),
        "is_current": get_current_box_id() == box.box_id,
        "root": str(box.root),
        "exists": box.root.exists(),
        "journey_results": [p.stem for p in sorted(box.logs.glob("journey-*.json"))]
        if box.logs.exists()
        else [],
    }
    human = (
        f"box {box.box_id}  driver={box.driver}  status={box.status}  seed={box.seed}\n"
        f"  root: {box.root}\n"
        f"  current: {payload['is_current']}  journeys run: {payload['journey_results']}"
    )
    _emit(payload, json_mode=args.json, human=human)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    boxes = []
    for box_id in list_box_ids():
        b = Box.load(box_id)
        boxes.append({**b.to_dict(), "is_current": get_current_box_id() == box_id})
    snaps = []
    for name in list_snapshots():
        try:
            snaps.append(load_snapshot(name).to_dict())
        except Exception:  # noqa: BLE001
            snaps.append({"name": name, "error": "unreadable"})
    payload = {
        "action": "list",
        "boxes": boxes,
        "snapshots": snaps,
        "drivers": available_drivers(),
        "seeds": available_seeds(),
        "journeys": available_journeys(),
        "current": get_current_box_id(),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("boxes:")
        for b in boxes:
            mark = "*" if b["is_current"] else " "
            print(f"  {mark} {b['box_id']}  driver={b['driver']} status={b['status']} seed={b['seed']}")
        if not boxes:
            print("  (none)")
        print("snapshots:")
        for s in snaps:
            print(f"    {s['name']}  seed={s.get('seed')} size={s.get('size_bytes')}")
        if not snaps:
            print("  (none)")
        print(f"drivers: {', '.join(payload['drivers'])}")
        print(f"seeds: {', '.join(payload['seeds'])}")
        print("journeys:")
        for j in payload["journeys"]:
            print(f"  {j['name']}  lane={j['lane']} tier={j['tier']} seed={j['seed']}")
    return 0


def cmd_image(args: argparse.Namespace) -> int:
    """Build the Linux runtime image for the opt-in docker driver."""
    import subprocess

    from .env import REPO_ROOT

    dockerfile = REPO_ROOT / "tests" / "otbox" / "docker" / "Dockerfile"
    tag = args.tag
    cmd = ["docker", "build", "-f", str(dockerfile), "-t", tag, str(REPO_ROOT)]
    if not args.json:
        print(f"otbox image: building {tag} (needs network) ...")
    proc = subprocess.run(cmd, capture_output=args.json, text=True)
    ok = proc.returncode == 0
    _emit(
        {
            "action": "image-build",
            "tag": tag,
            "ok": ok,
            "returncode": proc.returncode,
            "command": cmd,
        },
        json_mode=args.json,
        human=(
            f"image: built {tag}"
            if ok
            else f"image: build FAILED (rc={proc.returncode}) — see docker output above"
        ),
    )
    return 0 if ok else 1


def cmd_matrix(args: argparse.Namespace) -> int:
    """Run the (journey × base-checkpoint) matrix (plan 062)."""
    from .drivers import get_driver
    from .matrix import run_matrix, write_report
    from .env import ARTIFACTS_DIR

    # --inventory short-circuits: rebuild journey-inventory.md and exit.
    if args.inventory:
        from .inventory import build_inventory
        out, summary, drift = build_inventory(strict=args.strict)
        payload = {"action": "matrix-inventory", "path": str(out), **summary}
        human = (
            f"inventory: wrote {out}\n"
            f"  {summary['public_total']} public ({summary['public_owned']} owned, "
            f"{summary['public_unowned']} unowned), {summary['hidden_total']} hidden\n"
            f"  unowned: {', '.join(summary['unowned_commands'][:8])}"
            + ("…" if len(summary['unowned_commands']) > 8 else "")
            + "\n  " + drift.human_summary()
        )
        _emit(payload, json_mode=args.json, human=human)
        # Plan 063 SSoT gate: under --strict, drift fails the run.
        if args.strict and not drift.ok:
            return 1
        return 0

    driver = get_driver(args.driver)
    report = run_matrix(
        driver,
        journey_pattern=args.journey,
        checkpoint_filter=args.checkpoint,
        lane=args.lane,
        tier=args.tier,
    )

    out_path = Path(args.out) if args.out else (
        ARTIFACTS_DIR / f"matrix-{report.started_at.replace(':', '')}.json"
    )
    write_report(report, out_path)

    payload = {"action": "matrix", **report.to_dict(), "report_path": str(out_path)}
    human_lines = [
        f"matrix: {report.pass_count} PASS / {report.fail_count} FAIL / "
        f"{report.skip_count} SKIP / {report.error_count} ERROR  "
        f"(in {report.duration_s}s)",
        f"  report: {out_path}",
    ]
    for r in report.rows:
        human_lines.append(
            f"  [{r.verdict}] {r.journey} @ {r.base_checkpoint} "
            f"({r.duration_s}s){' [cache hit]' if r.checkpoint_cache_hit else ''}"
            + (f" — {r.reason}" if r.reason and r.verdict != "PASS" else "")
        )
    _emit(payload, json_mode=args.json, human="\n".join(human_lines))
    return 0 if report.fail_count == 0 and report.error_count == 0 else 1


def cmd_capture_refresh(args: argparse.Namespace) -> int:
    """Drive a simulated-user scenario against a real (or echo) agent and
    snapshot the resulting box state into ``tests/otbox/captures/<name>/``.

    Plan 071 R4 + R5; plan B0 adds the version-aware batch surface:

    * ``--check-versions`` — compare installed agent binary versions
      against the manifest's ``binary_version`` per scenario and report
      which batches are stale. Pure report: no boxes, no agents driven.
    * ``--all --agent <a>`` — regenerate the whole scenario batch for one
      harness (the one-command refresh after a harness version bump).

    The CLI is the operator-facing surface; the heavy lifting lives in
    ``tests.otbox.simulated_users.runner`` (Agent A) and
    ``tests.otbox.simulated_users.scenario`` (Agent B). When the named
    binary is not on PATH, the command SKIPs cleanly (exit 0) so the
    default-CI surface never depends on a real ``claude`` install.
    """
    import datetime as _dt
    import shutil

    if getattr(args, "check_versions", False):
        from .capture_versions import check_capture_versions, format_check_report

        report = check_capture_versions()
        _emit(report, json_mode=args.json, human=format_check_report(report))
        return 0

    if getattr(args, "all_scenarios", False):
        from .simulated_users.scenario import available_scenarios

        if not args.agent:
            raise OtboxError("capture-refresh --all requires --agent <a>")
        names = [
            s["name"]
            for s in available_scenarios()
            if not s.get("error") and s.get("agent") == args.agent
        ]
        if not names:
            raise OtboxError(
                f"capture-refresh --all: no scenarios for agent {args.agent!r}"
            )
        worst = 0
        for name in names:
            sub = argparse.Namespace(**vars(args))
            sub.scenario = name
            sub.all_scenarios = False
            rc = cmd_capture_refresh(sub)
            worst = max(worst, rc)
        return worst

    if not args.scenario:
        raise OtboxError(
            "capture-refresh requires --scenario <name> "
            "(or --all --agent <a>, or --check-versions)"
        )

    # Lazy imports — these modules ship in parallel siblings (plan 071
    # Agents A + B). Importing at the top of cli.py would couple the
    # whole CLI to their import-time health.
    from .checkpoints import resolve_checkpoint
    from .drivers import get_driver
    from .env import REPO_ROOT
    from .footage import _footage_dir
    from .simulated_users.runner import run_simulated_session
    from .simulated_users.scenario import load_scenario, scenario_digest
    from .snapshot import create_snapshot

    captures_root = REPO_ROOT / "tests" / "otbox" / "captures"

    try:
        scenario = load_scenario(args.scenario)
    except Exception as exc:  # noqa: BLE001 - surface load failures cleanly
        raise OtboxError(f"capture-refresh: failed to load scenario "
                         f"{args.scenario!r}: {exc}") from None

    artifact_dir = captures_root / scenario.capture.artifact_dir_name
    artifact_path = artifact_dir / "snapshot.tar.gz"
    metadata_path = artifact_dir / "metadata.json"

    # Binary resolution: echo scenarios use the in-tree meta-test binary;
    # everything else looks the binary up on PATH.
    if scenario.agent == "echo":
        echo_binary = (
            REPO_ROOT / "tests" / "otbox" / "simulated_users" / "_echo_binary.py"
        )
        binary_path: str | None = str(echo_binary) if echo_binary.exists() else None
    else:
        binary_path = shutil.which(scenario.binary_name)

    base_checkpoint = args.base_checkpoint
    real_pi_enabled = os.environ.get("OT_REAL_PI") == "1"

    # --dry-run reports the plan without running anything (and never
    # produces an artifact). The envelope shape mirrors the real-run one.
    if args.dry_run:
        payload = {
            "action": "capture-refresh",
            "status": "dry-run",
            "scenario": scenario.name,
            "agent": scenario.agent,
            "binary_name": scenario.binary_name,
            "binary_path": binary_path,
            "binary_found": binary_path is not None,
            "real_pi_enabled": real_pi_enabled if scenario.agent == "pi" else None,
            "turn_count": len(scenario.turns),
            "base_checkpoint": base_checkpoint,
            "artifact_path": str(artifact_path),
            "metadata_path": str(metadata_path),
            "expected_paths": list(scenario.capture.expected_paths),
        }
        # The dry-run --json envelope is frozen at its pre-#49 shape (hard
        # rule: no shape changes to existing envelopes); the declared
        # contract floors are surfaced in the human text below and enforced
        # by the post-run gate, whose sub-contract payload is a NEW envelope.
        human = (
            f"capture-refresh (dry-run): scenario={scenario.name!r} "
            f"agent={scenario.agent} binary={scenario.binary_name} "
            f"({'found' if binary_path else 'NOT found'} on PATH)\n"
            f"  turns: {len(scenario.turns)}  "
            f"base_checkpoint: {base_checkpoint}\n"
            f"  artifact: {artifact_path}\n"
            f"  metadata: {metadata_path}\n"
            f"  contract floors: min_traces={scenario.capture.min_traces} "
            f"require_security_fingerprints="
            f"{scenario.capture.require_security_fingerprints} "
            f"require_revert_commit={scenario.capture.require_revert_commit} "
            f"enable_security_tools={scenario.capture.enable_security_tools}"
        )
        _emit(payload, json_mode=args.json, human=human)
        return 0

    # Live Pi capture is opt-in even when this development machine has a
    # `pi` binary on PATH; default CI and ordinary package dev must stay
    # offline/deterministic.
    if scenario.agent == "pi" and not real_pi_enabled:
        payload = {
            "action": "capture-refresh",
            "status": "skipped",
            "reason": "set OT_REAL_PI=1 to refresh live Pi artifacts",
            "scenario": scenario.name,
            "agent": scenario.agent,
            "binary_name": scenario.binary_name,
            "binary_found": binary_path is not None,
        }
        _emit(
            payload,
            json_mode=args.json,
            human=(
                f"capture-refresh: SKIP — Pi live capture requires OT_REAL_PI=1 "
                f"(scenario {scenario.name!r})"
            ),
        )
        return 0

    # Binary missing → SKIP cleanly. The whole point of this path is that
    # plain `git clone && make capture-refresh` on a substrate without a
    # real agent on PATH never fails the CI surface.
    if binary_path is None:
        payload = {
            "action": "capture-refresh",
            "status": "skipped",
            "reason": f"binary not found: {scenario.binary_name}",
            "scenario": scenario.name,
            "agent": scenario.agent,
            "binary_name": scenario.binary_name,
        }
        _emit(
            payload,
            json_mode=args.json,
            human=(
                f"capture-refresh: SKIP — binary {scenario.binary_name!r} "
                f"not on PATH (scenario {scenario.name!r})"
            ),
        )
        return 0

    # Resolve the base checkpoint (forks a fresh box from its cached snapshot).
    driver = get_driver("local")
    cp_result = resolve_checkpoint(driver, base_checkpoint)
    box = cp_result.box

    # Copy the scenario's initial-state template into box.project before
    # the runner spins the agent up. We do the copy here (not via the
    # runner's initial_state_dir hook) so the runner stays focused on
    # PTY orchestration.
    template_dir = scenario.initial_state.template_dir
    if template_dir is not None and template_dir.exists():
        for entry in template_dir.iterdir():
            dest = box.project / entry.name
            if entry.is_dir():
                shutil.copytree(entry, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, dest)

    output_dir = box.logs / "capture-refresh" / scenario.name
    output_dir.mkdir(parents=True, exist_ok=True)
    turns = _expand_capture_refresh_turns(
        scenario.turns,
        _capture_refresh_template_context(box),
    )

    # Footage is purely additive to capture-refresh: the SAME interactive
    # run that validates capture also renders the gallery-ready MP4 +
    # result.json/markers.json into the footage dir (drive.drive_session
    # writes those when export_mp4=True). The trace-capture path below is
    # unchanged. footage_dir mirrors footage._footage_dir so the gallery
    # picks it up; it is gitignored (captures/**/footage/).
    footage_dir = _footage_dir(scenario.name, scenario.agent)
    footage_dir.mkdir(parents=True, exist_ok=True)

    result = run_simulated_session(
        driver,
        box,
        binary_path,
        turns,
        initial_state_dir=None,
        output_dir=output_dir,
        agent=scenario.agent,
        scenario=scenario.name,
        record_dir=footage_dir,
        export_mp4=True,
        # Issue #49: the runner flips the scenario's declared security
        # detectors on during box prep — after `opentraces init`, BEFORE the
        # agent spawns — so the agent's Stop-hook ingest sanitizes with the
        # tools active and captured traces carry
        # metadata.security.tools_applied fingerprints.
        enable_security_tools=list(scenario.capture.enable_security_tools)
        or None,
    )

    # FAIL → leave the box up for inspection, exit non-zero. The runner's
    # pane_log_path is the operator's debugging entry point.
    #
    # Key the capture verdict on ``turn_verdict`` (the agent-run outcome
    # BEFORE the footage MP4 export could mutate ``verdict``). Footage is
    # purely additive: a video-export hiccup (missing ffmpeg / video
    # timeout) must never sink a real trace capture. ``turn_verdict`` falls
    # back to ``verdict`` on the legacy lane that doesn't set it.
    capture_verdict = result.turn_verdict or result.verdict
    if capture_verdict != "PASS":
        payload = {
            "action": "capture-refresh",
            "status": "failed",
            "scenario": scenario.name,
            "verdict": result.verdict,
            "error": result.error_message,
            "binary_path": result.binary_path,
            "binary_version": result.binary_version,
            "turn_count": result.turn_count,
            "pane_log_path": str(result.pane_log_path)
            if result.pane_log_path else None,
            "box_id": box.box_id,
            "box_root": str(box.root),
        }
        _emit(
            payload,
            json_mode=args.json,
            human=(
                f"capture-refresh: FAIL — verdict={result.verdict} "
                f"({result.error_message or 'no error message'})\n"
                f"  pane log: {result.pane_log_path}\n"
                f"  box left up at: {box.root}"
            ),
        )
        return 3

    # Stamp the footage's scenario.json so the gallery shows a description
    # (drive already wrote result.json + markers.json + the mp4 there). This
    # is footage-only metadata, independent of the trace capture below.
    try:
        footage_dir.mkdir(parents=True, exist_ok=True)
        (footage_dir / "scenario.json").write_text(
            json.dumps(
                {
                    "name": scenario.name,
                    "description": scenario.description,
                    "agent": scenario.agent,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass

    # The security-tool flip itself ran pre-drive inside the runner's box
    # prep (issue #49); record it in the postprocess report so metadata.json
    # still documents which detectors were active during capture.
    postprocess: list[dict] = []
    if scenario.capture.enable_security_tools:
        postprocess.append({
            "step": "enable-security-tools",
            "tools": list(scenario.capture.enable_security_tools),
            "when": "pre-drive",
            "ok": True,
        })

    postprocess.extend(_postprocess_capture_box(driver, box, scenario.agent))

    # Issue #49: pre-snapshot contract-floor gate. Peek the live box's world
    # shape and reject a sub-contract capture (too few traces / no security
    # fingerprints / no revert commit) BEFORE writing any artifact, mirroring
    # the restore_from_capture peek floors. A violation exits non-zero with a
    # JSON payload naming the unmet floor and writes NO snapshot.tar.gz /
    # metadata.json — so the machine-gated regen can never reproduce a
    # sub-contract batch.
    #
    # Real capture agents (claude/codex/pi) are always gated. The echo/hermes
    # meta-test lane never mints a trace, so it is gated only when the
    # scenario DECLARES floors above the permissive baseline — which is also
    # what lets default CI exercise this exact gate end-to-end (a meta
    # scenario with min_traces > 1 must exit sub-contract with no artifact).
    floors_declared = (
        scenario.capture.min_traces > 1
        or scenario.capture.require_security_fingerprints
        or scenario.capture.require_revert_commit
    )
    floor_violations: list[str] = []
    world_facts: dict = {}
    if floors_declared or _capture_agent_name(scenario.agent) in {
        "codex-cli",
        "claude-code",
        "pi",
    }:
        world_facts = _capture_box_world_facts(driver, box)
        floor_violations = _evaluate_capture_floors(world_facts, scenario.capture)
    if floor_violations:
        payload = {
            "action": "capture-refresh",
            "status": "sub-contract",
            "scenario": scenario.name,
            "contract_floors": {
                "min_traces": scenario.capture.min_traces,
                "require_security_fingerprints": (
                    scenario.capture.require_security_fingerprints
                ),
                "require_revert_commit": scenario.capture.require_revert_commit,
            },
            "world_facts": world_facts,
            "violations": floor_violations,
            "artifact_written": False,
            "box_id": box.box_id,
            "box_root": str(box.root),
        }
        _emit(
            payload,
            json_mode=args.json,
            human=(
                f"capture-refresh: SUB-CONTRACT — scenario={scenario.name!r} "
                f"failed {len(floor_violations)} contract floor(s); NO "
                f"artifact written:\n  "
                + "\n  ".join(floor_violations)
                + f"\n  world: {world_facts}\n  box left up at: {box.root}"
            ),
        )
        return 4

    postprocess.extend(_scrub_capture_box_before_snapshot(driver, box, scenario.agent))

    # PASS → snapshot the box, copy the archive into the artifact dir.
    snap_name = f"_capture-refresh-{scenario.name}-{box.box_id}"
    snap_info = create_snapshot(box, snap_name, overwrite=True)

    artifact_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snap_info.archive, artifact_path)

    # Write provenance metadata next to the artifact.
    try:
        from opentraces import __version__ as cli_version
    except Exception:  # noqa: BLE001 - never fail capture-refresh on version probe
        cli_version = "unknown"
    try:
        from opentraces_schema import SCHEMA_VERSION as schema_version
    except Exception:  # noqa: BLE001
        schema_version = "unknown"

    metadata = {
        "captured_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "scenario_name": scenario.name,
        "scenario_digest": scenario_digest(scenario),
        "agent": scenario.agent,
        "binary_name": scenario.binary_name,
        "binary_path": result.binary_path,
        "binary_version": result.binary_version,
        "turn_count": result.turn_count,
        "base_checkpoint": base_checkpoint,
        "opentraces_schema_version": schema_version,
        "opentraces_cli_version": cli_version,
        "postprocess": postprocess,
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )

    # Tear down the box — its state lives in the artifact now.
    try:
        driver.teardown(box)
    except Exception:  # noqa: BLE001 - teardown failures are non-fatal
        pass

    payload = {
        "action": "capture-refresh",
        "status": "ok",
        "scenario": scenario.name,
        "artifact_path": str(artifact_path),
        "metadata_path": str(metadata_path),
        "binary_path": result.binary_path,
        "binary_version": result.binary_version,
        "turn_count": result.turn_count,
        "base_checkpoint": base_checkpoint,
        # Footage emitted from the SAME run (empty when ffmpeg/export skipped).
        "footage_mp4": result.mp4_path or None,
        "footage_dir": str(footage_dir),
    }
    human = (
        f"capture-refresh: OK — scenario={scenario.name!r} "
        f"binary_version={result.binary_version!r} turns={result.turn_count}\n"
        f"  artifact: {artifact_path}\n"
        f"  metadata: {metadata_path}\n"
        f"  footage:  {result.mp4_path or '(none)'}"
    )
    _emit(payload, json_mode=args.json, human=human)
    return 0


def cmd_acceptance(args: argparse.Namespace) -> int:
    """Drive the 5 B0 acceptance scenarios and write a scored report.

    Sibling of ``cmd_capture_refresh`` (issue #61, plan 095 U9), NOT a fork:
    it reuses the same scenario parser, the same drive core, and the same
    binary-absent / ``--echo`` SKIP semantics.

    Modes:
      * ``--echo`` — drive the deterministic synthetic harness (no real
        agent, no network) and write a valid report to the GITIGNORED
        echo-report.json default path. This is the default-CI
        end-to-end path.
      * real binary present (``--agent <a>``) — fork each scenario's
        base_checkpoint, drive the real agent (the CLI ``--agent`` is
        authoritative for prep/drive/attribution), score the drive, write
        the report. Machine-gated (the maintainer ritual). A drive whose
        preflight SKIPs (missing termctrl/tmux) emits a skip envelope and
        writes NO report.
      * real binary absent and ``--echo`` not requested — SKIP cleanly
        (exit-0 skip envelope), never FAIL (the
        ``test_real_agent_optin.py`` contract).
    """
    import shutil
    import time

    from . import acceptance as accept
    from .simulated_users.scenario import load_scenario, scenario_digest

    # Echo (synthetic) reports default to the gitignored ECHO_REPORT_PATH so
    # a synthetic report never sits on the committed-report path by default.
    default_out = accept.ECHO_REPORT_PATH if args.echo else accept.REPORT_PATH
    out_path = Path(args.out) if args.out else default_out

    # --- echo mode: deterministic, offline, default-CI safe ----------------
    if args.echo:
        report = accept.run_acceptance_echo()
        accept.write_report(report, out_path)
        payload = {
            "action": "acceptance",
            "status": "ok",
            "mode": "echo",
            "report_path": str(out_path),
            "summary": report["summary"],
        }
        _emit(
            payload,
            json_mode=args.json,
            human=(
                f"acceptance (echo): {report['summary']['arcs_completed']}/"
                f"{report['summary']['journeys']} arcs completed, "
                f"mean_score={report['summary']['mean_score']}\n"
                f"  report: {out_path}"
            ),
        )
        return 0

    # --- real-agent mode: requires --agent + a real binary on PATH ---------
    agent = args.agent or "claude"
    binary_name = {"claude": "claude", "codex": "codex", "pi": "pi"}.get(agent)
    binary_path = shutil.which(binary_name) if binary_name else None
    if binary_path is None:
        payload = {
            "action": "acceptance",
            "status": "skipped",
            "reason": (
                f"agent binary {binary_name!r} not on PATH; pass --echo for "
                f"the deterministic synthetic harness"
            ),
            "agent": agent,
        }
        _emit(
            payload,
            json_mode=args.json,
            human=(
                f"acceptance: SKIP — {binary_name!r} not on PATH "
                f"(use --echo for the synthetic harness)"
            ),
        )
        return 0

    # Lazy imports — the real drive couples to the checkpoint + drive stack.
    from .checkpoints import resolve_checkpoint
    from .drivers import get_driver
    from .footage import CAPTURES_ROOT, _footage_dir, build_gallery
    from .simulated_users.footage_runner import (
        FootageResult,
        FootageTurnResult,
    )
    from .simulated_users.runner import run_simulated_session

    driver = get_driver("local")
    captures_root = CAPTURES_ROOT if not args.out else out_path.parent
    scenarios = accept.ACCEPTANCE_SCENARIOS
    if args.scenario:
        scenarios = tuple(s for s in scenarios if s.name == args.scenario)
        if not scenarios:
            raise OtboxError(
                f"acceptance: unknown scenario {args.scenario!r}; "
                f"known: {accept.acceptance_scenario_names()}"
            )

    def _footage_result_from_dir(
        record_dir: Path,
        *,
        fallback: object,
        scenario_name: str,
        agent_name: str,
    ) -> FootageResult:
        data: dict[str, object] = {}
        try:
            data = json.loads((record_dir / "result.json").read_text())
        except (OSError, json.JSONDecodeError):
            data = {}

        turn_rows = data.get("turns") if isinstance(data, dict) else []
        turns: list[FootageTurnResult] = []
        if isinstance(turn_rows, list):
            for row in turn_rows:
                if isinstance(row, dict):
                    try:
                        turns.append(FootageTurnResult(**row))
                    except TypeError:
                        continue

        return FootageResult(
            verdict=str(data.get("verdict") or getattr(fallback, "verdict", "")),
            binary_path=str(
                data.get("binary_path") or getattr(fallback, "binary_path", "")
            ),
            binary_version=str(
                data.get("binary_version")
                or getattr(fallback, "binary_version", "")
            ),
            agent=str(data.get("agent") or agent_name),
            scenario=str(data.get("scenario") or scenario_name),
            turn_count=int(
                data.get("turn_count") or getattr(fallback, "turn_count", 0)
            ),
            mp4_path=str(
                data.get("mp4_path") or getattr(fallback, "mp4_path", "")
            ),
            termctrl_path=str(
                data.get("termctrl_path")
                or getattr(fallback, "termctrl_path", "")
            ),
            markers=list(data.get("markers") or []),
            turns=turns,
            duration_s=float(data.get("duration_s") or 0.0),
            cols=int(data.get("cols") or 110),
            rows=int(data.get("rows") or 32),
            fps=int(data.get("fps") or 20),
            error_message=str(
                data.get("error_message")
                or getattr(fallback, "error_message", "")
            ),
        )

    rows: list[dict] = []
    footage_results: list[FootageResult] = []
    binary_version = ""
    for spec in scenarios:
        scenario = load_scenario(spec.name)
        cp_result = resolve_checkpoint(driver, spec.base_checkpoint)
        box = cp_result.box

        template_dir = scenario.initial_state.template_dir
        if template_dir is not None and template_dir.exists():
            for entry in template_dir.iterdir():
                dest = box.project / entry.name
                if entry.is_dir():
                    shutil.copytree(entry, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(entry, dest)

        output_dir = box.logs / "acceptance" / scenario.name
        output_dir.mkdir(parents=True, exist_ok=True)
        footage_dir = _footage_dir(
            scenario.name, agent, captures_root=captures_root
        )
        footage_dir.mkdir(parents=True, exist_ok=True)
        turns = _expand_capture_refresh_turns(
            scenario.turns, _capture_refresh_template_context(box)
        )

        started = time.monotonic()
        # The CLI ``--agent`` is authoritative end-to-end: box prep, the
        # drive, and row attribution all use it. The TOMLs declare a
        # default agent for portability but a codex/pi run must never prep
        # or attribute as claude.
        result = run_simulated_session(
            driver,
            box,
            binary_path,
            turns,
            initial_state_dir=None,
            output_dir=output_dir,
            agent=agent,
            scenario=scenario.name,
            record_dir=footage_dir,
            export_mp4=True,
        )
        wall_clock_s = time.monotonic() - started
        binary_version = result.binary_version or binary_version

        verdict = result.turn_verdict or result.verdict
        if verdict == "SKIP":
            # The drive never ran (termctrl/tmux/binary preflight). A SKIP
            # must surface as a skip envelope, NOT a scored report — a
            # machine that never ran the arcs must not produce one.
            try:
                driver.teardown(box)
            except Exception:  # noqa: BLE001 - teardown failures are non-fatal
                pass
            payload = {
                "action": "acceptance",
                "status": "skipped",
                "agent": agent,
                "scenario": scenario.name,
                "reason": result.error_message
                or "drive preflight returned SKIP",
            }
            _emit(
                payload,
                json_mode=args.json,
                human=(
                    f"acceptance: SKIP at {scenario.name} — "
                    f"{payload['reason']} (no report written)"
                ),
            )
            return 0

        # End-state path presence (the #49 lesson): every declared
        # expected_paths entry must exist in the driven box project.
        paths_present = all(
            (box.project / rel).exists()
            for rel in scenario.capture.expected_paths
        )

        rows.append(
            accept.score_journey(
                scenario=scenario.name,
                spec_journey=spec.spec_journey,
                verdict=verdict,
                turn_count=result.turn_count,
                par_calls=scenario.par_calls,
                expected_paths_present=paths_present,
                wall_clock_s=wall_clock_s,
                binary_version=result.binary_version,
                agent=agent,
            )
        )
        try:
            (footage_dir / "scenario.json").write_text(
                json.dumps(
                    {
                        "name": scenario.name,
                        "description": scenario.description,
                        "agent": agent,
                        "binary_name": binary_name,
                        "turn_count": len(scenario.turns),
                        "scenario_digest": scenario_digest(scenario),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        footage_results.append(
            _footage_result_from_dir(
                footage_dir,
                fallback=result,
                scenario_name=scenario.name,
                agent_name=agent,
            )
        )
        try:
            driver.teardown(box)
        except Exception:  # noqa: BLE001 - teardown failures are non-fatal
            pass

    digests = {s.name: scenario_digest(load_scenario(s.name)) for s in scenarios}
    report = accept.build_report(
        rows=rows,
        agent=agent,
        binary_version=binary_version,
        scenario_digests=digests,
    )
    accept.write_report(report, out_path)
    gallery_path = build_gallery(
        footage_results, captures_root=captures_root
    )
    # Surface committed-gate validity in the envelope: a --scenario subset
    # run is honest-but-partial and will (correctly) fail the strict
    # committed-report schema, which requires all 5 arcs.
    problems = accept.validate_report_schema(report)
    payload = {
        "action": "acceptance",
        "status": "ok",
        "mode": "real",
        "agent": agent,
        "report_path": str(out_path),
        "gallery_path": str(gallery_path),
        "gallery_dir": str(gallery_path.parent),
        "summary": report["summary"],
        "schema_valid": not problems,
    }
    if problems:
        payload["schema_problems"] = problems
    human = (
        f"acceptance ({agent}): {report['summary']['arcs_completed']}/"
        f"{report['summary']['journeys']} arcs completed, "
        f"mean_score={report['summary']['mean_score']}\n"
        f"  report: {out_path}\n"
        f"  gallery: {gallery_path}"
    )
    if problems:
        human += (
            "\n  NOTE: report does NOT pass the committed-report schema "
            "(partial run?); do not commit it as-is"
        )
    _emit(payload, json_mode=args.json, human=human)
    return 0


def cmd_footage(args: argparse.Namespace) -> int:
    """Record termctrl journey footage and build the gallery (additive).

    Sibling of ``cmd_capture_refresh`` but visual: drives a simulated-user
    scenario through the termctrl-backed recorder, exports an MP4, and
    builds a self-contained gallery. termctrl / agent-binary / ffmpeg
    absence all SKIP cleanly so default CI never depends on a real agent.
    """
    from .footage import (
        FOOTAGE_GALLERY_DIR,
        build_gallery,
        record_all,
        record_scenario,
    )

    fps = args.fps

    if args.all:
        harnesses = [args.harness] if args.harness else None
        results = record_all(harnesses=harnesses, fps=fps)
    else:
        if not args.scenario:
            raise OtboxError(
                "footage: pass --scenario <name> or --all"
            )
        results = [
            record_scenario(args.scenario, harness=args.harness, fps=fps)
        ]

    gallery_path = build_gallery(results)

    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1

    payload = {
        "action": "footage",
        "status": "ok",
        "gallery": str(gallery_path),
        "gallery_dir": str(FOOTAGE_GALLERY_DIR),
        "fps": fps,
        "counts": counts,
        "clips": [
            {
                "scenario": r.scenario,
                "agent": r.agent,
                "verdict": r.verdict,
                "mp4_path": r.mp4_path,
                "termctrl_path": r.termctrl_path,
                "duration_s": r.duration_s,
                "turn_count": r.turn_count,
                "error": r.error_message,
            }
            for r in results
        ],
    }
    human_lines = [
        f"footage: {counts['PASS']} PASS / {counts['FAIL']} FAIL / "
        f"{counts['SKIP']} SKIP",
        f"  gallery: {gallery_path}",
    ]
    for r in results:
        line = f"  [{r.verdict}] {r.scenario} ({r.agent})"
        if r.mp4_path:
            line += f" → {r.mp4_path}"
        elif r.error_message:
            line += f" — {r.error_message}"
        human_lines.append(line)
    _emit(payload, json_mode=args.json, human="\n".join(human_lines))

    if args.open and not args.json:
        try:
            import subprocess as _sp

            _sp.run(["open", str(gallery_path)], check=False)
        except OSError:
            pass

    # A clip-level FAIL is a real failure; SKIPs are clean (binary/ffmpeg
    # absent). Exit non-zero only on a genuine FAIL.
    return 1 if counts["FAIL"] else 0


def cmd_snapshot_rm(args: argparse.Namespace) -> int:
    if not snapshot_exists(args.name):
        raise OtboxError(f"no snapshot named {args.name!r}")
    delete_snapshot(args.name)
    _emit(
        {"action": "snapshot-rm", "name": args.name},
        json_mode=args.json,
        human=f"snapshot-rm: deleted snapshot {args.name!r}",
    )
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    # --json is shared: it works before *or* after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable JSON output")

    parser = argparse.ArgumentParser(
        prog="otbox",
        description="otbox — snapshottable full test environment for opentraces",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"otbox {__version__}")
    # dest="_cmd" so it never collides with the `run` subcommand's REMAINDER arg.
    sub = parser.add_subparsers(dest="_cmd", required=True)

    def add(name: str, **kw):
        return sub.add_parser(name, parents=[common], **kw)

    p_up = add("up", help="provision (or restore) a box")
    p_up.add_argument("--driver", default="local", choices=available_drivers())
    p_up.add_argument("--from", dest="from_snapshot", metavar="SNAPSHOT", help="restore from a snapshot")
    p_up.add_argument("--seed", metavar="SCENARIO", help="seed the box after provisioning")
    p_up.add_argument("--id", metavar="BOX_ID", help="explicit box id")
    p_up.set_defaults(func=cmd_up)

    p_init = add("init", help="write .otbox.yaml + the agent skill into this repo")
    p_init.add_argument("--force", action="store_true",
                        help="overwrite existing config / skill files")
    p_init.set_defaults(func=cmd_init)

    p_warm = add("warmup", help="provision a Tier 1 box (no seed) — for reuse")
    p_warm.add_argument("--driver", default="remote", choices=available_drivers())
    p_warm.add_argument("--id", metavar="BOX_ID")
    p_warm.set_defaults(func=cmd_warmup)

    p_sync = add("sync", help="rsync the working tree into the box")
    p_sync.add_argument("--box", metavar="BOX_ID")
    p_sync.add_argument("--full-resync", action="store_true",
                        help="wipe the remote source and re-sync from scratch")
    p_sync.set_defaults(func=cmd_sync)

    p_ssh = add("ssh", help="drop into the box's project dir (Tier 1 remote, Tier 0 local)")
    p_ssh.add_argument("--box", metavar="BOX_ID")
    p_ssh.add_argument("--root", action="store_true",
                       help="cd to the box root instead of the project dir")
    p_ssh.set_defaults(func=cmd_ssh)

    p_seed = add("seed", help="materialize a seeded world in a box")
    p_seed.add_argument("scenario", choices=available_seeds())
    p_seed.add_argument("--box", metavar="BOX_ID", help="target box (default: current)")
    p_seed.set_defaults(func=cmd_seed)

    p_snap = add("snapshot", help="freeze a box to a tar archive")
    p_snap.add_argument("name")
    p_snap.add_argument("--box", metavar="BOX_ID")
    p_snap.add_argument("--overwrite", action="store_true")
    p_snap.set_defaults(func=cmd_snapshot)

    p_restore = add("restore", help="fork a fresh box from a snapshot")
    p_restore.add_argument("name")
    p_restore.add_argument("--id", metavar="BOX_ID", help="explicit box id")
    p_restore.add_argument("--driver", default="local", choices=available_drivers(),
                           help="driver to restore into (interchange across tiers)")
    p_restore.set_defaults(func=cmd_restore)

    p_down = add("down", help="tear a box down (zero host residue)")
    p_down.add_argument("--box", metavar="BOX_ID")
    p_down.add_argument("--all", action="store_true", help="tear down every box")
    p_down.set_defaults(func=cmd_down)

    p_run = add("run", help="run a command inside a box")
    p_run.add_argument("--box", metavar="BOX_ID")
    p_run.add_argument("command", nargs=argparse.REMAINDER, help="command after `--`")
    p_run.set_defaults(func=cmd_run)

    p_journey = add("journey", help="run a catalogue journey")
    p_journey.add_argument("name")
    p_journey.add_argument("--box", metavar="BOX_ID")
    p_journey.add_argument("--artifacts", action="store_true", help="bundle artifacts after the run")
    p_journey.add_argument(
        "--save-transcript",
        metavar="PATH",
        help="write a markdown transcript to PATH when the journey passes (the demo-acceptance conviction artifact)",
    )
    p_journey.set_defaults(func=cmd_journey)

    p_art = add("artifacts", help="bundle journey-run evidence for a PR")
    p_art.add_argument("--box", metavar="BOX_ID")
    p_art.add_argument("--label", metavar="LABEL", help="bundle label")
    p_art.set_defaults(func=cmd_artifacts)

    p_fetch = add("fetch-captures", help="download manifested real-agent capture artifacts")
    p_fetch.add_argument("--scenario", help="fetch one scenario (default: all manifested)")
    p_fetch.set_defaults(func=cmd_fetch_captures)

    p_ledger = add("ledger", help="compact a matrix report into the executed-evidence ledger")
    p_ledger.add_argument("--from-matrix", required=True, metavar="REPORT_JSON")
    p_ledger.add_argument("--out", default="run-ledger.json")
    p_ledger.add_argument("--source", default="local-matrix")
    p_ledger.add_argument("--fail-on-red", action="store_true")
    p_ledger.set_defaults(func=cmd_ledger)

    p_status = add("status", help="inspect a box")
    p_status.add_argument("--box", metavar="BOX_ID")
    p_status.set_defaults(func=cmd_status)

    p_list = add("list", help="list boxes, snapshots, drivers, seeds, journeys")
    p_list.set_defaults(func=cmd_list)

    p_matrix = add("matrix", help="run the (journey × base-checkpoint) matrix")
    p_matrix.add_argument("--driver", default="local", choices=available_drivers())
    p_matrix.add_argument("--journey", metavar="PATTERN",
                          help="fnmatch pattern on journey name")
    p_matrix.add_argument("--checkpoint", metavar="PATTERN",
                          help="fnmatch pattern on base checkpoint")
    p_matrix.add_argument("--lane", choices=("core", "extended", "diagnostic"))
    p_matrix.add_argument("--tier", type=int, choices=(0, 1))
    p_matrix.add_argument("--out", metavar="PATH", help="explicit report output path")
    p_matrix.add_argument("--inventory", action="store_true",
                          help="rebuild tests/otbox/catalogue/journey-inventory.md and exit")
    p_matrix.add_argument("--strict", action="store_true",
                          help="fail non-zero on plan 063 SSoT drift "
                          "(missing-in-063, unknown-trajectory, unowned-core-lane)")
    p_matrix.set_defaults(func=cmd_matrix)

    p_snaprm = add("snapshot-rm", help="delete a snapshot")
    p_snaprm.add_argument("name")
    p_snaprm.set_defaults(func=cmd_snapshot_rm)

    p_capref = add(
        "capture-refresh",
        help="drive a simulated-user scenario and snapshot the result (plan 071)",
    )
    p_capref.add_argument("--scenario",
                          help="scenario name under tests/otbox/simulated_users/scenarios/")
    p_capref.add_argument("--all", action="store_true", dest="all_scenarios",
                          help="run every scenario for one agent (requires --agent)")
    p_capref.add_argument("--agent", default=None,
                          help="agent filter for --all (claude/codex/pi/echo)")
    p_capref.add_argument("--check-versions", action="store_true",
                          help="report capture staleness vs installed agent "
                               "binary versions (no boxes, no agents driven)")
    p_capref.add_argument("--dry-run", action="store_true",
                          help="report what would happen without running anything")
    p_capref.add_argument("--base-checkpoint", default="c-installed-source",
                          help="base checkpoint to fork from (default: c-installed-source)")
    p_capref.set_defaults(func=cmd_capture_refresh)

    p_accept = add(
        "acceptance",
        help="drive the 5 B0 acceptance scenarios and write a scored report (issue #61)",
    )
    p_accept.add_argument("--echo", action="store_true",
                          help="drive the deterministic synthetic harness "
                               "(no real agent, no network) — default-CI safe")
    p_accept.add_argument("--agent", default=None,
                          help="real agent to drive (claude/codex/pi); "
                               "default claude. Ignored under --echo")
    p_accept.add_argument("--scenario", default=None,
                          help="drive a single acceptance scenario "
                               "(default: all 5)")
    p_accept.add_argument("--out", default=None,
                          help="report output path (default: tests/otbox/"
                               "captures/_acceptance/report.json; echo mode "
                               "defaults to the gitignored echo-report.json)")
    p_accept.set_defaults(func=cmd_acceptance)

    p_footage = add(
        "footage",
        help="record termctrl journey footage and build the gallery (additive)",
    )
    p_footage.add_argument(
        "--scenario",
        help="scenario name under tests/otbox/simulated_users/scenarios/",
    )
    p_footage.add_argument(
        "--harness",
        choices=("claude", "codex", "pi", "echo"),
        help="override the agent/harness to record against (default: scenario's agent)",
    )
    p_footage.add_argument(
        "--all",
        action="store_true",
        help="record every scenario for its native agent, then build the gallery",
    )
    p_footage.add_argument(
        "--fps", type=int, default=20, help="MP4 frames per second (default: 20)"
    )
    p_footage.add_argument(
        "--open",
        action="store_true",
        help="open the gallery in the default browser (macOS `open`)",
    )
    p_footage.set_defaults(func=cmd_footage)

    p_image = add("image", help="build the Linux runtime image for the docker driver")
    image_sub = p_image.add_subparsers(dest="_image_cmd", required=True)
    p_image_build = image_sub.add_parser("build", parents=[common], help="docker build the runtime image")
    p_image_build.add_argument("--tag", default="otbox-runtime:latest")
    p_image_build.set_defaults(func=cmd_image)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # `run` takes a REMAINDER; strip a leading "--" if argparse kept it.
    if args._cmd == "run" and args.command and args.command[0] == "--":
        args.command = args.command[1:]
    try:
        return args.func(args)
    except (OtboxError, BoxNotFound) as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "command": args._cmd}, indent=2))
        else:
            print(f"otbox: error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - surface unexpected failures cleanly
        if args.json:
            print(json.dumps({"error": str(exc), "type": type(exc).__name__}, indent=2))
        else:
            print(f"otbox: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
