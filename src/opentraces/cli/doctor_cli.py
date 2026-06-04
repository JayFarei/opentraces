"""doctor command + render helpers, split out from cli/installers.py.

The ``doctor`` command is registered on ``main`` here via the
``@main.command(...)`` decorator side-effect; the module is loaded by the
``from . import doctor_cli  # noqa: F401`` at the bottom of
``cli/installers.py``.

All code below is moved VERBATIM from cli/installers.py lines 1563-2101.
Zero logic changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from opentraces import cli as _cli
from . import main
from ..core.config import load_config

emit_json = _cli.emit_json
human_echo = _cli.human_echo


@main.command(
    "doctor",
    examples=[
        "opentraces doctor",
        "opentraces doctor --security",
    ],
    see_also=[
        ("opentraces setup", "install or configure a missing integration."),
        ("opentraces status", "project-level snapshot instead of pipeline."),
    ],
)
@click.option(
    "--security", "security_only", is_flag=True,
    help="Show only the security tool subview (versions + enabled tools).",
)
def doctor_cmd(security_only: bool) -> None:
    """Report security pipeline and integration health.

    Probes every configured integration (hooks, scanners, LLM review,
    post-processors) and reports versions, enabled tool state, and any
    actionable failures. Exits non-zero if a required configured tool is broken.
    """
    from ..core import doctor

    cfg = load_config()
    report = doctor.report(cfg, Path.cwd())

    if security_only:
        _render_doctor_security(report)
        # Exit-code signal still needs the full tier data, but trim the
        # JSON payload so piping consumers don't get stuff they asked to
        # hide.
        trimmed = {
            "security_version": report["security_version"],
            "schema_version": report.get("schema_version"),
            "security": report["security"],
        }
        emit_json({"status": "ok", "doctor": trimmed})
    else:
        _render_doctor_human(report)
        emit_json({"status": "ok", "doctor": report})

    code = doctor.exit_code(report)
    if code:
        sys.exit(code)


# Marker glyphs. click.echo strips ANSI on non-TTY and respects NO_COLOR, so
# raw unicode + click.style is safe for both humans and pipes.
_MARK_OK = click.style("✓", fg="green", bold=True)
_MARK_WARN = click.style("⚠", fg="yellow", bold=True)
_MARK_ERR = click.style("✗", fg="red", bold=True)
_MARK_OFF = click.style("·", dim=True)


def _mark_for(kind: str) -> str:
    return {
        "ok": _MARK_OK,
        "warn": _MARK_WARN,
        "err": _MARK_ERR,
        "off": _MARK_OFF,
    }.get(kind, _MARK_OFF)


def _section(title: str) -> None:
    human_echo("")
    human_echo(_cli._bold(title))


def _row(mark_kind: str, label: str, value: str, *, detail: str | None = None) -> None:
    """Fixed-width label column so colons line up across sections."""
    mark = _mark_for(mark_kind)
    padded = f"{label:<16}"
    line = f"  {mark} {padded} {value}"
    if detail:
        line += f"  {_cli._dim(detail)}"
    human_echo(line)


_TIER_STATE_MARK = {
    "enabled": "ok",
    "on-demand": "ok",
    "required": "ok",
    "disabled": "off",
    "not-required": "off",
    "not-initialized": "off",
    "missing": "err",
    "unreachable": "err",
}

_TIER_STATE_LABEL = {
    "enabled": "enabled",
    "on-demand": "on demand",
    "required": "required",
    "disabled": "disabled",
    "not-required": "not required",
    "not-initialized": "no project",
    "missing": "missing binary",
    "unreachable": "unreachable",
}


def _tier_row(tier: dict) -> None:
    """Render one tier: name, state label, detail, and a toggle-hint line."""
    state = tier.get("state", "")
    mark = _mark_for(_TIER_STATE_MARK.get(state, "off"))
    name = tier.get("name", "?")
    value = _TIER_STATE_LABEL.get(state, state)
    detail = tier.get("detail")
    # Fixed name column so state values line up.
    head = f"{name:<22}"
    line = f"  {mark} {head} {value}"
    if detail:
        line += f"  {_cli._dim(detail)}"
    human_echo(line)

    # Setup info lines (e.g., LLM endpoint/env) render above the hints,
    # at the same indent, so `doctor` exposes the full configuration.
    pad = " " * (4 + 22 + 1)  # align under the state column
    for info in _tier_info_lines(tier):
        if info:
            human_echo(f"{pad}{_cli._dim(info)}")

    # Actionable hints, only where applicable.
    hints = _tier_toggle_hint(state, tier)
    if hints:
        if isinstance(hints, str):
            hints = [hints]
        for h in hints:
            if h:
                human_echo(f"{pad}{_cli._dim(h)}")


def _tier_info_lines(tier: dict) -> list[str]:
    """Return read-only setup detail lines for this tier.

    Currently only the LLM trace review tier exposes configuration worth
    surfacing (endpoint, api-key env var, probe result), so other tiers
    return an empty list.
    """
    if tier.get("name") != "LLM trace review":
        return []
    state = tier.get("state")
    # Only show setup details when the user has opted in; the disabled
    # row already carries an "enable:" hint and nothing else is configured.
    if state in ("disabled", None):
        return []
    lines: list[str] = []
    base_url = tier.get("base_url")
    api_format = tier.get("api_format")
    if base_url:
        parts = [f"endpoint: {base_url}"]
        if api_format:
            parts.append(f"api: {api_format}")
        lines.append(" — ".join(parts))
    api_key_env = tier.get("api_key_env")
    if api_key_env:
        import os as _os
        present = bool(_os.environ.get(api_key_env))
        lines.append(
            f"api key env: ${api_key_env} ({'set' if present else 'unset'})"
        )
    probe = tier.get("probe_status")
    # `probe_status` already folds model/backend into the main row for the
    # on-demand case; only surface it when it carries extra signal (model
    # count, unreachable reason, etc.).
    if probe and any(tok in probe for tok in ("models available", "UNREACHABLE", "not found", "not installed", "not set")):
        lines.append(f"probe: {probe}")
    return lines


def _tier_toggle_hint(state: str, tier: dict) -> str | None:
    """Return the command the user can run to flip this tier's state."""
    enable = tier.get("enable_cmd")
    disable = tier.get("disable_cmd")
    # Human review is policy-driven, not an on/off toggle.
    if "review_policy" in tier:
        other = "auto" if tier.get("review_policy") == "review" else "review"
        cmd = disable if other == "auto" else enable
        return f"switch to {other}: {cmd}" if cmd else None
    if state in ("disabled", "not-initialized"):
        return f"enable: {enable}" if enable else None
    if state == "enabled":
        return f"disable: {disable}" if disable else None
    if state == "on-demand":
        return [
            "run: opentraces dataset publish <name> --check-only",
            "upload: opentraces dataset publish <name>",
            f"reconfigure: {enable}" if enable else "",
        ]
    if state == "missing":
        return f"fix: {enable} --enable"
    if state == "unreachable":
        return f"reconfigure: {enable}"
    return None


