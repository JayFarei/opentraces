"""B3a — mechanical ``--json`` envelope contract sweep over the public CLI.

Three layers, all CI-safe (each execution is a SUBPROCESS with an
explicitly isolated ``HOME`` and a hard timeout — a hanging or
memory-pathological public command becomes a named test failure instead
of a wedged CI job):

1. **Registry contract** — every visible (non-hidden) leaf command either
   declares a local ``--json`` flag or is on the frozen
   ``GLOBAL_JSON_ONLY`` list (commands that rely on the root-level
   ``opentraces --json <cmd>`` mode). Adding a new public command without
   a local ``--json`` fails this sweep; the fix is to add the flag, or —
   as a reviewed contract change — extend the frozen list.

2. **Execution contract** — every public command with no required
   arguments is actually executed under ``--json`` on the empty isolated
   world. Exit 0 must produce parseable JSON (an object envelope). Any
   exit code must never leak a Python traceback (the deterministic taste
   floor from the B2 spec).

3. **Envelope-shape snapshot** — for executed commands that exit 0, the
   envelope's top-level key set (and ``schema_version`` when present) is
   compared against the committed ``json_envelope_shapes.json``. Shape
   drift fails; the fix is to revert, or to update the snapshot in the
   same PR as a reviewed contract change (and bump the envelope's
   ``schema_version`` when the envelope is one of the frozen
   ``opentraces.*.v1`` consumer contracts).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import click
import pytest

from opentraces.cli import main as click_root

# Hard per-command budget. A public command that cannot answer --json on an
# EMPTY world inside this window is itself a finding.
COMMAND_TIMEOUT_S = 30.0

SNAPSHOT_PATH = Path(__file__).parent / "json_envelope_shapes.json"

# Commands that intentionally rely on the ROOT ``--json`` flag instead of a
# local one. FROZEN: shrinking this list is always fine; growing it is a
# reviewed contract change (new public commands must ship a local --json).
GLOBAL_JSON_ONLY = {
    "auth login",
    "auth logout",
    "auth whoami",
    "capsule issue",
    "capsule share",
    "capsule verdict",
    "completions install",
    "completions uninstall",
    "config set",
    "config show",
    "config tracking-mode",
    "ctx anchor-for-step",
    "doctor",
    "init",
    "remove",
    "security sanitize",
    "setup auth",
    "setup claude-code",
    "setup codex-cli",
    "setup git",
    "setup llm-review",
    "setup privacy-filter",
    "setup skill",
    "setup trufflehog",
    "setup upgrade",
    "setup watcher install",
    "setup watcher restart",
    "setup watcher start",
    "setup watcher stop",
    "setup watcher uninstall",
    "trail blame pr create",
    "trail blame pr render",
    "trail blame pr update",
}

# Zero-required-arg commands we do NOT execute in the sweep, with reasons.
# Keep this list honest: only network, interactive, or destructive-on-real-
# substrate commands belong here.
EXEC_DENYLIST = {
    "auth login": "opens a real HF device-authorization flow (network)",
    "setup auth": "starts the same HF device-authorization flow as auth login "
                  "(opens the browser verification page)",
    "auth logout": "mutates credential state; logout flow covered in tests/cli/test_auth_group.py",
    "completions install": "writes to the user's shell profile",
    "completions uninstall": "edits the user's shell profile",
    "setup upgrade": "spawns the package manager (network)",
    "capture-otlp start": "spawns a long-lived daemon process",
    "capture-otlp restart": "spawns a long-lived daemon process",
    "setup watcher start": "spawns the long-lived watcher daemon (sweep timeout)",
    "setup watcher restart": "spawns the long-lived watcher daemon (sweep timeout)",
    "init": "interactive project scan prompt flow; covered in tests/cli/test_cli_init.py",
    "remove": "interactive confirmation flow; covered by dedicated tests",
    "dataset publish": "real HF publish path; covered by the otbox live-hf lane",
    "bucket security run": "mutates real bucket trace records (re-sanitizes + "
                           "rewrites envelopes); covered in tests/cli/test_bucket_cli.py",
}


def _walk(group, prefix=()):
    for name in sorted(group.commands):
        sub = group.commands[name]
        path = (*prefix, name)
        if isinstance(sub, click.Group):
            yield from _walk(sub, path)
        else:
            yield " ".join(path), sub


def _public_commands():
    out = []

    def _visible_walk(group, prefix=()):
        for name in sorted(group.commands):
            sub = group.commands[name]
            if getattr(sub, "hidden", False):
                continue
            path = (*prefix, name)
            if isinstance(sub, click.Group):
                _visible_walk(sub, path)
            else:
                out.append((" ".join(path), sub))

    _visible_walk(click_root)
    return out


def _has_local_json(cmd) -> bool:
    return any(
        p.name in ("as_json", "json", "json_mode") or "--json" in (p.opts or [])
        for p in cmd.params
    )


def _required_args(cmd) -> list[str]:
    required = []
    for p in cmd.params:
        if isinstance(p, click.Argument) and p.required:
            required.append(p.name)
        elif getattr(p, "required", False):
            required.append(p.name)
    return required


PUBLIC = _public_commands()


def test_every_public_command_offers_json():
    """Layer 1: local --json everywhere, frozen exemptions only."""
    missing = [
        path
        for path, cmd in PUBLIC
        if not _has_local_json(cmd) and path not in GLOBAL_JSON_ONLY
    ]
    assert not missing, (
        "public commands without a local --json flag (add the flag, or — as "
        f"a reviewed contract change — extend GLOBAL_JSON_ONLY): {missing}"
    )


def test_global_json_only_list_is_not_stale():
    """Entries must still exist and still lack a local --json flag."""
    by_path = dict(PUBLIC)
    stale = [
        path
        for path in sorted(GLOBAL_JSON_ONLY)
        if path not in by_path or _has_local_json(by_path[path])
    ]
    assert not stale, (
        f"GLOBAL_JSON_ONLY entries that gained --json or were removed: {stale} "
        "— shrink the frozen list"
    )


_EXECUTABLE = [
    (path, cmd)
    for path, cmd in PUBLIC
    if not _required_args(cmd) and path not in EXEC_DENYLIST
]


def _isolated_env(home: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OPENTRACES_", "HF_", "CLAUDE"))
    }
    env["HOME"] = str(home)
    env["OPENTRACES_NO_COLOR"] = "1"
    # Belt-and-braces: even if a future command calls webbrowser.open, the
    # stdlib honors $BROWSER — `true` is a silent no-op, so the sweep can
    # never pop a real browser window on the developer's machine.
    env["BROWSER"] = "true"
    return env


def _envelope_payload(stdout: str):
    """Parse the JSON envelope using the product's two emission conventions:
    pure-JSON stdout (local ``--json`` flags) or human text followed by a
    ``---OPENTRACES_JSON---`` sentinel (the root ``--json`` mode). Mirrors
    ``tests/otbox/journey.py::_extract_json``."""
    stripped = (stdout or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    marker = "---OPENTRACES_JSON---"
    if marker in stdout:
        tail = stdout.split(marker, 1)[1].strip()
        try:
            return json.loads(tail)
        except ValueError:
            return None
    return None


class _RunResult:
    def __init__(self, exit_code: int, output: str, stderr: str, timed_out: bool):
        self.exit_code = exit_code
        self.output = output
        self.stderr = stderr
        self.timed_out = timed_out


def _run_json(path: str) -> _RunResult:
    """Run one command in its OWN fresh isolated home.

    Per-command isolation keeps every invocation order-independent (a
    shared home let earlier commands' side effects change later commands'
    envelopes, which made the shape snapshot non-deterministic under
    randomized test order).
    """
    home = Path(tempfile.mkdtemp(prefix="ot-json-sweep-home-"))
    argv = [sys.executable, "-m", "opentraces", "--json", *path.split()]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
            env=_isolated_env(home),
            cwd=str(home),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        return _RunResult(
            -1,
            (exc.stdout or b"").decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or ""),
            (exc.stderr or b"").decode("utf-8", "replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or ""),
            True,
        )
    return _RunResult(proc.returncode, proc.stdout, proc.stderr, False)


# Genuine product gaps the sweep surfaced, tracked on issue #25 (Phase B3a
# findings). Entries here xfail INSTEAD of red so the sweep gates NEW
# regressions while the known set burns down; a fixed entry hard-fails until
# it is removed (the unexpected-pass guard keeps this list honest).
KNOWN_FINDINGS = {
    # WAIVED (deliberate shape choice, not an unfixed gap): the original
    # finding — plain '(no enlisted projects)' text under --json — is fixed;
    # the empty world now emits `[]`. The envelope stays a JSON ARRAY because
    # the non-empty path emits one report object per enlisted project and
    # live consumers index it positionally (`0.jsonl_activity` in
    # tests/otbox/catalogue/journeys/migration-u-setup-5-watcher-tick-safe.toml,
    # `tick[0]` in tests/integration/harness/trace_trails_full_stack_demo.py).
    # A type-stable `[]` beats an empty-only object envelope; converting the
    # whole command to `{"status": ..., "reports": [...]}` is a coordinated
    # consumer-contract change tracked on issue #25.
    "setup watcher tick": (
        "emits a type-stable JSON array (empty world: `[]`), not an object "
        "envelope — array shape retained for positional consumers; "
        "object-envelope conversion is a coordinated contract change"
    ),
}


def _execution_contract_error(path: str) -> str | None:
    result = _run_json(path)
    if result.timed_out:
        return (
            f"`opentraces --json {path}` exceeded {COMMAND_TIMEOUT_S}s on an "
            "empty world — a hanging public command is a product finding"
        )
    combined = (result.output or "") + (result.stderr or "")
    if "Traceback (most recent call last)" in combined:
        return f"`opentraces --json {path}` leaked a traceback:\n{combined[-800:]}"
    if result.exit_code == 0:
        doc = _envelope_payload(result.output)
        if doc is None:
            return (
                f"`opentraces --json {path}` exited 0 without a parseable "
                f"JSON envelope (pure JSON or ---OPENTRACES_JSON--- "
                f"delimited):\n{result.output[:400]}"
            )
        if not isinstance(doc, dict):
            return (
                f"`opentraces --json {path}` envelope must be a JSON object, "
                f"got {type(doc).__name__}"
            )
    return None


@pytest.mark.parametrize(
    "path", [p for p, _ in _EXECUTABLE], ids=[p.replace(" ", "_") for p, _ in _EXECUTABLE]
)
def test_json_execution_contract(path):
    """Layer 2: exit 0 => parseable JSON object; never a traceback or hang."""
    error = _execution_contract_error(path)
    if path in KNOWN_FINDINGS:
        if error is None:
            pytest.fail(
                f"`{path}` no longer exhibits the tracked finding — remove it "
                "from KNOWN_FINDINGS (and close it on issue #25)"
            )
        pytest.xfail(f"tracked on issue #25: {KNOWN_FINDINGS[path]}")
    assert error is None, error


def _current_shapes() -> dict:
    shapes = {}
    for path, _ in _EXECUTABLE:
        result = _run_json(path)
        if result.timed_out or result.exit_code != 0:
            continue
        doc = _envelope_payload(result.output)
        if not isinstance(doc, dict):
            continue
        shapes[path] = {
            "schema_version": doc.get("schema_version"),
            "top_level_keys": sorted(doc.keys()),
        }
    return shapes


def test_envelope_shapes_match_snapshot():
    """Layer 3: top-level envelope shape is frozen per command.

    Regenerate intentionally with:
        .venv/bin/python -m tests.cli.regen_json_envelope_shapes
    (which re-runs THIS test under the regen flag, inside the conftest's
    isolated HOME — never against the developer's real ~/.opentraces) and
    review the diff as a contract change (bump the envelope's
    schema_version when it is a frozen ``opentraces.*.v1`` contract).
    """
    import os

    if os.environ.get("OT_REGEN_ENVELOPE_SHAPES") == "1":
        shapes = _current_shapes()
        SNAPSHOT_PATH.write_text(
            json.dumps(shapes, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        pytest.skip(f"regenerated {len(shapes)} envelope shapes at {SNAPSHOT_PATH}")
    assert SNAPSHOT_PATH.exists(), (
        f"missing committed snapshot {SNAPSHOT_PATH}; regenerate with "
        "`.venv/bin/python -m tests.cli.regen_json_envelope_shapes`"
    )
    committed = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    current = _current_shapes()
    # A committed command absent from `current` is NOT drift: some envelopes
    # only materialize when an external binary is installed (e.g. `setup
    # trufflehog` exits non-zero on substrates without the binary). Drift is
    # a command whose shape CHANGED, or a new exit-0 envelope with no
    # committed shape.
    drift = {}
    for path in sorted(set(committed) | set(current)):
        if path not in current:
            continue
        if committed.get(path) != current.get(path):
            drift[path] = {
                "committed": committed.get(path),
                "current": current.get(path),
            }
    assert not drift, (
        "envelope shape drift (revert, or regenerate the snapshot in the "
        f"same PR as a reviewed contract change): {json.dumps(drift, indent=1)[:2000]}"
    )
