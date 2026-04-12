"""CLI installers/admin group: notes, setup, blame, doctor, review-llm.

Extracted from cli/__init__.py (phase 5).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

import click

from opentraces import cli as _cli
from . import main
from ..core.config import save_config  # noqa: F401


def load_config():
    return _cli.load_config()

logger = logging.getLogger("opentraces.cli.installers")


def emit_json(data):
    return _cli.emit_json(data)


def error_response(*a, **k):
    return _cli.error_response(*a, **k)


def human_echo(*a, **k):
    return _cli.human_echo(*a, **k)


def human_hint(*a, **k):
    return _cli.human_hint(*a, **k)


def print_banner(*a, **k):
    return _cli.print_banner(*a, **k)




@main.command("notes")
@click.argument("ref", default="HEAD")
@click.option("--json", "json_out", is_flag=True, help="Emit machine-readable JSON")
def notes_cmd(ref: str, json_out: bool) -> None:
    """Print opentraces notes attached to a commit (plan 041 R27)."""
    from ..enrichment.git import notes_store

    cwd = Path.cwd()
    lines = notes_store.read(ref, cwd)
    parsed = [p for p in (notes_store.parse_link(l) for l in lines) if p]

    if json_out:
        emit_json({
            "ref": ref,
            "traces": [
                {"trace_id": tid, "url": url}
                for (tid, url) in parsed
            ],
        })
        return

    if not parsed:
        human_echo(f"no opentraces notes on {ref}")
        return
    human_echo(f"opentraces notes on {ref}:")
    for tid, url in parsed:
        if url:
            human_echo(f"  {tid}  {url}")
        else:
            human_echo(f"  {tid}")


@main.command("_run-post-commit-hook", hidden=True)
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False), default=".")
def run_post_commit_hook(repo_path: str) -> None:
    """Invoked by .git/hooks/opentraces-post-commit after each commit.

    Never raises to the shell: any failure exits 0.
    """
    import logging
    log = logging.getLogger("opentraces.post_commit")

    try:
        from datetime import datetime, timedelta, timezone

        from ..core.config import get_project_staging_dir
        from ..capture.git import post_commit
        from ..core.inbox import load_trace_records

        repo = Path(repo_path).resolve()
        staging = get_project_staging_dir(repo)
        if not staging.exists():
            return
        # Prune by timestamp_end before Pydantic validation — a staging
        # dir of hundreds of historical rows would otherwise parse on
        # every commit.
        since = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        records = load_trace_records(staging, since_iso=since)
        post_commit.run(repo, records)
    except Exception as e:
        log.debug("post-commit hook suppressed error: %s", e)


@main.group("setup")
def setup_group() -> None:
    """Install opentraces integrations (git hook, etc.)."""


@main.command("blame")
@click.argument("target")
@click.option("--json", "json_out", is_flag=True)
def blame_cmd(target: str, json_out: bool) -> None:
    """Resolve `file:line` to the opentraces session that authored it (R30).

    TARGET is `path:line`, e.g. `src/auth.py:42`.
    """
    from ..core.config import get_project_staging_dir
    from ..enrichment.git import blame as git_blame
    from ..core.inbox import load_trace_records

    if ":" not in target:
        raise click.BadParameter("expected <path>:<line>")
    file_path, line_str = target.rsplit(":", 1)
    try:
        line = int(line_str)
    except ValueError as e:
        raise click.BadParameter(f"bad line number: {line_str}") from e

    staging = get_project_staging_dir(Path.cwd())
    traces = {rec.trace_id: rec for rec in load_trace_records(staging)}

    hits = git_blame.blame(file_path, line, traces, Path.cwd())
    if json_out:
        emit_json({
            "target": target,
            "hits": [
                {
                    "trace_id": h.trace_id,
                    "step": h.step,
                    "revision": h.revision,
                    "content_alive": h.content_alive,
                }
                for h in hits
            ],
        })
        return

    if not hits:
        human_echo(f"no opentraces attribution at {target}")
        return
    for h in hits:
        alive = "alive" if h.content_alive else "dead"
        step = f" step_{h.step}" if h.step is not None else ""
        human_echo(f"  {h.trace_id}{step}  {h.revision[:10]}  [{alive}]")


@setup_group.command("git")
@click.option("--uninstall", is_flag=True, help="Remove the hook instead of installing.")
def setup_git(uninstall: bool) -> None:
    """Install/remove the opentraces post-commit hook (plan 041 R21)."""
    from ..capture.git import install as git_hook
    if uninstall:
        git_hook.remove(Path.cwd())
        human_echo("opentraces post-commit hook removed.")
        emit_json({"status": "ok", "action": "remove",
                   "state": git_hook.status(Path.cwd())})
        return
    ok = git_hook.install(Path.cwd())
    st = git_hook.status(Path.cwd())
    if ok and st["installed"]:
        human_echo("")
        print_banner(tagline=_cli._ok("git hook installed"))
        human_echo(f"  {_cli._dim('owned hook:')} {st['hook_dir']}/{git_hook.HOOK_FILENAME}")
    else:
        human_echo("install failed: not a git repo or insufficient permissions.")
    emit_json({"status": "ok" if ok else "error", "action": "install", "state": st})


# ---------------------------------------------------------------------------
# Plan 032 Phase 1 — security-module CLI surfaces.
# ---------------------------------------------------------------------------


def _install_trufflehog_binary() -> tuple[bool, str]:
    """Install trufflehog via the first available path. Returns (ok, method)."""
    import subprocess as _sp

    if shutil.which("brew"):
        try:
            _sp.run(["brew", "install", "trufflehog"], check=True)
            return True, "brew"
        except _sp.CalledProcessError:
            pass
    if shutil.which("go"):
        try:
            _sp.run(
                ["go", "install",
                 "github.com/trufflesecurity/trufflehog/v3@latest"],
                check=True,
            )
            return True, "go install"
        except _sp.CalledProcessError:
            pass
    return False, "none"


@setup_group.command("trufflehog")
@click.option("--disable", is_flag=True, help="Turn the Tier 1.5 tier off without uninstalling the binary.")
@click.option("--verify", is_flag=True, help="Skip install; just verify binary is present and enable the tier.")
def setup_trufflehog_cmd(disable: bool, verify: bool) -> None:
    """Install or configure TruffleHog (Plan 032 Tier 1.5)."""
    from ..security.trufflehog import find_trufflehog

    cfg = load_config()

    if disable:
        cfg.security.trufflehog.enabled = False
        save_config(cfg)
        human_echo("TruffleHog tier disabled. Binary was not uninstalled.")
        emit_json({"status": "ok", "action": "disable",
                   "trufflehog_enabled": False})
        return

    if verify:
        version = find_trufflehog()
        if version is None:
            human_echo("trufflehog not on PATH. Install it first, or re-run without --verify.")
            emit_json(error_response(
                "TRUFFLEHOG_MISSING", "setup",
                "trufflehog binary not found",
                "Run 'opentraces setup trufflehog' (no flags) to install.",
            ))
            sys.exit(3)
        cfg.security.trufflehog.enabled = True
        save_config(cfg)
        human_echo(f"TruffleHog verified ({version}) and tier enabled.")
        emit_json({"status": "ok", "action": "verify",
                   "trufflehog_version": version, "trufflehog_enabled": True})
        return

    version = find_trufflehog()
    if version is None:
        ok, method = _install_trufflehog_binary()
        if not ok:
            human_echo(
                "Could not install trufflehog automatically.\n"
                "Install it manually from https://github.com/trufflesecurity/trufflehog\n"
                "then re-run 'opentraces setup trufflehog --verify'."
            )
            emit_json(error_response(
                "TRUFFLEHOG_INSTALL_FAILED", "setup",
                "no supported installer found (brew, go install)",
                "Install manually, then run --verify.",
            ))
            sys.exit(4)
        version = find_trufflehog()
        if version is None:
            human_echo("trufflehog installed but not on PATH. Re-run --verify once PATH is updated.")
            sys.exit(4)
        human_echo(f"Installed trufflehog via {method}: {version}")
    else:
        human_echo(f"trufflehog already present: {version}")

    cfg.security.trufflehog.enabled = True
    save_config(cfg)
    human_echo("")
    print_banner(tagline=_cli._ok(f"trufflehog ready ({version})"))
    human_echo(f"  {_cli._dim('Tier 1.5 enabled — scans and pushes will run TruffleHog.')}")
    emit_json({"status": "ok", "action": "install",
               "trufflehog_version": version, "trufflehog_enabled": True})


@main.command("doctor")
def doctor_cmd() -> None:
    """Report the health of the opentraces security pipeline."""
    from ..security.trufflehog import find_trufflehog
    from ..security.version import SECURITY_VERSION

    cfg = load_config()
    th_version = find_trufflehog()
    th_enabled = cfg.security.trufflehog.enabled

    report: dict[str, object] = {
        "security_version": SECURITY_VERSION,
        "schema_version": None,
        "trufflehog": {
            "enabled": th_enabled,
            "binary_version": th_version,
            "status": _trufflehog_status(th_enabled, th_version),
        },
        "hf_auth": "ok" if cfg.hf_token else "missing",
    }

    try:
        from opentraces_schema import SCHEMA_VERSION  # type: ignore
        report["schema_version"] = SCHEMA_VERSION
    except Exception:
        pass

    # Intent mode (plan 038)
    report["intent"] = {"mode": cfg.intent.mode}

    # Post-processors — enumerate + probe (plan 038 phase 4)
    from ..core.config import ProjectConfig, load_project_config
    from ..core.processors import probe_processors
    try:
        raw = load_project_config(Path.cwd())
        proj_cfg = ProjectConfig.model_validate(raw) if raw else None
        specs = proj_cfg.post_processors if proj_cfg else []
    except Exception:
        specs = []
    processors_report: list[dict[str, object]] = []
    for spec, resolved in probe_processors(specs):
        processors_report.append({
            "name": spec.name,
            "command": spec.command,
            "when": spec.when,
            "resolved_path": resolved,
            "status": "detected" if resolved else "missing",
        })
    report["post_processors"] = processors_report

    human_echo("opentraces doctor")
    human_echo(f"  security version: {SECURITY_VERSION}")
    if report["schema_version"]:
        human_echo(f"  schema version:   {report['schema_version']}")
    human_echo(f"  trufflehog:       {report['trufflehog']['status']}")
    human_echo(f"  hf auth:          {report['hf_auth']}")
    human_echo(f"  intent mode:      {report['intent']['mode']}")
    if processors_report:
        human_echo("  post-processors:")
        for p in processors_report:
            human_echo(f"    - {p['name']} ({p['when']}): {p['status']}")
    else:
        human_echo("  post-processors:  (none configured)")
    emit_json({"status": "ok", "doctor": report})
    if th_enabled and th_version is None:
        sys.exit(3)


def _trufflehog_status(enabled: bool, version: str | None) -> str:
    if not enabled:
        return "disabled (opt in via 'opentraces setup trufflehog')"
    if version is None:
        return "ENABLED-BUT-MISSING — run 'opentraces setup trufflehog --verify'"
    return f"enabled ({version})"


@main.command("review-llm")
@click.option("--provider", default="ollama",
              help="LLM provider: ollama, anthropic, fake.")
@click.option("--model", default="gemma4:e4b",
              help="Model name (default: gemma4:e4b for ollama; claude-haiku-4-5-20251001 recommended for anthropic).")
@click.option("--dry-run", is_flag=True,
              help="Estimate token usage and cost without calling the provider.")
@click.option("--limit", type=int, default=0,
              help="Max sessions to review this invocation (0 = all staged).")
@click.option("--force", is_flag=True,
              help="Re-review sessions that already have a cached verdict.")
@click.option("--context-file", "context_file", type=click.Path(exists=True, dir_okay=False),
              default=None, help="Optional README/AGENTS.md passed as project context.")
def review_llm_cmd(provider: str, model: str, dry_run: bool, limit: int, force: bool,
                   context_file: str | None) -> None:
    """Run Tier 2 LLM semantic review over staged/committed sessions."""
    from ..core.config import get_project_staging_dir
    from ..core.inbox import load_traces
    from ..security.llm_provider import build_provider
    from ..security.llm_review import (
        estimate_cost,
        review_session,
        review_key as _review_key,
    )
    from ..security.verdict_display import verdict_badge, verdict_to_payload

    staging = get_project_staging_dir(Path.cwd())
    if not staging.exists():
        human_echo("No staging directory found. Run opentraces init first.")
        emit_json(error_response(
            "NO_STAGING", "review", "staging dir missing",
            "Run 'opentraces init'.",
        ))
        sys.exit(2)

    raw = load_traces(staging)
    records: list[dict] = list(raw)
    if limit > 0:
        records = records[:limit]

    context = ""
    if context_file:
        try:
            context = Path(context_file).read_text()[:10_000]
        except OSError as exc:
            human_echo(f"Could not read context file: {exc}")
            sys.exit(2)

    # -- Dry-run: estimate only, no provider calls.
    if dry_run:
        total_chars = 0
        for rec in records:
            for step in rec.get("steps", []) or []:
                total_chars += len(step.get("content") or "")
                total_chars += len(step.get("reasoning_content") or "")
        est = estimate_cost(total_chars, model=model)
        human_echo(
            f"Dry run: {len(records)} sessions, ~{total_chars:,} chars, "
            f"~{int(est['tokens']):,} tokens, ~${est['cost_usd']:.4f}."
        )
        emit_json({
            "status": "ok",
            "action": "review-llm",
            "dry_run": True,
            "sessions": len(records),
            "chars": total_chars,
            "estimate": est,
            "model": model,
            "provider": provider,
        })
        return

    llm = build_provider(provider, model=model)
    results: list[dict] = []
    for rec in records:
        trace_id = rec.get("trace_id", "?")
        steps_text: list[str] = []
        for step in rec.get("steps", []) or []:
            content = step.get("content") or ""
            reasoning = step.get("reasoning_content") or ""
            if content or reasoning:
                steps_text.append("\n".join(filter(None, [content, reasoning])))
        content_hash = rec.get("content_hash", "")
        key = _review_key(content_hash, model, "1", context)

        existing = (rec.get("metadata") or {}).get("llm_review", {}) or {}
        if not force and existing.get("review_key") == key:
            human_echo(f"[cached] {trace_id}: {verdict_badge(None) if not existing else existing.get('badge', '(cached)')}")
            results.append({"trace_id": trace_id, "cached": True, "verdict": existing})
            continue

        verdict = review_session(steps=steps_text, provider=llm, context=context)
        payload = verdict_to_payload(verdict)
        payload["review_key"] = key
        human_echo(f"{trace_id}: {verdict_badge(verdict)}")
        results.append({"trace_id": trace_id, "cached": False, "verdict": payload})

    emit_json({
        "status": "ok",
        "action": "review-llm",
        "dry_run": False,
        "provider": provider,
        "model": model,
        "results": results,
    })

