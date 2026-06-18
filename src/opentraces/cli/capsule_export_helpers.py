"""Shared helpers for ``opentraces capsule`` export-style commands."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ._options import project_dir_option


def _resolve_project(project: Path | None) -> Path:
    return Path(project or Path.cwd()).resolve()


# Shared export options applied to export/share/issue.
def _export_options(fn):
    fn = click.option("--step", type=int, default=None, help="Failing step index (default: infer from error markers).")(fn)
    fn = click.option("--node", "node_id", default=None, help="Context node id (default: from the failing step).")(fn)
    fn = click.option("--radius", type=int, default=4, show_default=True, help="Slice radius around the failing step.")(fn)
    fn = click.option("--repo-url", default=None, help="Override the public repo remote URL recorded in the pin.")(fn)
    fn = project_dir_option(fn)
    fn = click.option(
        "--test-command", "test_command", default=None,
        help="Declare the repro command that makes this capsule a runnable test.",
    )(fn)
    fn = click.option(
        "--expect-error", "expect_error", default=None,
        help="Expected error string for the repro (omit = expect a non-zero exit).",
    )(fn)
    fn = click.option(
        "--setup-command", "setup_command", default=None,
        help="A setup/build/install step to run before the repro (e.g. 'pip install -e .').",
    )(fn)
    fn = click.option(
        "--consume", "consume_specs", multiple=True, metavar="[package|service:]NAME=PIN|URL",
        help="Record a CONSUMED dependency the verdict can be re-posed against "
             "(e.g. package:humanduration=git+https://github.com/o/r@v0.1.0). Repeatable.",
    )(fn)
    fn = click.option(
        "--product", default=None, metavar="NAME",
        help="Bind the capsule to ONE consumed product/dependency (the usage-episode "
             "grouping anchor); also scopes the slice to the steps that reference it.",
    )(fn)
    fn = click.option(
        "--include-prompts", "include_prompts", is_flag=True, default=False,
        help="Include prompt-bearing fields (system prompt + per-step reasoning) in the "
             "capsule. These are EXCLUDED by default; this opts them IN.",
    )(fn)
    return fn


def _parse_consume(spec: str) -> dict:
    """``[package|service:]NAME=PIN|ENDPOINT`` -> a consumes entry (default kind: package)."""

    kind = "package"
    body = spec
    head = spec.split(":", 1)[0]
    if ":" in spec and head in ("package", "service"):
        kind, body = spec.split(":", 1)
    if "=" not in body:
        raise click.BadParameter(f"--consume expects NAME=VALUE, got {spec!r}")
    name, value = body.split("=", 1)
    name, value = name.strip(), value.strip()
    if not name or not value:
        raise click.BadParameter(f"--consume needs a non-empty NAME and VALUE, got {spec!r}")
    if kind == "service":
        return {"kind": "service", "name": name, "endpoint": value}
    return {"kind": "package", "name": name, "pin": value}


def _do_export(trace_id, step, node_id, radius, repo_url, project_dir,
               test_command=None, expect_error=None, setup_command=None, consume_specs=(),
               product=None, include_prompts=False, product_full_span=False, progress=None):
    from ..core.capsule.export import CapsuleExportError, export_capsule

    project = _resolve_project(project_dir)
    consumes = [_parse_consume(s) for s in (consume_specs or ())]
    try:
        capsule = export_capsule(
            project_dir=project,
            trace_id=trace_id,
            step_index=step,
            node_id=node_id,
            radius=radius,
            remote_url=repo_url,
            test_command=test_command,
            expect_error=expect_error,
            setup_command=setup_command,
            consumes=consumes,
            product=product,
            product_full_span=product_full_span,
            include_prompts=include_prompts,
            progress=progress,
        )
    except CapsuleExportError as exc:
        click.echo(f"capsule export failed: {exc}", err=True)
        if progress is not None:
            progress.done()
        sys.exit(2)
    except Exception as exc:  # redaction gate, etc.
        click.echo(f"capsule export failed: {exc}", err=True)
        if progress is not None:
            progress.done()
        sys.exit(2)
    _hint_consumes(capsule, consumes, product)
    return capsule, project


def _hint_consumes(capsule, declared_consumes, product) -> None:
    """Suggest --consume specs when an episode is not product-bound.

    This stays stderr-only and never mutates ``environment.consumes``; the
    developer must confirm the product anchor explicitly.
    """

    if product or declared_consumes:
        return
    deps = list((capsule.get("environment") or {}).get("dependencies") or [])
    if not deps:
        return
    from ..enrichment import suggest_consumes

    hints = suggest_consumes(deps)
    if not hints:
        return
    click.echo(
        "hint: this episode is not bound to a product. Add --product <name> or "
        "--consume to anchor it. Candidates from captured dependencies:",
        err=True,
    )
    for h in hints[:8]:
        click.echo(f"  --consume {h}", err=True)


def _resolve_from_session_or_exit(session_id, from_agent, project_dir):
    """Materialize a live session into the bucket and return its trace_id."""

    from ..core.capsule.from_session import (
        EXCLUDED,
        INVALID_ID,
        LOCKED,
        NOT_FOUND,
        RESOLVED,
        UNPARSED,
        resolve_session_to_trace,
    )

    project = _resolve_project(project_dir)
    res = resolve_session_to_trace(session_id, project, agent=from_agent)

    if res.status == RESOLVED:
        return res.trace_id

    if res.status == INVALID_ID:
        click.echo(
            f"capsule export --from-session {session_id!r}: not a valid session id. "
            "A session id is an opaque token (letters, digits, '_', '.', '-') — it "
            "must not contain path separators, '..', or glob characters. Pass the "
            "exact session id (e.g. from 'opentraces trace query --cwd').",
            err=True,
        )
        sys.exit(2)

    if res.status == EXCLUDED:
        click.echo(
            f"capsule export --from-session {session_id}: the project at {project} "
            "is excluded from capture (opentraces ignore marker), so this session "
            "was never staged. Remove the exclusion or run from a captured project.",
            err=True,
        )
        sys.exit(2)

    if res.status == LOCKED:
        click.echo(
            f"capsule export --from-session {session_id}: another ingest is "
            "finalizing this session; retry in a moment.",
            err=True,
        )
        sys.exit(2)

    if res.status == UNPARSED:
        where = res.codex_sidecar if res.agent == "codex" else res.claude_glob
        click.echo(
            f"capsule export --from-session {session_id}: a {res.agent or 'session'} "
            f"source was found at {where} but carries no materializable turns yet "
            f"({res.detail}). The session may not be captured yet — finish the turn "
            "(the Stop hook ingests it), then run 'opentraces trace query --cwd' to "
            "find the trace id.",
            err=True,
        )
        sys.exit(2)

    # NOT_FOUND — name the exact paths checked + the recovery.
    click.echo(
        f"capsule export --from-session {session_id}: no Codex sidecar at "
        f"{res.codex_sidecar} and no Claude transcript at {res.claude_glob}. "
        "The session may not be captured yet — finish the turn (the Stop hook "
        "ingests it), then run 'opentraces trace query --cwd' to find the trace "
        "id, or pass --from-agent to disambiguate.",
        err=True,
    )
    sys.exit(2)