def _security_section(sec: dict) -> None:
    _section("Security pipeline")
    for tier in sec.get("tiers", []):
        _tier_row(tier)
    sensitivity = sec.get("classifier_sensitivity")
    if sensitivity:
        human_echo("")
        human_echo(f"  {_cli._dim(f'classifier sensitivity: {sensitivity}')}")


def _processors_section(specs: list[dict]) -> None:
    _section("Post-processors")
    if not specs:
        human_echo(f"  {_cli._dim('(none configured)')}")
        return
    for p in specs:
        status = p.get("status")
        kind = "ok" if status == "detected" else "err"
        detail = p.get("resolved_path") or p.get("command")
        _row(kind, p["name"], status or "?", detail=detail)


def _entity_parser_section(info: dict) -> None:
    """Render the entity-parser panel under `opentraces doctor`."""
    _section("Entity parser")
    if not info:
        _row("off", "ot-entities", "not installed",
             detail="run 'opentraces setup entity-parser'")
        return
    if info.get("installed"):
        version = info.get("version") or "installed"
        _row("ok", "ot-entities", version, detail=info.get("binary_path"))
    else:
        _row(
            "off", "ot-entities", "not installed",
            detail=info.get("advice") or "run 'opentraces setup entity-parser'",
        )
    if info.get("platform"):
        _row("ok", "  ↳ platform", info["platform"])


