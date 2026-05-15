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
from pathlib import Path

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

_OTBOX_SKILL_TEMPLATE = """\
---
name: otbox
description: Snapshottable full test environment for opentraces. Provisions \
isolated boxes (local fs, docker, or SSH-leased remote), seeds a \
fully-populated opentraces world, runs declarative journey TOMLs, \
collects PR-ready artifacts, tears down with zero host residue.
---

# otbox — agent quick reference

otbox runs from the repo-root shim ``./otbox`` (mirrors ``otd``).
Every command accepts ``--json`` for stable machine-readable output.

## Lifecycle (Tier 0 local, the default)

```bash
./otbox up --seed smoke                  # provision + seed in one
./otbox journey cli-publish-happy-path   # run a catalogue journey
./otbox artifacts                        # bundle evidence for a PR
./otbox down                             # tear down, zero residue
```

## Lifecycle (Tier 1 remote over SSH/Tailscale)

```bash
export OT_OTBOX_TIER1=1
export OT_OTBOX_SSH_TARGET=user@host    # or a Tailscale name
./otbox warmup                          # provision a Tier 1 box
./otbox sync                            # rsync the dirty working tree
./otbox seed smoke
./otbox snapshot t1-base
./otbox down
./otbox up --from t1-base --driver remote
./otbox journey cli-publish-happy-path
```

## Catalogue

Add coverage by adding a TOML file under
``tests/otbox/catalogue/journeys/``. The runner is generic. See plan
``kb/plans/060-otbox-test-environment.md`` for the schema and
``kb/plans/061-otbox-tailscale-local-tier1.md`` for Tier 1 specifics.

## Inspection

```bash
./otbox list           # boxes, snapshots, drivers, seeds, journeys
./otbox status         # the current box
./otbox ssh            # drop into the box's project dir
./otbox logs --box ID  # per-step transcripts
```
"""


def cmd_init(args: argparse.Namespace) -> int:
    """Write ``.otbox.yaml`` and the agent SKILL into the current repo."""
    from .env import REPO_ROOT

    repo_root = REPO_ROOT
    otbox_yaml = repo_root / ".otbox.yaml"
    skill_dir = repo_root / ".agents" / "skills" / "otbox"
    skill_md = skill_dir / "SKILL.md"

    written: list[str] = []
    skipped: list[str] = []

    for path, body in ((otbox_yaml, _OTBOX_YAML_TEMPLATE),
                       (skill_md, _OTBOX_SKILL_TEMPLATE)):
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
    box = resolve_box(args.box)
    driver = get_driver(box.driver)
    result = run_journey(driver, box, args.name)
    _persist_journey(box, result)

    bundle = None
    if args.artifacts:
        bundle = str(collect_artifacts(box, [result], run_label=result.name))

    payload = {"action": "journey", **result.to_dict()}
    if bundle:
        payload["artifacts"] = bundle
    human_lines = [f"journey: {result.name} -> {result.verdict}"]
    if result.reason:
        human_lines.append(f"  reason: {result.reason}")
    for s in result.steps:
        human_lines.append(f"  step[{s.index}] {s.step_id} ({s.type}): {'OK' if s.ok else 'FAIL — ' + s.message}")
    for a in result.assertions:
        human_lines.append(f"  assert[{a.index}] {a.kind}: {'OK' if a.ok else 'FAIL — ' + a.message}")
    if bundle:
        human_lines.append(f"  artifacts: {bundle}")
    _emit(payload, json_mode=args.json, human="\n".join(human_lines))
    return 0 if result.verdict in ("PASS", "SKIP") else 1


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
    p_journey.set_defaults(func=cmd_journey)

    p_art = add("artifacts", help="bundle journey-run evidence for a PR")
    p_art.add_argument("--box", metavar="BOX_ID")
    p_art.add_argument("--label", metavar="LABEL", help="bundle label")
    p_art.set_defaults(func=cmd_artifacts)

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
