"""``opentraces capsule`` — export, share, open, and file trace capsules.

v1 share-first surface (plan 082):

* ``capsule export <trace>``        — build a local self-contained capsule (zero
                                      remote config). stdout = the capsule.json path.
* ``capsule open <ref> --json``     — the CONSUME verb. Resolve a capsule from a
                                      file / https / hf:// ref and print the frozen
                                      ``opentraces.capsule.v1`` envelope. The
                                      maintainer agent's <5-step path.
* ``capsule share <trace> --repo``  — mint the shareable URL (``--execute`` uploads
                                      only capsule.json + capsule.md to HF). stdout =
                                      the URL. ``--copy`` to clipboard.
* ``capsule issue create <trace>``  — render a GitHub issue body embedding the URL +
                                      the ``capsule open`` command (``--execute`` files
                                      it via ``gh``, idempotent on the capsule marker).

Exit codes (house convention): 2 = precondition/tooling/export failure,
3 = remote / gh failure.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click


def _resolve_project(project: Path | None) -> Path:
    return Path(project or Path.cwd()).resolve()


def _hf_token(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for var in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    # opentraces config token
    try:
        from ..core.config import load_config

        tok = getattr(load_config(), "hf_token", None)
        if tok:
            return str(tok)
    except Exception:
        pass
    # otbox live token file (operator convenience)
    live = Path.home() / ".opentraces" / "otbox-live-hf-token"
    if live.exists():
        try:
            return live.read_text(encoding="utf-8").strip() or None
        except Exception:
            return None
    return None


# Shared export options applied to export/share/issue.
def _export_options(fn):
    fn = click.option("--step", type=int, default=None, help="Failing step index (default: infer from error markers).")(fn)
    fn = click.option("--node", "node_id", default=None, help="Context node id (default: from the failing step).")(fn)
    fn = click.option("--radius", type=int, default=4, show_default=True, help="Slice radius around the failing step.")(fn)
    fn = click.option("--repo-url", default=None, help="Override the public repo remote URL recorded in the pin.")(fn)
    fn = click.option(
        "--project", "project_dir",
        type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
        default=None, help="Project directory (default: CWD).",
    )(fn)
    return fn


def _do_export(trace_id, step, node_id, radius, repo_url, project_dir):
    from ..core.capsule.export import CapsuleExportError, export_capsule

    project = _resolve_project(project_dir)
    try:
        return export_capsule(
            project_dir=project,
            trace_id=trace_id,
            step_index=step,
            node_id=node_id,
            radius=radius,
            remote_url=repo_url,
        ), project
    except CapsuleExportError as exc:
        click.echo(f"capsule export failed: {exc}", err=True)
        sys.exit(2)
    except Exception as exc:  # redaction gate, etc.
        click.echo(f"capsule export failed: {exc}", err=True)
        sys.exit(2)


def _mint_and_maybe_publish(capsule, *, hf_repo, execute, private, public_ack, token):
    """Return (capsule_url, human_url, published_info|None). Exits on policy/remote errors."""

    from ..core.capsule.share import (
        human_capsule_url,
        mint_capsule_url,
        publish_capsule,
    )

    cid = capsule["capsule_id"]
    if execute:
        if not hf_repo:
            click.echo("--execute requires --repo <owner/name> (the HF dataset to publish to).", err=True)
            sys.exit(2)
        if not public_ack:
            click.echo(
                f"Refusing to publish to a PUBLIC destination ({hf_repo}) without "
                "consent. Re-run with --public to acknowledge this publishes a "
                "redacted capsule of your agent session publicly.",
                err=True,
            )
            sys.exit(2)
        tok = _hf_token(token)
        if not tok:
            click.echo(
                "No Hugging Face token found. Pass --token, set HF_TOKEN, or run "
                "`opentraces auth login`.",
                err=True,
            )
            sys.exit(2)
        try:
            info = publish_capsule(capsule, repo_id=hf_repo, token=tok, private=private)
        except Exception as exc:
            click.echo(f"capsule publish failed: {exc}", err=True)
            sys.exit(3)
        return info["capsule_url"], info["human_url"], info

    # Dry: construct the URL form (not yet published).
    if hf_repo:
        return (
            mint_capsule_url(hf_repo, cid, revision="main"),
            human_capsule_url(hf_repo, cid, revision="main"),
            None,
        )
    return None, None, None


@click.group("capsule")
def capsule_group() -> None:
    """Export, share, and open trace capsules (agent-to-agent bug reports)."""


@capsule_group.command("export")
@click.argument("trace_id")
@_export_options
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Output dir (default: <project>/.opentraces/capsules).")
@click.option("--json", "as_json", is_flag=True, help="Print the capsule envelope JSON to stdout.")
def export_cmd(trace_id, step, node_id, radius, repo_url, project_dir, out, as_json):
    """Build a local, redacted, self-contained capsule for one failing session."""

    from ..core.capsule.share import write_capsule_dir

    capsule, project = _do_export(trace_id, step, node_id, radius, repo_url, project_dir)
    dest = out or (project / ".opentraces" / "capsules")
    arts = write_capsule_dir(capsule, dest)
    if as_json:
        click.echo(json.dumps(capsule, indent=2, ensure_ascii=False))
        return
    manifest = (capsule.get("redaction") or {}).get("manifest") or {}
    click.echo(
        f"capsule {capsule['capsule_id']} · intent: "
        f"{(capsule.get('intent') or {}).get('headline','')[:70]!r}\n"
        f"  redaction: floor {manifest.get('floor')} ran · "
        f"{manifest.get('redactions_applied',0)} redactions · "
        f"{manifest.get('home_paths_scrubbed',0)} paths scrubbed\n"
        f"  closure: {(capsule.get('render_state') or {}).get('closure')} · "
        f"limitations: {capsule.get('limitations')}\n"
        f"  md: {arts['md']}",
        err=True,
    )
    click.echo(str(arts["json"]))  # primary stdout = the capsule path


@capsule_group.command("open")
@click.argument("ref")
@click.option("--json/--no-json", "as_json", default=True, show_default=True,
              help="Emit the frozen capsule envelope as JSON (the agent contract).")
@click.option("--summary", is_flag=True, help="Print the human markdown instead of JSON.")
def open_cmd(ref, as_json, summary):
    """Resolve a capsule (file / https / hf:// ref) and print its envelope.

    This is the agent-to-agent consume verb: one command, structured JSON out,
    zero bespoke parsing. The ``--json`` flag is the default and is accepted
    explicitly so the command embedded in the issue body runs verbatim.
    """

    from ..core.capsule.contract import CapsuleSchemaAheadError
    from ..core.capsule.render import render_capsule_markdown
    from ..core.capsule.share import CapsuleResolveError, resolve_capsule

    try:
        capsule = resolve_capsule(ref)
    except CapsuleSchemaAheadError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
    except (CapsuleResolveError, ValueError) as exc:
        click.echo(f"not a resolvable opentraces capsule: {exc}", err=True)
        sys.exit(2)
    except Exception as exc:  # never crash with a house-foreign traceback
        click.echo(f"failed to open capsule {ref!r}: {exc}", err=True)
        sys.exit(2)
    if summary:
        click.echo(render_capsule_markdown(capsule))
        return
    click.echo(json.dumps(capsule, indent=2, ensure_ascii=False))


@capsule_group.command("share")
@click.argument("trace_id")
@_export_options
@click.option("--repo", "hf_repo", default=None, help="HF dataset repo (owner/name) to publish to.")
@click.option("--execute", is_flag=True, help="Actually upload capsule.json + capsule.md to HF.")
@click.option("--public", "public_ack", is_flag=True, help="Acknowledge publishing publicly (required with --execute).")
@click.option("--private", is_flag=True, help="Create the HF dataset repo as private.")
@click.option("--copy", "do_copy", is_flag=True, help="Copy the shareable URL to the clipboard.")
@click.option("--token", default=None, help="HF token (default: env / config / live token file).")
def share_cmd(trace_id, step, node_id, radius, repo_url, project_dir, hf_repo, execute, public_ack, private, do_copy, token):
    """Mint a shareable capsule URL (and optionally publish it)."""

    from ..core.capsule.share import copy_to_clipboard, write_capsule_dir

    capsule, project = _do_export(trace_id, step, node_id, radius, repo_url, project_dir)
    arts = write_capsule_dir(capsule, project / ".opentraces" / "capsules")
    url, human, info = _mint_and_maybe_publish(
        capsule, hf_repo=hf_repo, execute=execute, private=private,
        public_ack=public_ack, token=token,
    )
    if url is None:
        click.echo(
            f"Local capsule written: {arts['json']}\n"
            "Pass --repo <owner/name> to mint a shareable HF URL, and --execute to publish it.",
            err=True,
        )
        click.echo(f"file://{arts['json']}")
        return
    if info is not None:
        click.echo(f"Published capsule {capsule['capsule_id']} (rev {info['revision'][:12]}). Human page: {human}", err=True)
    else:
        click.echo(f"URL form (not yet published; add --execute): human {human}", err=True)
    if do_copy:
        ok, how = copy_to_clipboard(url)
        click.echo(f"clipboard: {'copied via ' + how if ok else how}", err=True)
    click.echo(url)  # primary stdout = the shareable URL


@capsule_group.group("issue")
def issue_group() -> None:
    """Render or file a GitHub issue carrying a capsule."""


def _issue_options(fn):
    fn = click.argument("trace_id")(fn)
    fn = _export_options(fn)
    fn = click.option("--repo", "hf_repo", default=None, help="HF dataset repo to publish the capsule to.")(fn)
    fn = click.option("--issue-repo", default=None, help="GitHub repo (owner/name) to file the issue on.")(fn)
    fn = click.option("--title", default=None, help="Issue title (default: derived from intent).")(fn)
    fn = click.option("--execute", is_flag=True, help="Publish to HF and file/update the issue via gh.")(fn)
    fn = click.option("--public", "public_ack", is_flag=True, help="Acknowledge publishing publicly (required with --execute).")(fn)
    fn = click.option("--copy", "do_copy", is_flag=True, help="Copy the capsule URL to the clipboard.")(fn)
    fn = click.option("--token", default=None, help="HF token (default: env / config / live token file).")(fn)
    return fn


def _build_issue(trace_id, step, node_id, radius, repo_url, project_dir, hf_repo, execute, public_ack, token):
    from ..core.capsule.render import render_issue_body
    from ..core.capsule.share import write_capsule_dir

    capsule, project = _do_export(trace_id, step, node_id, radius, repo_url, project_dir)
    write_capsule_dir(capsule, project / ".opentraces" / "capsules")
    url, human, info = _mint_and_maybe_publish(
        capsule, hf_repo=hf_repo, execute=execute, private=False,
        public_ack=public_ack, token=token,
    )
    body = render_issue_body(capsule, capsule_url=url, human_url=human)
    return capsule, body, url, info


@issue_group.command("render")
@_issue_options
def issue_render_cmd(trace_id, step, node_id, radius, repo_url, project_dir, hf_repo, issue_repo, title, execute, public_ack, do_copy, token):
    """Render the GitHub issue body to stdout (no outward action)."""

    _capsule, body, _url, _info = _build_issue(
        trace_id, step, node_id, radius, repo_url, project_dir, hf_repo, False, public_ack, token,
    )
    click.echo(body)


@issue_group.command("create")
@_issue_options
def issue_create_cmd(trace_id, step, node_id, radius, repo_url, project_dir, hf_repo, issue_repo, title, execute, public_ack, do_copy, token):
    """Create (or idempotently update) a GitHub issue carrying the capsule."""

    from ..core.capsule.share import (
        GhError,
        GhUnavailableError,
        copy_to_clipboard,
        create_or_update_issue,
        gh_available,
    )

    capsule, body, url, _info = _build_issue(
        trace_id, step, node_id, radius, repo_url, project_dir, hf_repo, execute, public_ack, token,
    )
    if do_copy and url:
        ok, how = copy_to_clipboard(url)
        click.echo(f"clipboard: {'copied via ' + how if ok else how}", err=True)

    if not execute:
        click.echo(body)
        click.echo(
            "\n(dry run: pass --execute --issue-repo <owner/name> --repo <hf-repo> "
            "--public to publish + file the issue.)",
            err=True,
        )
        return

    if not issue_repo:
        click.echo("--execute requires --issue-repo <owner/name>.", err=True)
        sys.exit(2)
    if not gh_available():
        click.echo("GitHub CLI (`gh`) not found. Install via `brew install gh`.", err=True)
        sys.exit(2)
    derived_title = title or f"[agent capsule] {(capsule.get('intent') or {}).get('headline','bug')[:80]}"
    try:
        info = create_or_update_issue(
            repo=issue_repo, capsule_id=capsule["capsule_id"], title=derived_title, body=body,
        )
    except GhUnavailableError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
    except GhError as exc:
        click.echo(f"gh issue failed: {exc}", err=True)
        sys.exit(3)
    click.echo(f"{info['action']} issue: {info['url']}", err=True)
    click.echo(info["url"])  # primary stdout = the issue URL


__all__ = ["capsule_group"]