def _hooks_section(hooks: list[dict]) -> None:
    _section("Agent integrations")
    if not hooks:
        human_echo(f"  {_cli._dim('(no installers registered)')}")
        return
    for h in hooks:
        name = h.get("installer", "?")
        if name == "skill":
            _skill_row(h)
        elif name == "claude-code":
            _claude_code_row(h)
        elif name == "codex-cli":
            _codex_cli_row(h)
        elif name == "git":
            _git_row(h)
        else:
            kind = "ok" if h.get("installed") else "off"
            _row(kind, name, "installed" if h.get("installed") else "not installed")


def _post_commit_hook_section(info: dict) -> None:
    """Render post-commit hook runtime status, including Trail anchors."""
    _section("Post-commit hook")
    state = info.get("state") or "missing"
    kind = {
        "ok": "ok",
        "installed_never_ran": "warn",
        "installed_not_chained": "warn",
        "missing": "off",
    }.get(state, "warn")
    _row(kind, "status", state, detail=info.get("log_path"))
    if not info.get("installed"):
        return
    _row(
        "ok" if info.get("chained_in_post_commit") else "warn",
        "chained",
        "yes" if info.get("chained_in_post_commit") else "no",
    )
    last = info.get("last_run") or {}
    if not last:
        return
    sha = last.get("sha")
    if sha:
        _row("ok", "last commit", str(sha)[:12])
    _row("ok", "candidates", str(last.get("candidates") or 0))
    if last.get("notes_written"):
        _row("ok", "notes", "written")
    else:
        _row("off", "notes", "not written", detail=last.get("reason"))
    anchor_error = info.get("last_trail_anchor_error")
    if anchor_error:
        _row("err", "trail anchors", "error", detail=str(anchor_error))
    else:
        count = info.get("last_trail_anchors_created")
        _row("ok", "trail anchors", str(count if count is not None else 0))


def _trace_index_section(info: dict) -> None:
    _section("Trace Index")
    state = info.get("state") or "missing"
    kind = {"ok": "ok", "stale": "warn", "missing": "off", "error": "err"}.get(state, "warn")
    _row(kind, "status", state, detail=info.get("index_path"))
    _row("ok", "traces", str(info.get("trace_count") or 0))
    _row("ok", "units", str(info.get("unit_count") or 0))
    _row("ok", "map nodes", str(info.get("map_node_count") or 0))
    if info.get("legacy_warning"):
        _row("warn", "legacy cache", "ignored", detail="canonical cache is ~/.opentraces/index/index.db")
    if state != "ok":
        _row("warn", "rebuild", info.get("rebuild_advice") or "opentraces trace index rebuild")


def _skill_row(h: dict) -> None:
    installed = h.get("installed")
    iv = h.get("installed_version")
    pv = h.get("package_version")
    broken = h.get("broken_harnesses") or []
    canonical_path = h.get("canonical")
    if not installed:
        _row("off", "skill", "not installed", detail="run 'opentraces setup skill'")
    else:
        kind = "warn" if h.get("drift") or broken else "ok"
        value = iv or "installed"
        # Always surface the canonical path so users see the one global
        # copy their harness symlinks point at.
        detail = canonical_path
        if h.get("drift"):
            detail = f"drift: package is {pv}, run 'opentraces setup skill'"
        _row(kind, "skill", value, detail=detail)
    # Per-harness detail — shows the symlink location in the agent's
    # own skills namespace and what it resolves to, so the global-vs-
    # per-agent split is explicit.
    harnesses = h.get("harnesses") or {}
    for hname, st in harnesses.items():
        symlink_path = st.get("symlink_path") or ""
        if not st.get("present"):
            sub_kind, sub_val = "off", "not linked"
            sub_detail: str | None = symlink_path or None
        elif st.get("canonical"):
            sub_kind, sub_val = "ok", "linked"
            target = st.get("target") or ""
            sub_detail = f"{symlink_path} → {target}" if symlink_path else target
        else:
            sub_kind, sub_val = "warn", "non-canonical dir"
            sub_detail = f"{symlink_path} ({st.get('kind')})" if symlink_path else st.get("kind")
        _row(sub_kind, f"  ↳ {hname}", sub_val, detail=sub_detail)


