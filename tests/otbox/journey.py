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
                # otbox 2.0 phase 3: the CI lane (pr | nightly |
                # local-agents). Explicit ci_lane in the TOML wins;
                # otherwise derived (sentinels -> pr, tier 0 -> nightly,
                # tier 1 -> local-agents).
                "ci_lane": _derive_ci_lane(doc),
            }
        )
    return out


def _derive_ci_lane(doc: dict) -> str:
    from .lanes import derive_ci_lane

    return derive_ci_lane(doc)


def _sysconfig_purelib() -> str:
    import sysconfig

    return sysconfig.get_paths()["purelib"]


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

    # Plan 085 migration-suite vocabulary. Boolean keys require the
    # checkpoint to advertise the same flag True; ``pre_migration_schema``
    # is an exact-string match against the restored legacy schema version.
    for flag in (
        "legacy_world_restored",
        "migration_applied",
        "no_data_loss",
        "migration_idempotent",
        "otel_captures_present",
    ):
        if preconditions.get(flag):
            if not bool(p.get(flag)):
                return False, f"{flag} is not True"

    want_schema = preconditions.get("pre_migration_schema")
    if want_schema is not None:
        have_schema = p.get("pre_migration_schema")
        if have_schema != want_schema:
            return False, (
                f"pre_migration_schema {have_schema!r} != {want_schema!r}"
            )

    # Issue #42 — bucket-spine-v2 + context-tree boolean world flags. Each
    # requires the hosting checkpoint to advertise the same flag True.
    for flag in (
        "bucket_spine_v2_layout",
        "pushed_to_fake_remote",
        "orphan_blob_injected",
        "dangling_ref_injected",
        "bucket_only_no_git_ref",
        "local_blobs_dropped",
        "lazy_projection_enabled",
        "events_mirror_v1_populated",
        "git_repo_present",
        "post_commit_hook_installed",
    ):
        if preconditions.get(flag):
            if not bool(p.get(flag)):
                return False, f"{flag} is not True"

    # Issue #42 — context-tree integer/list world requirements.
    req_branch_types = preconditions.get("requires_branch_types") or []
    if req_branch_types:
        have_branch_types = set(p.get("branch_types") or [])
        missing = [b for b in req_branch_types if b not in have_branch_types]
        if missing:
            return False, f"missing branch_types: {missing}"

    min_compactions = preconditions.get("requires_compactions_min")
    if min_compactions is not None:
        try:
            need = int(min_compactions)
        except (TypeError, ValueError):
            return False, f"requires_compactions_min is not an int: {min_compactions!r}"
        have = int(p.get("compactions") or 0)
        if have < need:
            return False, f"compactions {have} < {need}"

    min_msg_bytes = preconditions.get("requires_messages_layer_bytes_min")
    if min_msg_bytes is not None:
        try:
            need = int(min_msg_bytes)
        except (TypeError, ValueError):
            return False, (
                f"requires_messages_layer_bytes_min is not an int: {min_msg_bytes!r}"
            )
        have = int(p.get("messages_layer_bytes_max") or 0)
        if have < need:
            return False, f"messages_layer_bytes_max {have} < {need}"

    min_edit_commits = preconditions.get("requires_edit_commits_min")
    if min_edit_commits is not None:
        try:
            need = int(min_edit_commits)
        except (TypeError, ValueError):
            return False, f"requires_edit_commits_min is not an int: {min_edit_commits!r}"
        have = int(p.get("edit_commits") or 0)
        if have < need:
            return False, f"edit_commits {have} < {need}"

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

    # Plan 085: expose the legacy-world checkpoint audits so migration
    # journeys forking from c-legacy-v033 / -upgraded can address the
    # restored 0.3.0 trace via {legacy_trace_id} and the fresh 0.4 capture
    # via {new_trace_id} without re-resolving them from box.notes.
    legacy_audit = box.notes.get("c_legacy_v033_audit") or {}
    if legacy_audit:
        result["legacy_trace_id"] = str(legacy_audit.get("legacy_trace_id") or "")
        result["pre_migration_schema"] = str(legacy_audit.get("pre_migration_schema") or "")
        result["legacy_slug"] = str(legacy_audit.get("legacy_slug") or "")
    upgraded_audit = box.notes.get("c_legacy_v033_upgraded_audit") or {}
    if upgraded_audit:
        result["legacy_trace_id"] = str(
            upgraded_audit.get("legacy_trace_id") or result.get("legacy_trace_id", "")
        )
        result["pre_migration_schema"] = str(
            upgraded_audit.get("pre_migration_schema") or result.get("pre_migration_schema", "")
        )
        result["new_trace_id"] = str(upgraded_audit.get("new_trace_id") or "")
        result["new_commit_sha"] = str(upgraded_audit.get("new_commit_sha") or "")
        result["head_before_capture"] = str(upgraded_audit.get("head_before_capture") or "")
        result["step_index"] = str(
            upgraded_audit.get("edit_step_index") or result.get("step_index", "")
        )

    otel_audit = box.notes.get("c_legacy_v033_otel_audit") or {}
    if otel_audit:
        result["legacy_trace_id"] = str(
            otel_audit.get("legacy_trace_id") or result.get("legacy_trace_id", "")
        )
        result["otel_trace_id"] = str(otel_audit.get("otel_trace_id") or "")
        result["otel_session_id"] = str(otel_audit.get("otel_session_id") or "")

    codex_audit = box.notes.get("c_captured_codex_session_audit") or {}
    if codex_audit:
        result["trace_id"] = str(codex_audit.get("trace_id") or result.get("trace_id", ""))
        result["session_id"] = str(codex_audit.get("session_id") or result.get("session_id", ""))
        result["commit_sha"] = str(codex_audit.get("commit_sha") or result.get("commit_sha", ""))
        result["step_index"] = str(codex_audit.get("step_index") or result.get("step_index", ""))
        result["codex_patch_count"] = str(codex_audit.get("patch_count") or "")
        result["transcript_path"] = str(codex_audit.get("transcript_path") or result.get("transcript_path", ""))

    pi_audit = box.notes.get("c_captured_pi_session_audit") or {}
    if pi_audit:
        result["trace_id"] = str(pi_audit.get("trace_id") or result.get("trace_id", ""))
        result["session_id"] = str(pi_audit.get("session_id") or result.get("session_id", ""))
        result["commit_sha"] = str(pi_audit.get("commit_sha") or result.get("commit_sha", ""))
        result["step_index"] = str(pi_audit.get("step_index") or result.get("step_index", ""))
        result["transcript_path"] = str(pi_audit.get("transcript_path") or result.get("transcript_path", ""))

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

    # Issue #42 — context-tree substrate (c-context-tree-substrate).
    ct_audit = box.notes.get("c_context_tree_substrate_audit") or {}
    if ct_audit:
        result["trace_id"] = str(ct_audit.get("trace_id") or result.get("trace_id", ""))
        result["session_id"] = str(
            ct_audit.get("session_id") or result.get("session_id", "")
        )
        result["step_index"] = str(
            ct_audit.get("step_index") or result.get("step_index", "")
        )
        result["subagent_trace_id"] = str(ct_audit.get("subagent_trace_id") or "")
        result["subagent_session_id"] = str(ct_audit.get("subagent_session_id") or "")
        result["rewound_trace_id"] = str(ct_audit.get("rewound_trace_id") or "")
        result["edit_commits"] = str(ct_audit.get("edit_commits") or "")
        # Resolve the node-id template vars from the live event log at
        # expansion time (snapshot/restore-safe — never bake content ids).
        result.update(
            _resolve_context_tree_node_template_vars(
                box,
                primary_trace_id=result["trace_id"],
                subagent_trace_id=result["subagent_trace_id"],
                rewound_trace_id=result["rewound_trace_id"],
                edit_step_index=ct_audit.get("step_index"),
            )
        )
    return result


