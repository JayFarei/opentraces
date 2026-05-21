"""Journey runner — declarative scenario docs (spec M3 / R3, R4, R8).

A *journey* is a TOML scenario document describing one user journey
across an opentraces product surface. The runner is generic: adding
coverage means adding a ``.toml`` file under ``catalogue/journeys/``,
not editing this module.

The schema extends the plan-045 release-UAT scenario format
(``name`` / ``description`` / ``lane`` / ``requires`` / ``[[steps]]`` /
``[[assertions]]``) with two otbox additions: ``tier`` (0 = local/docker,
1 = remote lease) and ``seed`` (which seed scenario the journey expects).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .drivers.base import Driver, ExecResult
from .env import REPO_ROOT, Box

CATALOGUE_DIR = Path(__file__).resolve().parent / "catalogue" / "journeys"

# Reuse the e2e smoke helpers' network primitives rather than duplicating.
if str(REPO_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tests"))
from e2e._smoke_helpers import free_port, wait_for_http  # noqa: E402


class JourneyError(Exception):
    pass


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------
@dataclass
class StepResult:
    index: int
    step_id: str
    type: str
    detail: dict
    result: ExecResult | None
    ok: bool
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "step_id": self.step_id,
            "type": self.type,
            "detail": self.detail,
            "ok": self.ok,
            "message": self.message,
            "result": self.result.to_dict() if self.result else None,
        }


@dataclass
class AssertionResult:
    index: int
    kind: str
    ok: bool
    message: str
    spec: dict

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "kind": self.kind,
            "ok": self.ok,
            "message": self.message,
            "spec": self.spec,
        }


@dataclass
class JourneyResult:
    name: str
    description: str
    lane: str
    tier: int
    seed: str | None
    box_id: str
    verdict: str  # PASS | FAIL | SKIP
    reason: str = ""
    steps: list[StepResult] = field(default_factory=list)
    assertions: list[AssertionResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "lane": self.lane,
            "tier": self.tier,
            "seed": self.seed,
            "box_id": self.box_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "steps": [s.to_dict() for s in self.steps],
            "assertions": [a.to_dict() for a in self.assertions],
        }


# --------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------
def journey_path(name: str) -> Path:
    path = CATALOGUE_DIR / f"{name}.toml"
    if not path.exists():
        raise JourneyError(f"no journey {name!r} in catalogue ({CATALOGUE_DIR})")
    return path


def available_journeys() -> list[dict]:
    out: list[dict] = []
    if not CATALOGUE_DIR.exists():
        return out
    for path in sorted(CATALOGUE_DIR.glob("*.toml")):
        doc = tomllib.loads(path.read_text())
        out.append(
            {
                "name": doc.get("name", path.stem),
                "description": doc.get("description", "").strip(),
                "lane": doc.get("lane", "core"),
                "tier": int(doc.get("tier", 0)),
                "seed": doc.get("seed"),
                # Plan 062: journeys declare their starting checkpoint(s).
                # A list because one journey can be run from N bases.
                "from_checkpoints": list(doc.get("from_checkpoints", [])),
                "persona": doc.get("persona"),
                "requires": list(doc.get("requires", [])),
                # Plan 069 R1/R4: declarative preconditions + coverage
                # tier label. Both are optional; defaults preserve
                # today's behaviour.
                "preconditions": dict(doc.get("preconditions") or {}),
                "tier_label": str(doc.get("tier_label", "bronze")),
            }
        )
    return out


# --------------------------------------------------------------------------
# precondition resolver (plan 069 R2)
# --------------------------------------------------------------------------
_TIER_LABELS = ("bronze", "silver", "gold")
_TIER_RANK = {label: rank for rank, label in enumerate(_TIER_LABELS)}


def normalize_tier_label(label: str | None) -> str:
    """Coerce ``label`` to a known tier, defaulting to ``bronze``."""
    if not label:
        return "bronze"
    normalized = str(label).strip().lower()
    return normalized if normalized in _TIER_RANK else "bronze"


def max_tier(a: str, b: str) -> str:
    """Return the higher-ranked of two tier labels."""
    return a if _TIER_RANK[normalize_tier_label(a)] >= _TIER_RANK[normalize_tier_label(b)] else b


def _checkpoint_satisfies(
    provides: dict | None,
    preconditions: dict,
) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for whether ``provides`` meets every key
    in ``preconditions``. Empty preconditions are trivially satisfied.

    Match rules (plan 069 R1):
      * ``min_captured_traces: int`` — provides[``captured_traces``] >= N
      * ``requires_survival_states: list[str]`` — every requested state
        must appear in provides[``survival_states``]
      * ``requires_skills: list[str]`` — every requested skill must
        appear in provides[``skills``]
      * ``requires_branch_commits_min: int`` —
        provides[``branch_commits``] >= N
      * ``requires_security_findings: bool`` —
        provides[``has_security_findings``] == True (when requested)
    """
    if not preconditions:
        return True, ""
    p = provides or {}

    min_traces = preconditions.get("min_captured_traces")
    if min_traces is not None:
        try:
            need = int(min_traces)
        except (TypeError, ValueError):
            return False, f"min_captured_traces is not an int: {min_traces!r}"
        have = int(p.get("captured_traces") or 0)
        if have < need:
            return False, f"captured_traces {have} < {need}"

    req_states = preconditions.get("requires_survival_states") or []
    if req_states:
        have_states = set(p.get("survival_states") or [])
        missing = [s for s in req_states if s not in have_states]
        if missing:
            return False, f"missing survival_states: {missing}"

    req_skills = preconditions.get("requires_skills") or []
    if req_skills:
        have_skills = set(p.get("skills") or [])
        missing = [s for s in req_skills if s not in have_skills]
        if missing:
            return False, f"missing skills: {missing}"

    min_branch = preconditions.get("requires_branch_commits_min")
    if min_branch is not None:
        try:
            need = int(min_branch)
        except (TypeError, ValueError):
            return False, f"requires_branch_commits_min is not an int: {min_branch!r}"
        have = int(p.get("branch_commits") or 0)
        if have < need:
            return False, f"branch_commits {have} < {need}"

    if preconditions.get("requires_security_findings"):
        if not bool(p.get("has_security_findings")):
            return False, "has_security_findings is not True"

    # Plan 077 + 078 declarative keys.
    if preconditions.get("context_tree_built"):
        if not bool(p.get("context_tree_built")):
            return False, "context_tree_built is not True"

    if preconditions.get("otel_captures_present"):
        if not bool(p.get("otel_captures_present")):
            return False, "otel_captures_present is not True"

    if preconditions.get("otel_settings_patched"):
        if not bool(p.get("otel_settings_patched")):
            return False, "otel_settings_patched is not True"

    if preconditions.get("otlp_receiver_running"):
        if not bool(p.get("otlp_receiver_running")):
            return False, "otlp_receiver_running is not True"

    if preconditions.get("otel_bypass_active"):
        if not bool(p.get("otel_bypass_active")):
            return False, "otel_bypass_active is not True"

    min_mcp = preconditions.get("mcp_servers_connected")
    if min_mcp is not None:
        try:
            need = int(min_mcp)
        except (TypeError, ValueError):
            return False, f"mcp_servers_connected is not an int: {min_mcp!r}"
        have = int(p.get("mcp_servers_connected") or 0)
        if have < need:
            return False, f"mcp_servers_connected {have} < {need}"

    return True, ""