def _claude_code_row(h: dict) -> None:
    if not h.get("installed"):
        _row("off", "claude-code", "not installed", detail="run 'opentraces setup claude-code'")
        return
    _row("ok", "claude-code", "installed")


def _codex_cli_row(h: dict) -> None:
    if not h.get("installed"):
        _row("off", "codex-cli", "not installed", detail="run 'opentraces setup codex-cli'")
        return
    _row("ok", "codex-cli", "installed")


def _git_row(h: dict) -> None:
    if not h.get("installed"):
        reason = h.get("reason") or "not installed"
        _row("off", "git", reason, detail="run 'opentraces setup git'")
        return
    _row("ok", "git", "post-commit hook active")


def _opted_in_section(info: dict) -> None:
    _section("Opted-in projects")
    count = info.get("count", 0)
    paths = info.get("paths") or []
    if not count:
        human_echo(f"  {_cli._dim('(none — run opentraces init in a project to opt in)')}")
        return
    human_echo(f"  {_cli._dim(f'{count} project(s) registered')}")
    # Show at most 3 to keep doctor compact.
    for p in paths[:3]:
        human_echo(f"    {_cli._dim(p)}")
    if len(paths) > 3:
        human_echo(f"    {_cli._dim(f'... and {len(paths) - 3} more')}")


def _versions_section(report: dict) -> None:
    _section("Versions")
    _row("ok", "security", report["security_version"])
    if report.get("schema_version"):
        _row("ok", "schema", report["schema_version"])


def _tracking_mode_section(report: dict) -> None:
    mode = report.get("tracking_mode", "global")
    _section("Tracking mode")
    if mode == "global":
        _row("ok", "mode", "global", detail="auto-enroll every project an agent touches")
    else:
        _row("off", "mode", "manual", detail="explicit 'opentraces init' opt-in per project")


def _render_doctor_human(report: dict) -> None:
    _cli.print_banner(tagline="doctor")

    _versions_section(report)
    _security_section(report["security"])
    _tracking_mode_section(report)
    _opted_in_section(report.get("opted_in_projects") or {})

    _section("Authentication")
    hf = report.get("hf_auth")
    if hf == "ok":
        _row("ok", "huggingface", "authenticated")
    else:
        _row("err", "huggingface", "missing", detail="run 'hf auth login'")

    _entity_parser_section(report.get("entity_parser") or {})
    _attribution_section(report.get("attribution") or {})
    _watcher_section(report.get("watcher") or {})
    _bucket_section(report.get("bucket") or {})
    _trace_index_section(report.get("trace_index") or {})
    _hooks_section(report["hooks"])
    _post_commit_hook_section(report.get("post_commit_hook") or {})
    _trail_event_log_section(report.get("trail_event_log") or {})
    human_echo("")