def _resolve_context_tree_node_template_vars(
    box: Box,
    *,
    primary_trace_id: str,
    subagent_trace_id: str,
    rewound_trace_id: str,
    edit_step_index,
) -> dict[str, str]:
    """Derive the context-tree node-id template vars from the event log.

    Issue #42 / plan 077. Resolved at template-expansion time (mirrors
    ``_resolve_otel_node_template_vars``) so the substrate's content-
    addressed node ids never go stale across a snapshot/restore cycle.
    Empty strings on miss so a journey referencing an absent var fails
    loudly (the desired TDD signal) rather than silently passing.

    Vars served:
      {first_node_id}            root node of the primary trace
      {first_read_node_id}       node of the first Read tool_result
      {target_node_id}           node at the primary edit step_index
      {expected_node_count}      active-path node count (primary)
      {active_path_length}       active-path length to {target_node_id}
      {active_path_uuid_set}            JSON list of active-path transcript uuids
      {active_path_uuid_set_sorted}     sorted variant (two-way set check)
      {captured_cwd} / {captured_model} runtime_state probe for the prune
      {subagent_session_id_node}        a subagent_fork node id
      {orphan_root_node_id} / {orphan_leaf_node_id}  rewind orphan branch
    """
    import json as _json

    extras: dict[str, str] = {
        "first_node_id": "",
        "first_read_node_id": "",
        "target_node_id": "",
        "expected_node_count": "",
        "active_path_length": "",
        "active_path_uuid_set": "[]",
        "active_path_uuid_set_sorted": "[]",
        "captured_cwd": "",
        "captured_model": "",
        "orphan_root_node_id": "",
        "orphan_leaf_node_id": "",
    }
    project = box.project
    if not (project / ".git").exists() or not primary_trace_id:
        return extras
    try:
        from opentraces.core.trails.event_log import read_events
        events = read_events(project, verify=False)
    except Exception:  # noqa: BLE001
        return extras

    try:
        edit_step = int(edit_step_index) if edit_step_index is not None else None
    except (TypeError, ValueError):
        edit_step = None

    # Primary-trace nodes by step_index. Active path = the linear/root
    # chain (orphan branches excluded). The primary corpus is strictly
    # linear so every primary node is on the active path.
    primary_nodes: list[dict] = []
    rewound_nodes: list[dict] = []
    for e in events:
        if e.event_type != "context_node_observed":
            continue
        p = e.payload or {}
        if e.trace_id == primary_trace_id:
            primary_nodes.append(p)
        elif rewound_trace_id and e.trace_id == rewound_trace_id:
            rewound_nodes.append(p)

    def _by_step(nodes: list[dict]) -> list[dict]:
        return sorted(
            nodes,
            key=lambda n: (
                n.get("step_index") if isinstance(n.get("step_index"), int) else 1 << 30
            ),
        )

    primary_sorted = _by_step(primary_nodes)
    if primary_sorted:
        root = next(
            (n for n in primary_sorted if n.get("branch_type") == "root"),
            primary_sorted[0],
        )
        extras["first_node_id"] = str(root.get("node_id") or "")
        extras["expected_node_count"] = str(len(primary_sorted))
        extras["captured_cwd"] = str(root.get("cwd") or root.get("transcript_cwd") or "")
        # model lives on the runtime_state layer; fall back to a node hint.
        extras["captured_model"] = str(root.get("model") or "")

    # target node = the node at edit_step (the resume/anchor target).
    if edit_step is not None:
        target = next(
            (n for n in primary_sorted if n.get("step_index") == edit_step), None
        )
        if target is not None:
            extras["target_node_id"] = str(target.get("node_id") or "")
            # active path root->target inclusive (linear chain).
            path = [
                n for n in primary_sorted
                if isinstance(n.get("step_index"), int)
                and n["step_index"] <= edit_step
            ]
            extras["active_path_length"] = str(len(path))
            uuids = [
                str(n.get("transcript_uuid"))
                for n in path
                if n.get("transcript_uuid")
            ]
            extras["active_path_uuid_set"] = _json.dumps(uuids)
            extras["active_path_uuid_set_sorted"] = _json.dumps(sorted(uuids))

    # first Read node — the first non-root node carrying a read; the
    # primary corpus reads on step 2.
    read_node = next(
        (
            n for n in primary_sorted
            if isinstance(n.get("step_index"), int) and n["step_index"] >= 2
        ),
        None,
    )
    if read_node is not None:
        extras["first_read_node_id"] = str(read_node.get("node_id") or "")

    # rewind orphan branch nodes.
    orphans = [n for n in rewound_nodes if n.get("branch_type") == "rewind_branch"]
    orphans = _by_step(orphans)
    if orphans:
        extras["orphan_root_node_id"] = str(orphans[0].get("node_id") or "")
        extras["orphan_leaf_node_id"] = str(orphans[-1].get("node_id") or "")

    return extras


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
        # python version of the interpreter running the tests, for journeys
        # that touch venv lib paths (e.g. host-venv.pth). Hardcoding
        # python3.14 broke on CI's python3.12.
        "py_tag": f"python{sys.version_info.major}.{sys.version_info.minor}",
        "host_site_packages": _sysconfig_purelib(),
        # absolute path to the interpreter running the tests; journeys that
        # need to run a python script must use this, NOT {repo_root}/.venv
        # (no .venv exists on CI — deps live in the setup-python).
        "py_bin": sys.executable,
    }
    ctx.update(_captured_session(box))
    # live_hf lane: expose the ephemeral private repo ids provisioned for this
    # box so journey TOMLs can reference {live_bucket_repo}/{live_dataset_repo}.
    # Absent on the fake lane (registry returns None), leaving placeholders
    # un-expanded — which is fine, fake-lane journeys never reference them.
    try:
        from .live_hf import get_live_repos

        live = get_live_repos(box.box_id)
        if live is not None:
            ctx["live_bucket_repo"] = live.bucket_repo
            ctx["live_dataset_repo"] = live.dataset_repo
    except Exception:  # noqa: BLE001 - never let live wiring break the fake lane
        pass
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
    if shutil.which("termctrl"):
        caps.add("termctrl")
    if os.environ.get("OT_OTBOX_TIER1") == "1":
        caps.add("tier1")
    if os.environ.get("OT_REAL_REPL") == "1":
        caps.add("real_repl")
    # live_hf: opt-in lane that talks to real huggingface.co (private bucket +
    # dataset repos). Requires the gate env, a resolvable token, and the
    # huggingface_hub import — absent any of these, live journeys SKIP.
    if os.environ.get("OT_OTBOX_LIVE_HF") == "1" and (
        os.environ.get("OPENTRACES_LIVE_HF_TOKEN") or os.environ.get("HF_TOKEN")
    ):
        try:
            import huggingface_hub  # noqa: F401
            caps.add("live_hf")
        except ImportError:
            pass
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
    *,
    live_hf: bool = False,
) -> StepResult:
    step = _expand(raw, ctx)
    step_type = step.get("type", "cli")
    step_id = str(step.get("id", f"{step_type}-{index}"))
    expect_rc = int(step.get("expect_returncode", 0))
    timeout = step.get("timeout")
    timeout = float(timeout) if timeout is not None else None

    if step_type in ("cli", "shell"):
        argv = [*driver.cli_argv(box), *step["argv"]] if step_type == "cli" else list(step["argv"])
        result = driver.exec(box, argv, env_extra=step.get("env"), timeout=timeout, live_hf=live_hf)
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
        proc = driver.popen(box, argv, env_extra=step.get("env"), live_hf=live_hf)
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
            mode=str(step.get("mode") or "interactive"),
            scenario=str(step.get("scenario") or step_id),
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