def resolve_precondition_match(preconditions: dict) -> str | None:
    """Return the name of the first checkpoint (sorted by name) whose
    ``provides`` dict satisfies every key in ``preconditions``.

    Returns ``None`` when no registered checkpoint matches. Empty /
    missing preconditions match nothing here (callers should fall back
    to ``from_checkpoints`` in that case).
    """
    if not preconditions:
        return None
    # Local import — the checkpoint registry imports this module
    # transitively, so deferring keeps import-time cycles harmless.
    from .checkpoints import REGISTRY

    for name in sorted(REGISTRY):
        cp = REGISTRY[name]
        ok, _reason = _checkpoint_satisfies(cp.provides, preconditions)
        if ok:
            return name
    return None


def validate_precondition_pin(
    pinned_name: str,
    preconditions: dict,
) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for whether ``pinned_name`` (a checkpoint
    the journey named via ``from_checkpoints``) satisfies the journey's
    declared preconditions. ``ok=True`` with empty reason means the
    pin is valid (or preconditions are empty)."""
    if not preconditions:
        return True, ""
    from .checkpoints import REGISTRY

    cp = REGISTRY.get(pinned_name)
    if cp is None:
        return False, f"pinned checkpoint {pinned_name!r} is not registered"
    ok, reason = _checkpoint_satisfies(cp.provides, preconditions)
    if ok:
        return True, ""
    return False, (
        f"pinned checkpoint {pinned_name!r} does not satisfy declared "
        f"preconditions: {reason}"
    )


# --------------------------------------------------------------------------
# templating
# --------------------------------------------------------------------------
def _state_dir(driver: Driver, box: Box) -> str:
    paths = driver.paths(box)
    dirs = driver.glob(box, f"{paths['opentraces_dir']}/projects/*")
    return dirs[0] if len(dirs) == 1 else ""


def _captured_session(box: Box) -> dict[str, str]:
    """Expose the audit produced by the c-captured-real-session
    checkpoint (plan 064) as journey templating variables.

    The checkpoint records the minted trace_id + commit_sha + step
    index in ``box.notes["c_captured_session_audit"]`` so happy-path
    journeys forked from c-captured-real-session can address the
    captured trace via ``{trace_id}`` / ``{commit_sha}`` / ``{step_index}``
    in their TOML — no per-journey wiring required.

    Returns empty strings (not ``None``) for the keys when the audit
    is absent, so journeys NOT forking from this checkpoint still
    render their TOML cleanly (the placeholder template just expands
    to the empty string instead of raising).
    """
    audit = box.notes.get("c_captured_session_audit") or {}
    result = {
        "trace_id": str(audit.get("trace_id") or ""),
        "session_id": str(audit.get("session_id") or ""),
        "commit_sha": str(audit.get("commit_sha") or ""),
        "step_index": str(audit.get("edit_step_index") or ""),
        "transcript_path": str(audit.get("transcript_path") or ""),
    }
    # Plan 070 R1: expose the pr-branch audit fields to journey
    # templating so PR-blame happy-path journeys can address the
    # captured branch via ``{branch_name}`` / ``{base_commit_sha}`` /
    # ``{head_commit_sha}`` / ``{branch_commit_count}`` without each
    # journey re-resolving them from box.notes. Empty strings when the
    # audit is absent so journeys NOT forking from
    # c-captured-with-pr-branch still render their TOML cleanly.
    pr_audit = box.notes.get("c_captured_with_pr_branch_audit") or {}
    result["branch_name"] = str(pr_audit.get("branch_name") or "")
    result["base_commit_sha"] = str(pr_audit.get("base_commit_sha") or "")
    result["head_commit_sha"] = str(pr_audit.get("head_commit_sha") or "")
    result["branch_commit_count"] = str(pr_audit.get("branch_commit_count") or 0)

    codex_audit = box.notes.get("c_captured_codex_session_audit") or {}
    if codex_audit:
        result["trace_id"] = str(codex_audit.get("trace_id") or result.get("trace_id", ""))
        result["session_id"] = str(codex_audit.get("session_id") or result.get("session_id", ""))
        result["commit_sha"] = str(codex_audit.get("commit_sha") or result.get("commit_sha", ""))
        result["step_index"] = str(codex_audit.get("step_index") or result.get("step_index", ""))
        result["transcript_path"] = str(codex_audit.get("transcript_path") or result.get("transcript_path", ""))

    # Plan 078: expose OTel checkpoint audit fields. Overrides plan-064
    # values when the journey forks from an OTel checkpoint because the
    # OTel audits also pin a session/trace id under the same key names.
    otel_linear_audit = box.notes.get("c_context_tree_otel_linear_audit") or {}
    if otel_linear_audit:
        result["session_id"] = str(otel_linear_audit.get("session_id") or result.get("session_id", ""))
        result["trace_id"] = str(otel_linear_audit.get("trace_id") or result.get("trace_id", ""))
        result["request_id_with_body"] = str(otel_linear_audit.get("request_id_with_body") or "")
        result["prompts_total"] = str(otel_linear_audit.get("prompts_total") or "")
        result["prompts_with_body"] = str(otel_linear_audit.get("prompts_with_body") or "")
        # Plan 078 bypass-mode template vars: the linear checkpoint stages
        # two traces — the second simulates "pre-bypass" and "post-restart".
        result["trace_id_bypassed"] = "otel-linear-trace-0001"
        result["trace_id_post_restart"] = "otel-linear-trace-0002"
        # Always re-derive box-relative paths from the CURRENT box so
        # snapshot/restore across box ids doesn't surface stale absolutes.
        session_id = result["session_id"]
        if session_id:
            result["snapshot_path"] = str(
                box.home / ".opentraces" / "staging" / "otel" / f"{session_id}.json"
            )
        else:
            result["snapshot_path"] = ""
        result["project_dir"] = str(box.project)
        result["source_jsonl"] = str(box.home / "_otel-linear-source.jsonl")
        # Derive first_otel_node_id from the actual project event log.
        # Plan 078 + OG's TOML contract: journeys address the first OTel
        # node via this template var. We resolve at expansion time so the
        # checkpoint's snapshot/restore cycle doesn't bake stale ids in.
        result.update(_resolve_otel_node_template_vars(box, result["trace_id"]))
    otel_mcp_audit = box.notes.get("c_context_tree_otel_with_mcp_audit") or {}
    if otel_mcp_audit:
        # When the with-mcp checkpoint is the journey's base, override the
        # trace/session ids so templating addresses the with-mcp trace
        # (which has the mcp_server_connection events), not the parent's.
        if otel_mcp_audit.get("trace_id"):
            result["trace_id"] = str(otel_mcp_audit["trace_id"])
        if otel_mcp_audit.get("session_id"):
            result["session_id"] = str(otel_mcp_audit["session_id"])
        result["mcp_server_name"] = str(otel_mcp_audit.get("mcp_server_name") or "")
        result["mcp_servers_connected"] = str(otel_mcp_audit.get("mcp_servers_connected") or 0)
        # Plugin/MCP/hook lifecycle journeys address the first item via
        # template vars; pull the canonical names from the snapshot.
        result["first_plugin_name"] = "test-plugin"
        result["first_hook_event"] = "PreToolUse"
        # Re-resolve OTel node id under the with-mcp trace.
        result.update(_resolve_otel_node_template_vars(box, result["trace_id"]))
    return result


def _resolve_otel_node_template_vars(box: Box, trace_id: str) -> dict[str, str]:
    """Look up first OTel + first JSONL node ids from the project's event log.

    Resolved at template expansion time so snapshot/restore cycles don't
    bake stale sha256 ids into the templating context. Returns empty
    strings when no events are found (journey assertions referencing the
    vars then fail loudly, which is the desired TDD signal).
    """
    extras: dict[str, str] = {
        "first_otel_node_id": "",
        "first_jsonl_node_id": "",
        "first_jsonl_messages_content_hash": "",
        "otel_receiver_port": "",
    }
    project = box.project
    if not (project / ".git").exists() or not trace_id:
        return extras
    try:
        from opentraces.core.trails.event_log import read_events
        events = read_events(project, verify=False)
    except Exception:
        return extras
    # Collect node_ids first, then derive messages_layer_id from the
    # node's payload for the equivalence assertion's template var.
    jsonl_node_payload: dict | None = None
    for ev in events:
        if ev.event_type == "context_node_observed" and ev.trace_id == trace_id:
            cm = ev.capture_method or []
            payload = ev.payload or {}
            nid = payload.get("node_id")
            if payload.get("step_index") not in (0, "0"):
                continue
            if "otel" in cm and not extras["first_otel_node_id"]:
                extras["first_otel_node_id"] = nid or ""
            if "transcript_reconstruction" in cm and not extras["first_jsonl_node_id"]:
                extras["first_jsonl_node_id"] = nid or ""
                jsonl_node_payload = payload
    if jsonl_node_payload is not None:
        extras["first_jsonl_messages_content_hash"] = str(
            jsonl_node_payload.get("messages_layer_id") or ""
        )
    return extras


def _context(driver: Driver, box: Box, port: int) -> dict[str, str]:
    paths = driver.paths(box)
    ctx = {
        "project": paths["project"],
        "home": paths["home"],
        "fake_remote": paths["fake_remote"],
        "box_root": paths["root"],
        "box_id": box.box_id,
        "state_dir": _state_dir(driver, box),
        "opentraces_dir": paths["opentraces_dir"],
        "repo_root": str(REPO_ROOT),
        "port": str(port),
    }
    ctx.update(_captured_session(box))
    return ctx


def _expand(value: Any, ctx: dict[str, str]) -> Any:
    if isinstance(value, str):
        try:
            return value.format(**ctx)
        except (KeyError, IndexError):
            return value
    if isinstance(value, list):
        return [_expand(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, ctx) for k, v in value.items()}
    return value


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------
def _capabilities(driver: Driver, box: Box) -> set[str]:
    """Capabilities available to journeys on this driver/box.

    Tier 1 capabilities (``tier1``, ``real_repl``) are only present when
    explicitly opted in, so Tier 1 journeys SKIP — never FAIL — in
    default CI.
    """
    import os
    import shutil

    caps = {"cli"}
    if driver.exec(box, ["git", "--version"]).ok:
        caps.add("git")
    if shutil.which("tmux"):
        caps.add("tmux")
    if os.environ.get("OT_OTBOX_TIER1") == "1":
        caps.add("tier1")
    if os.environ.get("OT_REAL_REPL") == "1":
        caps.add("real_repl")
    return caps


def _argv_for(step: dict, driver: Driver, box: Box) -> list[str]:
    """Resolve a step's argv, prefixing the real CLI when kind == 'cli'."""
    kind = step.get("kind", "cli")
    if kind == "cli":
        return [*driver.cli_argv(box), *step["argv"]]
    return list(step["argv"])