def _bucket_section(info: dict) -> None:
    """Render local bucket health for remote-sync readiness."""
    _section("Bucket")
    state = info.get("state") or "missing"
    if state != "ok":
        _row("err", "status", state, detail=info.get("error"))
        return
    trace_records = info.get("trace_records") or {}
    trail = info.get("trail") or {}
    sync = info.get("sync") or {}
    _row("ok", "root", str(info.get("root") or "?"))
    _row("ok", "trace records", str(trace_records.get("object_count") or 0))
    stale_sec = int(trace_records.get("security_stale_count") or 0)
    unfiltered = int(trace_records.get("unfiltered_count") or 0)
    _row("ok" if stale_sec == 0 else "warn", "stale security", str(stale_sec))
    _row("ok" if unfiltered == 0 else "warn", "unfiltered", str(unfiltered))
    trail_stale = int(trail.get("stale_count") or 0)
    _row("ok" if trail_stale == 0 else "warn", "stale trails", str(trail_stale))
    last_sync = trail.get("last_projection_sync_at")
    if last_sync:
        _row("ok", "trail sync", str(last_sync))
    _row(
        "ok" if sync.get("eligible") else "warn",
        "remote eligible",
        "yes" if sync.get("eligible") else "no",
        detail=", ".join(sync.get("blocked_reasons") or []) or None,
    )


def _attribution_section(info: dict) -> None:
    """Render attribution cache panel (plan 043 phase 7)."""
    _section("Attribution cache")
    health = info.get("health")
    if health == "no-project":
        _row("off", "status", "no project",
             detail="run 'opentraces init' in a project to enable attribution")
        return
    _row(
        {"ok": "ok", "empty": "warn", "stale": "warn"}.get(health, "warn"),
        "status",
        health or "?",
        detail=info.get("attribution_cache_dir"),
    )
    _row("ok", "cached commits", str(info.get("cached_commits") or 0))
    last = info.get("last_backfilled_commit")
    if last:
        _row("ok", "last backfill", last[:12],
             detail=info.get("last_backfill_at") or None)
    else:
        _row("off", "last backfill", "never",
             detail="run 'opentraces backfill --project .'")
    decision = info.get("first_run_backfill_decision")
    if decision:
        _row("ok", "first-run", str(decision))


def _watcher_section(info: dict) -> None:
    """Render watcher panel (plan 043 phase 7)."""
    _section("Watcher")
    health = info.get("health")
    plat = info.get("platform") or "?"
    if health == "unsupported-platform":
        _row("off", "platform", plat, detail="watcher unavailable on this platform")
        return
    _row("ok", "platform", plat)
    if not info.get("installed"):
        _row("off", "installed", "no",
             detail="run 'opentraces setup watcher install'")
        return
    _row("ok", "installed", "yes", detail=info.get("unit_path"))
    running_kind = "ok" if info.get("running") else "warn"
    _row(running_kind, "running", "yes" if info.get("running") else "no")
    interval = info.get("interval_seconds")
    if interval:
        _row("ok", "interval", f"{interval}s")
    last = info.get("last_run_at")
    if last:
        _row("ok", "last tick", last)


def _trail_event_log_section(info: dict) -> None:
    """Render Trace Trails event-log integrity."""
    _section("Trace Trails")
    state = info.get("state") or "missing"
    ref = info.get("ref") or "refs/opentraces/local/events/v1"

    if state == "missing":
        _row("off", "event log", "missing", detail=ref)
        return

    kind = "ok" if state == "ok" else "err"
    detail = ref
    head = info.get("head")
    if head:
        detail = f"{ref} @ {head[:12]}"
    _row(kind, "event log", state, detail=detail)
    parents_ok = bool(info.get("batch_parents_linear"))
    hashes_ok = bool(info.get("content_hashes_valid"))
    chain_ok = bool(info.get("event_chain_valid"))
    _row("ok" if parents_ok else "err", "batch parents", "linear" if parents_ok else "invalid")
    _row("ok" if hashes_ok else "err", "content hashes", "valid" if hashes_ok else "invalid")
    _row("ok" if chain_ok else "err", "event chain", "valid" if chain_ok else "invalid")
    _row("ok", "batches", str(info.get("batch_count") or 0))
    _row("ok", "events", str(info.get("event_count") or 0))

    for error in (info.get("errors") or [])[:3]:
        _row("err", "  ↳ error", str(error))


def _render_doctor_security(report: dict) -> None:
    """Focused subview: versions + security pipeline only."""
    human_echo(_cli._bold("opentraces doctor — security"))
    _versions_section(report)
    _security_section(report["security"])
    human_echo("")