# --------------------------------------------------------------------------
# assertion kinds — single registry; the registry IS the dispatch (otbox 2.0
# phase 1). Adding a kind means adding one entry here; the catalogue lint
# reads this same dict, so lint-known and runtime-known cannot diverge.
# --------------------------------------------------------------------------
_NO_EXPECTED = object()


def _brief(value: Any, limit: int = 120) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _resolve_expected(spec: dict, steps: list[StepResult], ctx: dict) -> Any:
    """Resolve the expected value for an assertion.

    Precedence: explicit ``equals`` wins; otherwise ``equals_var`` resolves
    either cross-step (``"step-id:json.path"`` digs another step's stdout
    JSON) or from the journey ctx (checkpoint audit / template variables,
    JSON-decoded when possible). An unresolvable variable raises so the
    assertion FAILS — it must never silently pass.
    """
    if "equals" in spec:
        return spec["equals"]
    if "equals_var" not in spec:
        return _NO_EXPECTED
    ref = str(spec["equals_var"])
    step_ref, sep, json_path = ref.partition(":")
    if sep and any(s.step_id == step_ref for s in steps):
        step = _step_by_ref(steps, step_ref)
        payload = _extract_json(step.result.stdout)
        return _dig(payload, json_path)
    if ref in ctx:
        val = ctx[ref]
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (ValueError, TypeError):
                return val
        return val
    raise JourneyError(f"unresolved equals_var {ref!r} (no ctx variable, no step id)")