def _run_step(
    driver: Driver,
    box: Box,
    index: int,
    raw: dict,
    ctx: dict,
    services: dict[str, subprocess.Popen],
) -> StepResult:
    step = _expand(raw, ctx)
    step_type = step.get("type", "cli")
    step_id = str(step.get("id", f"{step_type}-{index}"))
    expect_rc = int(step.get("expect_returncode", 0))
    timeout = step.get("timeout")
    timeout = float(timeout) if timeout is not None else None

    if step_type in ("cli", "shell"):
        argv = [*driver.cli_argv(box), *step["argv"]] if step_type == "cli" else list(step["argv"])
        result = driver.exec(box, argv, env_extra=step.get("env"), timeout=timeout)
        ok = result.returncode == expect_rc and not result.timed_out
        msg = (
            ""
            if ok
            else f"expected rc={expect_rc}, got rc={result.returncode}"
            f"{' (timed out)' if result.timed_out else ''}"
        )
        return StepResult(index, step_id, step_type, step, result, ok, msg)

    if step_type == "write_file":
        # Path is interpreted relative to the box's project dir, as the
        # box sees it. Driver-mediated so it works on Tier 0 + Tier 1.
        project = driver.paths(box)["project"]
        target = f"{project}/{step['path']}"
        driver.put_text(box, target, step.get("content", ""))
        return StepResult(index, step_id, step_type, step, None, True, f"wrote {target}")

    if step_type == "sync":
        # Workspace sync — laptop -> remote rsync on Tier 1; no-op on Tier 0.
        result = driver.sync(box, full_resync=bool(step.get("full_resync", False)))
        ok = result.ok
        msg = "" if ok else f"sync failed rc={result.returncode}"
        return StepResult(index, step_id, step_type, step, result, ok, msg)

    if step_type == "service":
        # Long-running background process (e.g. `ot web`). Lifecycle owned
        # by run_journey, which terminates every service at journey end.
        if not hasattr(driver, "popen"):
            return StepResult(
                index, step_id, step_type, step, None, False,
                f"driver {driver.name!r} does not support background services",
            )
        argv = _argv_for(step, driver, box)
        proc = driver.popen(box, argv, env_extra=step.get("env"))
        services[step_id] = proc
        ready_url = step.get("ready_url")
        if ready_url:
            try:
                wait_for_http(ready_url, timeout_s=float(step.get("ready_timeout", 20)))
            except TimeoutError as exc:
                return StepResult(index, step_id, step_type, step, None, False, str(exc))
        return StepResult(
            index, step_id, step_type, step, None, True,
            f"service started (pid={proc.pid}){' — ready' if ready_url else ''}",
        )

    if step_type == "http_get":
        url = step["url"]
        expect_status = int(step.get("expect_status", 200))
        try:
            with urllib.request.urlopen(url, timeout=float(step.get("timeout", 10))) as resp:
                status = resp.status
                body = resp.read(int(step.get("max_bytes", 65536))).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, body = exc.code, ""
        except Exception as exc:  # noqa: BLE001
            return StepResult(index, step_id, step_type, step, None, False, f"GET {url} failed: {exc}")
        synthetic = ExecResult(
            argv=["GET", url], returncode=status, stdout=body, stderr="",
            duration_s=0.0, cwd="", timed_out=False,
        )
        ok = status == expect_status
        return StepResult(
            index, step_id, step_type, step, synthetic, ok,
            "" if ok else f"GET {url} -> {status}, expected {expect_status}",
        )

    if step_type == "tmux":
        # Drive an interactive surface (the TUI) in a tmux session inside
        # the box, let it settle, capture the pane, then kill the session.
        #
        # Isolation note: `tmux new-session` runs the command under the
        # tmux *server's* environment, not this process's — so a running
        # tmux server would leak the developer's real HOME and the TUI
        # would render their real data. We defend by wrapping the command
        # in an explicit `env HOME=... ...` prefix that pins every
        # box-isolating variable regardless of the server environment.
        import shutil as _shutil

        if not _shutil.which("tmux"):
            return StepResult(index, step_id, step_type, step, None, False, "tmux not installed")
        session = f"otbox-{box.box_id}-{step_id}"
        argv = _argv_for(step, driver, box)
        settle = float(step.get("settle", 4))
        from .env import isolated_env
        import os as _os

        full_env = isolated_env(box, step.get("env"))
        # Only the keys that differ from the ambient environment need pinning.
        overrides = {
            k: v for k, v in full_env.items()
            if _os.environ.get(k) != v
        }
        env_prefix = ["env"] + [f"{k}={v}" for k, v in sorted(overrides.items())]
        wrapped = " ".join(_shlex_quote(a) for a in (*env_prefix, *argv))
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, "-x", "200", "-y", "50", wrapped],
            cwd=str(box.project), capture_output=True, text=True,
        )
        import time as _time

        _time.sleep(settle)
        cap = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", session],
            capture_output=True, text=True,
        )
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
        synthetic = ExecResult(
            argv=argv, returncode=cap.returncode, stdout=cap.stdout, stderr=cap.stderr,
            duration_s=settle, cwd=str(box.project), timed_out=False,
        )
        ok = cap.returncode == 0 and bool(cap.stdout.strip())
        return StepResult(
            index, step_id, step_type, step, synthetic, ok,
            "" if ok else "tmux pane capture empty or failed",
        )

    if step_type == "pty_runner":
        # Plan 078 outcome-3 + outcome-4 dispatch: drive a real agent
        # binary through scripted prompts in tmux. Requires OT_REAL_REPL=1
        # (real_repl capability). When the capability is absent the step
        # returns ok=False with a SKIP-shaped message so the journey's
        # capability-gate flips the whole journey to SKIP, not FAIL.
        import os as _os
        if _os.environ.get("OT_REAL_REPL") != "1":
            return StepResult(
                index, step_id, step_type, step, None, False,
                "SKIP: pty_runner requires OT_REAL_REPL=1 (real Claude Code)",
            )
        from pathlib import Path as _Path
        from .simulated_users.runner import Turn, run_simulated_session
        binary = step.get("binary") or step.get("binary_name")
        if not binary:
            return StepResult(
                index, step_id, step_type, step, None, False,
                "pty_runner step needs 'binary' (or 'binary_name')",
            )
        turns_raw = step.get("turns") or []
        turns = [
            Turn(
                prompt=str(t["prompt"]),
                expect_regex=str(t.get("expect_regex", ".*")),
                timeout_s=float(t.get("timeout_s", 60.0)),
            )
            for t in turns_raw
        ]
        save_transcript = step.get("save_transcript")
        output_dir = _Path(save_transcript).parent if save_transcript else (
            _Path(driver.paths(box)["home"]) / ".opentraces" / "pty-transcripts" / step_id
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        scenario_result = run_simulated_session(
            driver, box, binary, turns,
            initial_state_dir=None, output_dir=output_dir,
            env_extra=step.get("env"), agent=step.get("agent"),
        )
        if save_transcript:
            _Path(save_transcript).parent.mkdir(parents=True, exist_ok=True)
            _Path(save_transcript).write_text(
                f"# pty_runner transcript ({scenario_result.verdict})\n\n"
                f"binary: {binary}\n"
                f"binary_version: {scenario_result.binary_version}\n"
                f"turns_completed: {scenario_result.turn_count}\n\n"
                f"## Pane log\n\n```\n{(output_dir / 'pane.log').read_text(encoding='utf-8', errors='replace') if (output_dir / 'pane.log').exists() else '(no pane log)'}\n```\n",
                encoding="utf-8",
            )
        synthetic = ExecResult(
            argv=["pty_runner", binary],
            returncode=0 if scenario_result.verdict == "PASS" else 1,
            stdout=f"verdict={scenario_result.verdict} turns={scenario_result.turn_count}",
            stderr=scenario_result.error_message or "",
            duration_s=0.0, cwd=str(box.project), timed_out=False,
        )
        ok = scenario_result.verdict == "PASS"
        return StepResult(
            index, step_id, step_type, step, synthetic, ok,
            "" if ok else f"pty_runner verdict={scenario_result.verdict}: {scenario_result.error_message}",
        )

    return StepResult(
        index, step_id, step_type, step, None, False, f"unknown step type {step_type!r}"
    )


def _shlex_quote(s: str) -> str:
    import shlex

    return shlex.quote(s)


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


def _step_by_ref(steps: list[StepResult], ref: str | None) -> StepResult:
    if ref is None:
        cli_steps = [s for s in steps if s.result is not None]
        if not cli_steps:
            raise JourneyError("assertion references a step but no command steps ran")
        return cli_steps[-1]
    for s in steps:
        if s.step_id == ref:
            return s
    raise JourneyError(f"assertion references unknown step id {ref!r}")


def _eval_assertion(index: int, raw: dict, steps: list[StepResult], ctx: dict,
                    driver: Driver | None = None, box: Box | None = None) -> AssertionResult:
    spec = _expand(raw, ctx)
    kind = spec.get("kind", "")

    def make(ok: bool, message: str) -> AssertionResult:
        return AssertionResult(index, kind, ok, message, spec)

    try:
        if kind == "returncode":
            step = _step_by_ref(steps, spec.get("step"))
            actual = step.result.returncode if step.result else None
            want = int(spec["equals"])
            return make(actual == want, f"rc={actual} expected {want}")

        if kind in ("stdout_contains", "stderr_contains"):
            step = _step_by_ref(steps, spec.get("step"))
            stream = step.result.stdout if kind == "stdout_contains" else step.result.stderr
            needle = str(spec["value"])
            return make(needle in stream, f"{'found' if needle in stream else 'missing'}: {needle!r}")

        if kind == "stdout_json":
            step = _step_by_ref(steps, spec.get("step"))
            payload = _extract_json(step.result.stdout)
            actual = _dig(payload, spec["path"]) if "path" in spec else payload
            if "equals" in spec:
                return make(actual == spec["equals"], f"{spec.get('path','<root>')}={actual!r} expected {spec['equals']!r}")
            return make(actual is not None, f"{spec.get('path','<root>')}={actual!r}")

        if kind == "stdout_json_array_equals":
            # Element-wise equality on a JSON array. Order-sensitive.
            step = _step_by_ref(steps, spec.get("step"))
            payload = _extract_json(step.result.stdout)
            path = spec["path"]
            try:
                actual = _dig(payload, path)
            except (KeyError, IndexError, ValueError):
                return make(False, f"path not found: {path!r}")
            expected = spec["equals"]
            if not isinstance(actual, list):
                return make(False, f"{path!r} is {type(actual).__name__}, expected list")
            if not isinstance(expected, list):
                return make(False, f"spec.equals must be a list, got {type(expected).__name__}")
            if len(actual) != len(expected):
                return make(
                    False,
                    f"{path!r} length {len(actual)} != expected {len(expected)}",
                )
            for i, (a, e) in enumerate(zip(actual, expected)):
                if a != e:
                    return make(False, f"{path}[{i}]={a!r} expected {e!r}")
            return make(True, f"{path!r} array equals expected ({len(actual)} elements)")

        if kind == "stdout_json_set_contains":
            # Subset check on a JSON array. Order-insensitive.
            step = _step_by_ref(steps, spec.get("step"))
            payload = _extract_json(step.result.stdout)
            path = spec["path"]
            try:
                actual = _dig(payload, path)
            except (KeyError, IndexError, ValueError):
                return make(False, f"path not found: {path!r}")
            expected = spec["equals"]
            if not isinstance(actual, list):
                return make(False, f"{path!r} is {type(actual).__name__}, expected list")
            if not isinstance(expected, list):
                return make(False, f"spec.equals must be a list, got {type(expected).__name__}")
            try:
                actual_set = set(actual)
                expected_set = set(expected)
            except TypeError as exc:
                return make(False, f"{path!r} contains unhashable elements: {exc}")
            missing = expected_set - actual_set
            if missing:
                return make(False, f"{path!r} missing elements: {sorted(missing)!r}")
            return make(
                True,
                f"{path!r} contains all {len(expected_set)} expected elements",
            )

        if kind == "stdout_json_length_equals":
            # Length check on a JSON list or dict.
            step = _step_by_ref(steps, spec.get("step"))
            payload = _extract_json(step.result.stdout)
            path = spec["path"]
            try:
                actual = _dig(payload, path)
            except (KeyError, IndexError, ValueError):
                return make(False, f"path not found: {path!r}")
            if not isinstance(actual, (list, dict)):
                return make(
                    False,
                    f"{path!r} is {type(actual).__name__}, expected list or dict",
                )
            want = int(spec["equals"])
            have = len(actual)
            return make(
                have == want,
                f"len({path!r})={have} expected {want}",
            )

        if kind == "path_exists":
            path = str(spec["path"])
            if driver is not None and box is not None:
                exists = driver.path_exists(box, path)
            else:
                exists = Path(path).exists()
            return make(exists, f"{'exists' if exists else 'missing'}: {path}")

        if kind == "file_count_min":
            root = str(spec["path"])
            pattern = spec.get("glob", "**/*")
            want = int(spec["min"])
            if driver is not None and box is not None:
                count = driver.count_files(box, root, pattern)
            else:
                p = Path(root)
                count = sum(1 for f in p.glob(pattern) if f.is_file()) if p.exists() else 0
            return make(count >= want, f"{count} file(s) under {root}, need >= {want}")

        return make(False, f"unknown assertion kind {kind!r}")
    except Exception as exc:  # noqa: BLE001 - surface assertion eval failures as FAIL
        return make(False, f"assertion error: {exc}")


def _extract_json(text: str) -> Any:
    """Parse JSON from CLI stdout, tolerating a leading sentinel banner."""
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # opentraces emits a ---OPENTRACES_JSON--- sentinel before JSON payloads.
    for marker in ("---OPENTRACES_JSON---",):
        if marker in text:
            tail = text.split(marker, 1)[1].strip()
            try:
                return json.loads(tail)
            except json.JSONDecodeError:
                continue
    # last resort: first balanced object/array on its own lines
    start = stripped.find("{")
    if start == -1:
        start = stripped.find("[")
    if start != -1:
        return json.loads(stripped[start:])
    raise JourneyError("no JSON object found in stdout")


# --------------------------------------------------------------------------
# transcript renderer (plan 077 §"Demo acceptance journey")
# --------------------------------------------------------------------------
_TRANSCRIPT_STDOUT_LIMIT = 4096  # 4KB per the plan
_TRANSCRIPT_TRUNCATION_SUFFIX = "\n... (truncated)"


def _truncate_stdout(text: str, limit: int = _TRANSCRIPT_STDOUT_LIMIT) -> str:
    """Truncate captured stdout to ``limit`` bytes with a visible suffix."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + _TRANSCRIPT_TRUNCATION_SUFFIX


def _assertions_by_step(result: "JourneyResult") -> dict[str, list["AssertionResult"]]:
    """Group assertions by the step they reference.

    Assertions inherit the same defaulting rule as ``_step_by_ref``:
    when ``spec["step"]`` is omitted the assertion targets the last
    command step in the journey. We mirror that here so transcript
    grouping matches assertion-evaluation behaviour.
    """
    cli_steps = [s for s in result.steps if s.result is not None]
    last_step_id = cli_steps[-1].step_id if cli_steps else None
    groups: dict[str, list[AssertionResult]] = {}
    for a in result.assertions:
        ref = a.spec.get("step") if isinstance(a.spec, dict) else None
        target = ref or last_step_id or ""
        groups.setdefault(target, []).append(a)
    return groups


def render_transcript(
    result: "JourneyResult",
    *,
    fixture_name: str = "<unspecified>",
    substrate_versions: dict[str, str] | None = None,
    now_iso: str | None = None,
) -> str:
    """Render a JourneyResult as a markdown transcript artifact.

    Shape per plan 077 §"Demo acceptance journey":

        # Context Tree demo acceptance, run <iso>
        Fixture: <fixture_name>
        Substrate: opentraces <ver>, schema <ver>
        All <N> commands ran successfully. All <M> assertions passed.
        ---
        ## 1. <command argv>
        <captured stdout, truncated to 4KB>
        PASS: <comma-separated list of passing assertions>
        ## Summary
        | step | id | command | assertions | result |
    """
    from datetime import datetime, timezone

    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat()

    versions = substrate_versions or {}
    ot_version = versions.get("opentraces", "unknown")
    schema_version = versions.get("schema", "unknown")

    cli_steps = [s for s in result.steps if s.result is not None]
    n_commands = len(cli_steps)
    m_assertions = len(result.assertions)
    groups = _assertions_by_step(result)

    lines: list[str] = []
    lines.append(f"# Context Tree demo acceptance, run {now_iso}")
    lines.append("")
    lines.append(f"Fixture: {fixture_name}")
    lines.append(f"Substrate: opentraces {ot_version}, schema {schema_version}")
    lines.append(
        f"All {n_commands} commands ran successfully. "
        f"All {m_assertions} assertions passed."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    for human_index, step in enumerate(cli_steps, start=1):
        argv = " ".join(step.result.argv) if step.result and step.result.argv else step.step_id
        lines.append(f"## {human_index}. {argv}")
        lines.append("")
        body = _truncate_stdout(step.result.stdout if step.result else "")
        # Fence the captured stdout so markdown renders it verbatim.
        lines.append("```")
        lines.append(body.rstrip("\n"))
        lines.append("```")
        lines.append("")
        step_asserts = groups.get(step.step_id, [])
        if step_asserts:
            label = "PASS" if all(a.ok for a in step_asserts) else "MIXED"
            kinds = ", ".join(a.kind for a in step_asserts) or "(none)"
            lines.append(f"{label}: {kinds}")
        else:
            lines.append("PASS: (no assertions targeted this step)")
        lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| step | id | command | assertions | result |")
    lines.append("|------|----|---------|------------|--------|")
    for human_index, step in enumerate(cli_steps, start=1):
        argv = " ".join(step.result.argv) if step.result and step.result.argv else step.step_id
        # Escape pipes in the command cell so the markdown table stays valid.
        argv_cell = argv.replace("|", "\\|")
        n_asserts = len(groups.get(step.step_id, []))
        verdict = "PASS" if step.ok and all(a.ok for a in groups.get(step.step_id, [])) else "FAIL"
        lines.append(
            f"| {human_index} | {step.step_id} | {argv_cell} | {n_asserts} | {verdict} |"
        )
    lines.append("")

    return "\n".join(lines)


def save_transcript(
    result: "JourneyResult",
    path: Path,
    *,
    fixture_name: str = "<unspecified>",
    substrate_versions: dict[str, str] | None = None,
) -> Path:
    """Render and write the transcript when the journey fully passed.

    Only writes when ``result.verdict == "PASS"`` and every assertion
    passed; otherwise raises ``JourneyError`` (callers can decide to
    catch + ignore vs propagate). Returns the written path.
    """
    if result.verdict != "PASS":
        raise JourneyError(
            f"refusing to render transcript: journey verdict is {result.verdict!r}"
        )
    if not all(a.ok for a in result.assertions):
        raise JourneyError(
            "refusing to render transcript: one or more assertions failed"
        )
    text = render_transcript(
        result,
        fixture_name=fixture_name,
        substrate_versions=substrate_versions,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def run_journey(driver: Driver, box: Box, name: str) -> JourneyResult:
    doc = tomllib.loads(journey_path(name).read_text())
    j_name = doc.get("name", name)
    description = doc.get("description", "").strip()
    lane = doc.get("lane", "core")
    tier = int(doc.get("tier", 0))
    seed = doc.get("seed")
    requires = set(doc.get("requires", []))
    raw_steps = doc.get("steps", [])
    raw_assertions = doc.get("assertions", [])
    preconditions = dict(doc.get("preconditions") or {})
    from_checkpoints = list(doc.get("from_checkpoints") or [])

    result = JourneyResult(
        name=j_name,
        description=description,
        lane=lane,
        tier=tier,
        seed=seed,
        box_id=box.box_id,
        verdict="PASS",
    )

    # capability gate
    caps = _capabilities(driver, box)
    missing = requires - caps
    if missing:
        result.verdict = "SKIP"
        result.reason = f"missing capabilities: {sorted(missing)}"
        return result

    # Plan 069 R8: when preconditions AND from_checkpoints are both
    # declared, the explicit pin wins but must satisfy the declared
    # preconditions; otherwise SKIP with a clear conflict reason.
    if preconditions and from_checkpoints:
        for pinned in from_checkpoints:
            ok, reason = validate_precondition_pin(pinned, preconditions)
            if not ok:
                result.verdict = "SKIP"
                result.reason = (
                    f"precondition conflict: {reason}"
                )
                return result

    if seed and box.seed and seed != box.seed:
        result.reason = f"note: journey expects seed {seed!r}, box was seeded {box.seed!r}"

    port = free_port()
    ctx = _context(driver, box, port)
    services: dict[str, subprocess.Popen] = {}
    failed_step = False
    try:
        for index, raw in enumerate(raw_steps):
            step_result = _run_step(driver, box, index, raw, ctx, services)
            result.steps.append(step_result)
            # refresh context — state_dir may only resolve after `init` runs
            ctx = _context(driver, box, port)
            if not step_result.ok:
                failed_step = True
                break  # stop on first hard step failure; assertions still reported

        for index, raw in enumerate(raw_assertions):
            result.assertions.append(
                _eval_assertion(index, raw, result.steps, ctx, driver=driver, box=box)
            )
    finally:
        for name, proc in services.items():
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()

    step_ok = not failed_step and all(s.ok for s in result.steps)
    assert_ok = all(a.ok for a in result.assertions)
    if step_ok and assert_ok:
        result.verdict = "PASS"
    else:
        result.verdict = "FAIL"
        bits = []
        if not step_ok:
            bits.append("step failure")
        if not assert_ok:
            bits.append(f"{sum(not a.ok for a in result.assertions)} assertion(s) failed")
        result.reason = "; ".join(bits)
    return result
