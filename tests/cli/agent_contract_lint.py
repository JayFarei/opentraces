"""Agent-contract lint (ADR-0007 L1-L6) — a pure lint over the live CLI.

The CLI v7 epic (#129) locks six agent-facing contract laws. This module
is the mechanical enforcer, modeled on ``tests/otbox/catalogue_lint.py``:
a pure violation-producing core, an in-module QUARANTINE of today's known
violators (each tagged with the family slice that owns the fix), and a gate
(``tests/cli/test_agent_contract_lint.py``) that asserts zero violations
OUTSIDE quarantine.

The six laws:

  L1  must-terminate    every visible no-required-arg leaf command answers
                        ``--json`` within COMMAND_TIMEOUT_S on an empty world;
                        the named hang-cluster terminates on a small seeded
                        bucket (bounded work, not just bounded output).
  L2  must-not-prompt   guard-listed interactive commands, run with closed
                        stdin under ``--json``, emit ZERO prompt bytes, exit
                        non-zero, and carry a parseable
                        ``{status:'error', error:{code:'INTERACTIVE_REQUIRED'}}``.
  L3  valid JSON        every visible command exposing ``--json`` emits stdout
                        that ``json.loads`` parses (no ``---OPENTRACES_JSON---``
                        sentinel under explicit ``--json``).
  L4  remote uniformity no VISIBLE command advertises ``--remote-bucket`` /
                        ``--force-remote-bucket`` (hidden deprecated params
                        allowed); a read verb advertising a remote takes a
                        single ``--remote TEXT``.
  L5  uniform envelope  every visible read verb's ``--json`` output carries a
                        top-level ``status`` (or ``ok``) AND ``schema_version``.
  L6  self-consistency  on one seeded bucket, the trace count and security
                        version reported by ``bucket status`` / ``bucket
                        manifest`` / ``bucket security status`` are all equal.

The clause functions are pure (they take structured inputs — a Click command,
a parsed doc, a run-result) so the gate's kill tests can plant each violation
class directly. Execution (subprocess, seeded bucket) is injected by the gate.
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import click

from opentraces.cli import main as CLI_ROOT

# Hard per-command budget (mirrors tests/cli/test_json_surface_sweep.py).
COMMAND_TIMEOUT_S = 30.0

SENTINEL = "---OPENTRACES_JSON---"

# Prompt bytes a non-interactive command must never emit (L2).
PROMPT_BYTES = ("[y/N]", "[Y/n]", "Proceed?", "Trust and run it?", "choose", "◇")

# Read-verb family roots (L5/L6): the surfaces whose --json output an agent
# reads as data. Write/control verbs (setup*, dataset run, auth) are excluded.
READ_FAMILY_ROOTS = ("trace", "ctx", "trail", "bucket", "status", "doctor", "config")

# FROZEN_ENVELOPE_EXCEPTIONS — schema_version strings whose byte-shape is a
# frozen downstream-consumer contract (replay / RL / viewer / dashboards /
# Slack / PR). For these, the schema_version tag IS the shape contract: a field
# may not be added without a version bump, so they cannot carry the uniform L5
# ``status`` header without silently breaking byte-compatibility. L5 exempts
# them BY DESIGN — freeze beats uniformity. This is a first-class, documented
# mechanism (not a quarantine deferral); the exemption is keyed on the frozen
# schema_version VALUE, never on the command path, so a non-frozen surface
# cannot ride it (proven by test_l5_frozen_envelope_exemption). Mirrors
# opentraces.cli.ctx.FROZEN_ENVELOPE_SCHEMA_VERSIONS.
FROZEN_ENVELOPE_EXCEPTIONS = frozenset({
    "opentraces.context_tree.v1",
    "opentraces.context_reads.v1",
    "opentraces.context_writes.v1",
    "opentraces.context_resolve.v1",
    "opentraces.context_resume.v1",
    "opentraces.ctx.list.v2",
    "opentraces.ctx.info.v2",
})

# Commands we do NOT auto-execute in the sweep (network / daemon / interactive /
# destructive-on-real-substrate). Mirrors test_json_surface_sweep.EXEC_DENYLIST;
# kept local so this lint is self-contained.
EXEC_DENYLIST = frozenset(
    {
        "auth login",
        "auth logout",
        "setup auth",
        "completions install",
        "completions uninstall",
        "setup upgrade",
        "capture-otlp start",
        "capture-otlp restart",
        "setup watcher start",
        "setup watcher restart",
        "init",
        "remove",
        "dataset publish",
        "bucket security run",
        # bare setup + the interactive pickers are L2 guard targets: they exit
        # non-zero by design under --json, so they are not L1/L3 exit-0 targets.
        "setup",
        "setup trufflehog",
        "setup llm-review",
    }
)


@dataclass
class Violation:
    clause: str  # L1..L6
    command: str
    message: str
    quarantined: bool = False

    def __str__(self) -> str:  # pragma: no cover - display helper
        flag = " [quarantined]" if self.quarantined else ""
        return f"{self.clause} {self.command}: {self.message}{flag}"


@dataclass
class RunResult:
    """One subprocess CLI invocation outcome."""

    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def combined(self) -> str:
        return (self.stdout or "") + (self.stderr or "")


# --------------------------------------------------------------------------
# QUARANTINE — today's known violators, each owned by a family slice that will
# empty its entries. The gate asserts zero violations OUTSIDE this table; the
# meta-tests assert it only shrinks and that F1's own clauses (L2, L3) are and
# stay empty. A quarantine entry names (clause, command, reason, owner_slice).
# --------------------------------------------------------------------------
@dataclass
class QuarantineEntry:
    clause: str
    command: str
    reason: str
    owner_slice: str


QUARANTINE: list[QuarantineEntry] = [
    # L5 — visible read/family verbs whose --json envelope predates the uniform
    # {status, schema_version} header. Each owning slice adopts cli/_envelope
    # and empties its entries. Enumerated from the live CLI on an empty world.
    #
    # The trace-family entries (L4 trace get/query; L5 trace skills / trace
    # index *) were retired by the #163 trace slice: the dual --remote-bucket
    # flags are now hidden deprecated aliases, and skills / index are hidden
    # plumbing (no longer visible read verbs), so none of them surface a
    # violation any more. The #164 ctx slice retired `ctx list` and the #165
    # trail slice retired `trail graph` (both adopted the uniform envelope), and
    # the #161 status slice adopted the uniform envelope on `bucket status` and
    # `doctor`. The #162 bucket slice then emptied the last six bucket-family
    # entries: `bucket verify`/`repair`/`reclaim` adopted cli/_envelope (uniform
    # header), and `bucket manifest`/`bucket remote status`/`bucket remote diff`
    # became hidden (the canonical surfaces are `bucket list` and the
    # `bucket sync {status,diff}` mounts, which carry the header from birth), so
    # none surface a visible-read-verb violation any more. The quarantine is now
    # empty — every clause is enforced live outside it.
]

# Clauses F1 (this slice) is responsible for; they must have NO quarantine
# entry — proving the emission fixes landed rather than being deferred.
F1_OWNED_CLAUSES = ("L1", "L2", "L3")

# The quarantine may only shrink. A slice that lands empties entries; growing
# the table (a new deferred violator) requires bumping this baseline in review,
# exactly like catalogue_lint's MAX cap.
QUARANTINE_BASELINE = len(QUARANTINE)


def _quarantined(clause: str, command: str) -> bool:
    return any(q.clause == clause and q.command == command for q in QUARANTINE)


# --------------------------------------------------------------------------
# registry walkers
# --------------------------------------------------------------------------
def visible_leaf_commands(root: click.Group = CLI_ROOT) -> list[tuple[str, click.Command]]:
    out: list[tuple[str, click.Command]] = []

    def _walk(group: click.Group, prefix: tuple[str, ...] = ()) -> None:
        for name in sorted(group.commands):
            sub = group.commands[name]
            if getattr(sub, "hidden", False):
                continue
            path = (*prefix, name)
            if isinstance(sub, click.Group):
                _walk(sub, path)
            else:
                out.append((" ".join(path), sub))

    _walk(root)
    return out


def has_json_option(cmd: click.Command) -> bool:
    return any(
        p.name in ("as_json", "json", "json_mode") or "--json" in (p.opts or [])
        for p in cmd.params
    )


def required_args(cmd: click.Command) -> list[str]:
    out = []
    for p in cmd.params:
        if isinstance(p, click.Argument) and p.required:
            out.append(p.name)
        elif getattr(p, "required", False):
            out.append(p.name)
    return out


def _opts(cmd: click.Command) -> set[str]:
    out: set[str] = set()
    for p in cmd.params:
        out.update(p.opts or [])
    return out


def is_read_verb(path: str) -> bool:
    root = path.split(" ", 1)[0]
    return root in READ_FAMILY_ROOTS


# --------------------------------------------------------------------------
# L4 — remote uniformity (STATIC, no execution)
# --------------------------------------------------------------------------
def l4_remote_uniformity(path: str, cmd: click.Command) -> list[Violation]:
    # Only ADVERTISED (non-hidden) params count — the L4 contract explicitly
    # allows hidden deprecated aliases (``hidden=True`` on the option), so a
    # command that collapses to a single visible ``--remote`` while keeping the
    # old dual flags callable-but-hidden is clean.
    visible_opts: set[str] = set()
    for p in cmd.params:
        if getattr(p, "hidden", False):
            continue
        visible_opts.update(p.opts or [])
    out: list[Violation] = []
    dual = visible_opts & {"--remote-bucket", "--force-remote-bucket"}
    if dual:
        out.append(
            Violation(
                "L4",
                path,
                f"visible command advertises {sorted(dual)}; read verbs take a "
                "single --remote TEXT (hidden deprecated aliases allowed)",
            )
        )
    return out


# --------------------------------------------------------------------------
# L5 — uniform envelope header (needs the parsed --json doc)
# --------------------------------------------------------------------------
def l5_uniform_envelope(path: str, doc: Any) -> list[Violation]:
    if not isinstance(doc, dict):
        return [Violation("L5", path, f"--json output is not a JSON object ({type(doc).__name__})")]
    # Frozen consumer envelopes are exempt from the uniform header BY DESIGN
    # (see FROZEN_ENVELOPE_EXCEPTIONS): their byte-shape is a version-tagged
    # contract, so a status field cannot be added without a schema bump. The
    # exemption is keyed on the frozen schema_version VALUE, so a non-frozen
    # surface (even at the same command path) still fails L5.
    if doc.get("schema_version") in FROZEN_ENVELOPE_EXCEPTIONS:
        return []
    has_status = "status" in doc or "ok" in doc
    has_schema = "schema_version" in doc
    missing = []
    if not has_status:
        missing.append("status/ok")
    if not has_schema:
        missing.append("schema_version")
    if missing:
        return [Violation("L5", path, f"top-level envelope missing {missing}")]
    return []


# --------------------------------------------------------------------------
# L3 — valid JSON, no sentinel (needs a RunResult)
# --------------------------------------------------------------------------
def l3_valid_json(path: str, res: RunResult) -> list[Violation]:
    if res.timed_out:
        return []  # a hang is an L1 finding, not an L3 one
    if SENTINEL in (res.stdout or ""):
        return [Violation("L3", path, "stdout carries the ---OPENTRACES_JSON--- sentinel under explicit --json")]
    if res.exit_code != 0:
        return []  # non-zero paths may legitimately print human errors
    stripped = (res.stdout or "").strip()
    if not stripped:
        return [Violation("L3", path, "exit 0 under --json but emitted no JSON")]
    try:
        json.loads(stripped)
    except ValueError as exc:
        return [Violation("L3", path, f"exit 0 under --json but stdout is not valid JSON: {exc}")]
    return []


# --------------------------------------------------------------------------
# L1 — must terminate (needs a RunResult)
# --------------------------------------------------------------------------
def l1_terminates(path: str, res: RunResult) -> list[Violation]:
    out: list[Violation] = []
    if res.timed_out:
        out.append(Violation("L1", path, f"did not terminate within {COMMAND_TIMEOUT_S}s"))
        return out
    if "Traceback (most recent call last)" in res.combined:
        out.append(Violation("L1", path, "leaked a Python traceback"))
    return out


# --------------------------------------------------------------------------
# L2 — must not prompt (needs a RunResult from closed-stdin --json run)
# --------------------------------------------------------------------------
def l2_no_prompt(path: str, res: RunResult, *, full_contract: bool) -> list[Violation]:
    out: list[Violation] = []
    for token in PROMPT_BYTES:
        if token in res.combined:
            out.append(Violation("L2", path, f"emitted a prompt byte {token!r} under --json/closed stdin"))
            break
    if not full_contract:
        return out
    if res.timed_out:
        out.append(Violation("L2", path, f"hung instead of refusing within {COMMAND_TIMEOUT_S}s"))
        return out
    if res.exit_code == 0:
        out.append(Violation("L2", path, "exited 0 instead of refusing with a non-zero code"))
    doc = _parse_json(res.stdout)
    code = None
    if isinstance(doc, dict):
        err = doc.get("error")
        if isinstance(err, dict):
            code = err.get("code")
    if not (isinstance(doc, dict) and doc.get("status") == "error" and code):
        out.append(
            Violation(
                "L2",
                path,
                "did not emit a parseable {status:'error', error:{code:...}} envelope",
            )
        )
    return out


# --------------------------------------------------------------------------
# L6 — cross-sibling self-consistency (needs the three parsed docs)
# --------------------------------------------------------------------------
def _extract_trace_count(doc: Any) -> int | None:
    if not isinstance(doc, dict):
        return None
    for keys in (
        ("trace_count",),
        ("bucket", "trace_count"),
        ("manifest", "trace_records", "object_count"),
        ("bucket", "trace_records", "object_count"),
        ("security", "total"),
        ("store", "trace_count"),
    ):
        node: Any = doc
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                node = None
                break
        if isinstance(node, int):
            return node
    return None


def _extract_security_version(doc: Any) -> str | None:
    if not isinstance(doc, dict):
        return None
    for keys in (
        ("security_version",),
        ("bucket", "security_version"),
        ("manifest", "security_version"),
        ("security", "security_version"),
    ):
        node: Any = doc
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                node = None
                break
        if isinstance(node, str):
            return node
    return None


def l6_self_consistency(docs: dict[str, Any]) -> list[Violation]:
    """``docs`` maps sibling command -> its parsed --json doc."""
    counts = {name: _extract_trace_count(d) for name, d in docs.items()}
    versions = {name: _extract_security_version(d) for name, d in docs.items()}
    out: list[Violation] = []
    count_vals = {v for v in counts.values() if v is not None}
    if len(count_vals) > 1:
        out.append(
            Violation("L6", "bucket status/manifest/security", f"trace counts disagree: {counts}")
        )
    version_vals = {v for v in versions.values() if v is not None}
    if len(version_vals) > 1:
        out.append(
            Violation("L6", "bucket status/manifest/security", f"security versions disagree: {versions}")
        )
    return out


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _parse_json(stdout: str) -> Any:
    """Parse the JSON envelope tolerating BOTH conventions (pure JSON under
    explicit --json, or a legacy ``---OPENTRACES_JSON---`` sentinel)."""
    stripped = (stdout or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    if SENTINEL in stdout:
        tail = stdout.split(SENTINEL, 1)[1].strip()
        try:
            return json.loads(tail)
        except ValueError:
            return None
    return None


# The hang-cluster: commands historically at risk of an O(N) whole-corpus read.
# On a SMALL seeded bucket every one must terminate (bounded work). Args are
# benign (a missing id / HEAD) — a not-found lookup must still terminate.
HANG_CLUSTER: list[tuple[str, list[str]]] = [
    ("ctx resume", ["ctx", "resume", "nonexistent-node"]),
    ("ctx show", ["ctx", "show", "nonexistent-node"]),
    ("bucket manifest", ["bucket", "manifest"]),
    ("bucket verify --sample", ["bucket", "verify", "--sample", "5"]),
    ("trail graph", ["trail", "graph"]),
    ("trail resolve", ["trail", "resolve", "ot://file/README.md/line/1/origin"]),
    ("trail track --all", ["trail", "track", "--all", "--limit", "50"]),
    ("trail blame commit", ["trail", "blame", "commit", "HEAD"]),
]

# L2 guard-listed commands. ``full`` targets deterministically reach the guard
# under bare --json and must carry the full refusal contract; the others are
# asserted zero-prompt only (their guard is environment-gated, e.g. trufflehog
# auto-enables when the binary is already installed).
GUARD_COMMANDS: list[tuple[str, list[str], bool]] = [
    ("setup", ["setup"], True),
    ("setup llm-review", ["setup", "llm-review"], True),
    ("setup trufflehog", ["setup", "trufflehog"], False),
]


def lint_agent_contract(
    sweep_runner: Callable[[str], RunResult],
    guard_runner: Callable[[list[str]], RunResult],
    l6_docs: dict[str, Any] | None = None,
    hang_runner: Callable[[list[str]], RunResult] | None = None,
) -> tuple[list[Violation], list[Violation]]:
    """Run every clause and split into ``(failing, quarantined)``.

    ``sweep_runner(path)`` runs ``opentraces --json <path>`` with closed stdin
    on a fresh empty world (for L1/L3/L5). ``guard_runner(argv)`` runs the L2
    targets. ``l6_docs`` are the three sibling docs from one seeded bucket
    (when omitted, L6 is skipped — the gate always supplies them).
    ``hang_runner(argv)`` runs the named hang-cluster on a small seeded bucket
    for L1's bounded-work assertion (when omitted, that check is skipped).
    """
    raw: list[Violation] = []

    commands = visible_leaf_commands()

    # L4 — static.
    for path, cmd in commands:
        raw.extend(l4_remote_uniformity(path, cmd))

    # L1 / L3 / L5 — one sweep run per executable read/leaf command.
    for path, cmd in commands:
        if not has_json_option(cmd):
            continue
        if required_args(cmd) or path in EXEC_DENYLIST:
            continue
        res = sweep_runner(path)
        raw.extend(l1_terminates(path, res))
        raw.extend(l3_valid_json(path, res))
        if is_read_verb(path) and res.exit_code == 0 and not res.timed_out:
            doc = _parse_json(res.stdout)
            if doc is not None:
                raw.extend(l5_uniform_envelope(path, doc))

    # L1 (part B) — the named hang-cluster terminates on a small seeded bucket.
    if hang_runner is not None:
        for name, argv in HANG_CLUSTER:
            raw.extend(l1_terminates(name, hang_runner(argv)))

    # L2 — guard commands.
    for name, argv, full in GUARD_COMMANDS:
        res = guard_runner(argv)
        raw.extend(l2_no_prompt(name, res, full_contract=full))

    # L6 — cross-sibling.
    if l6_docs:
        raw.extend(l6_self_consistency(l6_docs))

    failing: list[Violation] = []
    quarantined: list[Violation] = []
    for v in raw:
        if _quarantined(v.clause, v.command):
            v.quarantined = True
            quarantined.append(v)
        else:
            failing.append(v)
    return failing, quarantined