def _require_expected(spec: dict, steps: list[StepResult], ctx: dict) -> Any:
    expected = _resolve_expected(spec, steps, ctx)
    if expected is _NO_EXPECTED:
        raise JourneyError(f"{spec.get('kind')} requires equals or equals_var")
    return expected


def _k_returncode(spec, steps, ctx, driver, box):
    step = _step_by_ref(steps, spec.get("step"))
    actual = step.result.returncode if step.result else None
    want = int(spec["equals"])
    return actual == want, f"rc={actual} expected {want}"


def _k_stream_contains(spec, steps, ctx, driver, box):
    step = _step_by_ref(steps, spec.get("step"))
    stream = step.result.stdout if spec["kind"].startswith("stdout") else step.result.stderr
    needle = str(spec["value"])
    return needle in stream, f"{'found' if needle in stream else 'missing'}: {needle!r}"


def _k_stream_not_contains(spec, steps, ctx, driver, box):
    step = _step_by_ref(steps, spec.get("step"))
    stream = step.result.stdout if spec["kind"].startswith("stdout") else step.result.stderr
    needle = str(spec["value"])
    absent = needle not in stream
    return absent, (
        f"absent as expected: {needle!r}" if absent else f"unexpectedly present: {needle!r}"
    )


def _k_stdout_json(spec, steps, ctx, driver, box):
    step = _step_by_ref(steps, spec.get("step"))
    payload = _extract_json(step.result.stdout)
    actual = _dig(payload, spec["path"]) if "path" in spec else payload
    expected = _resolve_expected(spec, steps, ctx)
    if expected is not _NO_EXPECTED:
        return actual == expected, (
            f"{spec.get('path', '<root>')}={_brief(actual)} expected {_brief(expected)}"
        )
    return actual is not None, f"{spec.get('path', '<root>')}={_brief(actual)}"


def _k_stdout_json_equals_var(spec, steps, ctx, driver, box):
    if "equals_var" not in spec:
        raise JourneyError("stdout_json_equals_var requires equals_var")
    step = _step_by_ref(steps, spec.get("step"))
    payload = _extract_json(step.result.stdout)
    actual = _dig(payload, spec["path"])
    expected = _resolve_expected(spec, steps, ctx)
    return actual == expected, (
        f"{spec['path']}={_brief(actual)} expected {_brief(expected)}"
        f" (from {spec['equals_var']!r})"
    )


def _k_stdout_json_array_equals(spec, steps, ctx, driver, box):
    # Element-wise equality on a JSON array. Order-sensitive.
    step = _step_by_ref(steps, spec.get("step"))
    payload = _extract_json(step.result.stdout)
    path = spec["path"]
    try:
        actual = _dig(payload, path)
    except (KeyError, IndexError, ValueError):
        return False, f"path not found: {path!r}"
    expected = _require_expected(spec, steps, ctx)
    if not isinstance(actual, list):
        return False, f"{path!r} is {type(actual).__name__}, expected list"
    if not isinstance(expected, list):
        return False, f"expected value must be a list, got {type(expected).__name__}"
    if len(actual) != len(expected):
        return False, f"{path!r} length {len(actual)} != expected {len(expected)}"
    for i, (a, e) in enumerate(zip(actual, expected)):
        if a != e:
            return False, f"{path}[{i}]={a!r} expected {e!r}"
    return True, f"{path!r} array equals expected ({len(actual)} elements)"


def _k_stdout_json_set_contains(spec, steps, ctx, driver, box):
    # Subset check on a JSON array. Order-insensitive.
    step = _step_by_ref(steps, spec.get("step"))
    payload = _extract_json(step.result.stdout)
    path = spec["path"]
    try:
        actual = _dig(payload, path)
    except (KeyError, IndexError, ValueError):
        return False, f"path not found: {path!r}"
    expected = _require_expected(spec, steps, ctx)
    if not isinstance(actual, list):
        return False, f"{path!r} is {type(actual).__name__}, expected list"
    if not isinstance(expected, list):
        return False, f"expected value must be a list, got {type(expected).__name__}"
    try:
        actual_set = set(actual)
        expected_set = set(expected)
    except TypeError as exc:
        return False, f"{path!r} contains unhashable elements: {exc}"
    missing = expected_set - actual_set
    if missing:
        return False, f"{path!r} missing elements: {sorted(missing)!r}"
    return True, f"{path!r} contains all {len(expected_set)} expected elements"


def _k_stdout_json_length_equals(spec, steps, ctx, driver, box):
    # Length check on a JSON list or dict.
    step = _step_by_ref(steps, spec.get("step"))
    payload = _extract_json(step.result.stdout)
    path = spec["path"]
    try:
        actual = _dig(payload, path)
    except (KeyError, IndexError, ValueError):
        return False, f"path not found: {path!r}"
    if not isinstance(actual, (list, dict)):
        return False, f"{path!r} is {type(actual).__name__}, expected list or dict"
    want = int(_require_expected(spec, steps, ctx))
    have = len(actual)
    return have == want, f"len({path!r})={have} expected {want}"


def _k_stdout_json_path_absent(spec, steps, ctx, driver, box):
    step = _step_by_ref(steps, spec.get("step"))
    payload = _extract_json(step.result.stdout)
    path = spec["path"]
    try:
        val = _dig(payload, path)
    except (KeyError, IndexError, ValueError):
        return True, f"path absent as expected: {path!r}"
    return False, f"path unexpectedly present: {path!r}={_brief(val)}"


def _k_stdout_json_greater_equal(spec, steps, ctx, driver, box):
    step = _step_by_ref(steps, spec.get("step"))
    payload = _extract_json(step.result.stdout)
    actual = _dig(payload, spec["path"])
    expected = _resolve_expected(spec, steps, ctx)
    if expected is _NO_EXPECTED:
        expected = spec["min"]
    want = float(expected)
    have = float(actual)
    return have >= want, f"{spec['path']}={have} expected >= {want}"


def _k_stdout_json_contains(spec, steps, ctx, driver, box):
    # Substring check on the value at a JSON path.
    step = _step_by_ref(steps, spec.get("step"))
    payload = _extract_json(step.result.stdout)
    actual = _dig(payload, spec["path"])
    needle = str(spec["value"])
    hay = actual if isinstance(actual, str) else json.dumps(actual)
    return needle in hay, (
        f"{'found' if needle in hay else 'missing'}: {needle!r} in {spec['path']}"
    )


def _k_duration_ms_max(spec, steps, ctx, driver, box):
    # Per-step wall-clock budget (milliseconds). NF-lite: uses the runner's
    # own duration_s measurement, outside the code under test.
    step = _step_by_ref(steps, spec.get("step"))
    have_ms = (step.result.duration_s if step.result else 0.0) * 1000.0
    want_ms = float(spec["max"])
    return have_ms <= want_ms, f"duration {have_ms:.0f}ms budget {want_ms:.0f}ms"


def _k_path_exists(spec, steps, ctx, driver, box):
    path = str(spec["path"])
    if driver is not None and box is not None:
        exists = driver.path_exists(box, path)
    else:
        exists = Path(path).exists()
    return exists, f"{'exists' if exists else 'missing'}: {path}"


def _k_path_not_exists(spec, steps, ctx, driver, box):
    path = str(spec["path"])
    if driver is not None and box is not None:
        exists = driver.path_exists(box, path)
    else:
        exists = Path(path).exists()
    return not exists, (
        f"unexpectedly exists: {path}" if exists else f"absent as expected: {path}"
    )


def _k_file_count_min(spec, steps, ctx, driver, box):
    root = str(spec["path"])
    pattern = spec.get("glob", "**/*")
    want = int(spec["min"])
    if driver is not None and box is not None:
        count = driver.count_files(box, root, pattern)
    else:
        p = Path(root)
        count = sum(1 for f in p.glob(pattern) if f.is_file()) if p.exists() else 0
    return count >= want, f"{count} file(s) under {root}, need >= {want}"


ASSERTION_KINDS: dict[str, Any] = {
    "returncode": _k_returncode,
    "stdout_contains": _k_stream_contains,
    "stderr_contains": _k_stream_contains,
    "stdout_not_contains": _k_stream_not_contains,
    "stderr_not_contains": _k_stream_not_contains,
    "stdout_json": _k_stdout_json,
    "stdout_json_equals_var": _k_stdout_json_equals_var,
    "stdout_json_array_equals": _k_stdout_json_array_equals,
    "stdout_json_set_contains": _k_stdout_json_set_contains,
    "stdout_json_length_equals": _k_stdout_json_length_equals,
    "stdout_json_path_absent": _k_stdout_json_path_absent,
    "stdout_json_greater_equal": _k_stdout_json_greater_equal,
    "stdout_json_contains": _k_stdout_json_contains,
    "duration_ms_max": _k_duration_ms_max,
    "path_exists": _k_path_exists,
    "path_not_exists": _k_path_not_exists,
    "file_count_min": _k_file_count_min,
}


def _eval_assertion(index: int, raw: dict, steps: list[StepResult], ctx: dict,
                    driver: Driver | None = None, box: Box | None = None) -> AssertionResult:
    spec = _expand(raw, ctx)
    kind = spec.get("kind", "")

    def make(ok: bool, message: str) -> AssertionResult:
        return AssertionResult(index, kind, ok, message, spec)

    evaluator = ASSERTION_KINDS.get(kind)
    if evaluator is None:
        return make(False, f"unknown assertion kind {kind!r}")
    try:
        ok, message = evaluator(spec, steps, ctx, driver, box)
        return make(ok, message)
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

    # Runtime precondition probes (otbox 2.0 phase 4): re-verify declared
    # preconditions against the LIVE box, not the checkpoint's static
    # provides list. A failed probe is an ERROR — never SKIP (hides),
    # never PASS (lies). This is what killed the survival-walk tautology.
    if preconditions:
        from .probes import run_probes

        for key, ok, message in run_probes(driver, box, preconditions):
            if not ok:
                result.verdict = "ERROR"
                result.reason = f"precondition_unmet: {key}: {message}"
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

    # live_hf journeys flip the HF seams (real token, no fake remote) for every
    # CLI/service step in this run. Fake-lane journeys stay fully offline.
    live_hf = "live_hf" in requires

    port = free_port()
    ctx = _context(driver, box, port)
    services: dict[str, subprocess.Popen] = {}
    failed_step = False
    try:
        for index, raw in enumerate(raw_steps):
            step_result = _run_step(driver, box, index, raw, ctx, services, live_hf=live_hf)
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
