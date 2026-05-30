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


def _infer_issue_repo(capsule) -> str | None:
    """``owner/name`` from the capsule's repo pin — the repo the bug happened in."""

    import re

    url = ((capsule.get("repo_pin") or {}).get("remote_url") or "").strip()
    m = re.search(r"github\.com[/:]([^/]+/[^/.\s]+)", url)
    return m.group(1) if m else None


def _default_hf_repo(token) -> str | None:
    """``cfg.capsule_repo`` if set, else ``<hf-user>/opentraces-capsules``."""

    try:
        from ..core.config import load_config

        cr = getattr(load_config(), "capsule_repo", None)
        if cr:
            return str(cr)
    except Exception:
        pass
    tok = _hf_token(token)
    if not tok:
        return None
    try:
        from huggingface_hub import HfApi

        name = HfApi(token=tok).whoami().get("name")
        return f"{name}/opentraces-capsules" if name else None
    except Exception:
        return None


def _publish_and_url(capsule, *, hf_repo, token, private):
    """Publish to HF (capsule.json + capsule.md only). Exits on error."""

    from ..core.capsule.share import publish_capsule

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
        f"capsule {capsule['capsule_id']} · {(capsule.get('summary') or {}).get('title','')[:80]}\n"
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


def _clip(do_copy, url):
    if do_copy and url:
        from ..core.capsule.share import copy_to_clipboard

        ok, how = copy_to_clipboard(url)
        click.echo(f"clipboard: {'copied via ' + how if ok else how}", err=True)


@capsule_group.command("share")
@click.argument("trace_id")
@_export_options
@click.option("--repo", "hf_repo", default=None, help="HF dataset repo (default: <you>/opentraces-capsules).")
@click.option("--publish", is_flag=True, help="Upload the capsule to HF (capsule.json + capsule.md only).")
@click.option("--private", is_flag=True, help="Create the HF dataset repo as private.")
@click.option("--copy", "do_copy", is_flag=True, help="Copy the shareable URL to the clipboard.")
@click.option("--token", default=None, help="HF token (default: env / config / live token file).")
def share_cmd(trace_id, step, node_id, radius, repo_url, project_dir, hf_repo, publish, private, do_copy, token):
    """Mint a shareable capsule URL (add --publish to upload it)."""

    from ..core.capsule.share import (
        human_capsule_url,
        mint_capsule_url,
        write_capsule_dir,
    )

    capsule, project = _do_export(trace_id, step, node_id, radius, repo_url, project_dir)
    arts = write_capsule_dir(capsule, project / ".opentraces" / "capsules")
    cid = capsule["capsule_id"]
    repo = hf_repo or _default_hf_repo(token)

    if publish:
        if not repo:
            click.echo("could not determine an HF repo; pass --repo <owner/name>.", err=True)
            sys.exit(2)
        url, human, info = _publish_and_url(capsule, hf_repo=repo, token=token, private=private)
        click.echo(f"published {cid} (rev {info['revision'][:12]}) · {human}", err=True)
    elif repo:
        url = mint_capsule_url(repo, cid)
        click.echo(f"URL form (add --publish to upload) · {human_capsule_url(repo, cid)}", err=True)
    else:
        click.echo(f"Local capsule: {arts['json']} (pass --repo / --publish to share)", err=True)
        click.echo(f"file://{arts['json']}")
        return
    _clip(do_copy, url)
    click.echo(url)  # primary stdout = the shareable URL


@capsule_group.command("issue")
@click.argument("trace_id")
@_export_options
@click.option("--repo", "hf_repo", default=None, help="HF dataset repo (default: <you>/opentraces-capsules).")
@click.option("--issue-repo", default=None, help="GitHub repo (default: inferred from the capsule's repo pin).")
@click.option("--title", default=None, help="Issue title (default: derived from the summary).")
@click.option("--publish", is_flag=True, help="Publish to HF and file/update the issue via gh.")
@click.option("--copy", "do_copy", is_flag=True, help="Copy the capsule URL to the clipboard.")
@click.option("--yes", "assume_yes", is_flag=True, help="Skip the publish confirmation (for scripts/agents).")
@click.option("--token", default=None, help="HF token (default: env / config / live token file).")
def issue_cmd(trace_id, step, node_id, radius, repo_url, project_dir, hf_repo, issue_repo, title, publish, do_copy, assume_yes, token):
    """Render the GitHub issue body for a capsule, or file it with --publish.

    The HF repo defaults to ``<you>/opentraces-capsules`` and the issue repo is
    inferred from the capsule's repo pin (the repo the bug happened in), so the
    common case is just ``opentraces capsule issue <trace> --publish``.
    """

    from ..core.capsule.render import render_issue_body
    from ..core.capsule.share import (
        GhError,
        GhUnavailableError,
        create_or_update_issue,
        gh_available,
        human_capsule_url,
        mint_capsule_url,
        write_capsule_dir,
    )

    capsule, project = _do_export(trace_id, step, node_id, radius, repo_url, project_dir)
    write_capsule_dir(capsule, project / ".opentraces" / "capsules")
    cid = capsule["capsule_id"]
    repo = hf_repo or _default_hf_repo(token)
    target_repo = issue_repo or _infer_issue_repo(capsule)

    if not publish:
        url = mint_capsule_url(repo, cid) if repo else None
        human = human_capsule_url(repo, cid) if repo else None
        body = render_issue_body(capsule, capsule_url=url, human_url=human)
        _clip(do_copy, url)
        click.echo(body)
        click.echo(
            f"\n(dry run · HF repo: {repo or '<unset, pass --repo>'} · "
            f"issue repo: {target_repo or '<unset, pass --issue-repo>'} · "
            "add --publish to file it.)",
            err=True,
        )
        return

    if not repo:
        click.echo("could not determine an HF repo; pass --repo <owner/name>.", err=True)
        sys.exit(2)
    if not target_repo:
        click.echo(
            "could not infer the GitHub repo from the capsule's repo pin; "
            "pass --issue-repo <owner/name>.",
            err=True,
        )
        sys.exit(2)
    if not gh_available():
        click.echo("GitHub CLI (`gh`) not found. Install via `brew install gh`.", err=True)
        sys.exit(2)

    # Explicit public-destination consent (replaces the old --public flag): a
    # confirm that names both destinations. --yes bypasses for scripts/agents.
    if not assume_yes:
        click.echo(
            f"This will PUBLISH a redacted capsule publicly to HF `{repo}` and "
            f"file/update a GitHub issue on `{target_repo}`.",
            err=True,
        )
        if not click.confirm("Proceed?", default=False):
            click.echo("aborted.", err=True)
            sys.exit(1)

    url, human, _info = _publish_and_url(capsule, hf_repo=repo, token=token, private=False)
    body = render_issue_body(capsule, capsule_url=url, human_url=human)
    _clip(do_copy, url)
    summary_title = (capsule.get("summary") or {}).get("title") or "session"
    derived_title = title or f"[agent capsule] {summary_title}"[:90]
    try:
        issue = create_or_update_issue(
            repo=target_repo, capsule_id=cid, title=derived_title, body=body,
        )
    except GhUnavailableError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
    except GhError as exc:
        click.echo(f"gh issue failed: {exc}", err=True)
        sys.exit(3)
    click.echo(f"{issue['action']} issue · capsule {cid} · {human}", err=True)
    click.echo(issue["url"])  # primary stdout = the issue URL


def _resolve_issue(issue_ref, repo_opt):
    from ..core.capsule.share import parse_issue_ref

    repo, number = parse_issue_ref(issue_ref)
    repo = repo or repo_opt
    if not repo or not number:
        click.echo(
            "could not parse the issue. Pass a full URL (…/issues/N), owner/repo#N, "
            "or N together with --repo owner/name.",
            err=True,
        )
        sys.exit(2)
    return repo, number


@capsule_group.command("replay")
@click.argument("ref")
@click.option("--against", "target_ref", default="HEAD", show_default=True,
              help="The post-fix ref to re-pose the intent against.")
@click.option("--json", "as_json", is_flag=True, help="Emit the replay packet as JSON (for the agent).")
def replay_cmd(ref, target_ref, as_json):
    """Build a replay packet: what a maintainer agent needs to re-pose the intent.

    The runner is the maintainer's own agent. This emits the intent + context +
    before/after commits + the success-oracle question; the agent re-poses and
    then records a verdict with `capsule verdict`.
    """

    from ..core.capsule.contract import CapsuleSchemaAheadError
    from ..core.capsule.replay import build_replay_packet
    from ..core.capsule.share import CapsuleResolveError, resolve_capsule

    try:
        capsule = resolve_capsule(ref)
    except CapsuleSchemaAheadError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
    except (CapsuleResolveError, ValueError) as exc:
        click.echo(f"not a resolvable opentraces capsule: {exc}", err=True)
        sys.exit(2)

    packet = build_replay_packet(capsule, target_ref=target_ref)
    if as_json:
        click.echo(json.dumps(packet, indent=2, ensure_ascii=False))
        return
    oracle = packet["oracle"]
    lines = [
        f"# Replay capsule {packet['capsule_id']}",
        f"Intent: {packet['intent']}",
        f"Failed at: {packet.get('before_commit') or '(unknown)'} → re-pose against: {target_ref}",
        f"Oracle ({oracle['strategy']}): {oracle['question']}",
        "",
        "Steps:",
        *[f"  {s}" for s in packet["instructions"]],
        "",
        "(full context: `opentraces capsule open <ref> --json` → context_resume_packet)",
    ]
    click.echo("\n".join(lines))


@capsule_group.command("verdict")
@click.argument("issue_ref")
@click.option("--state", required=True,
              type=click.Choice(["fixed", "reproduces", "inconclusive"]),
              help="The replay outcome.")
@click.option("--repo", "repo_opt", default=None, help="GitHub repo if the issue ref is a bare number.")
@click.option("--capsule", "capsule_ref", default=None, help="Capsule ref (default: read the issue's marker).")
@click.option("--ref", "target_ref", default="HEAD", show_default=True, help="The ref the intent was re-posed against.")
@click.option("--note", default=None, help="A short human note for the verdict comment.")
@click.option("--close/--no-close", default=False, help="Close the issue (for a `fixed` verdict).")
def verdict_cmd(issue_ref, state, repo_opt, capsule_ref, target_ref, note, close):
    """Record a replay verdict back onto the issue (closing the loop)."""

    from ..core.capsule.replay import render_verdict_comment
    from ..core.capsule.share import (
        GhError,
        GhUnavailableError,
        close_issue,
        comment_issue,
        gh_available,
        issue_state,
        resolve_capsule,
    )

    if not gh_available():
        click.echo("GitHub CLI (`gh`) not found. Install via `brew install gh`.", err=True)
        sys.exit(2)
    repo, number = _resolve_issue(issue_ref, repo_opt)

    capsule_id = None
    before_commit = None
    if capsule_ref:
        try:
            cap = resolve_capsule(capsule_ref)
            capsule_id = cap.get("capsule_id")
            before_commit = (cap.get("repo_pin") or {}).get("commit_sha")
        except Exception:
            pass
    try:
        if capsule_id is None:
            capsule_id = issue_state(repo, number).get("capsule_id")
    except (GhError, GhUnavailableError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    if not capsule_id:
        click.echo("could not determine the capsule id; pass --capsule <ref>.", err=True)
        sys.exit(2)

    body = render_verdict_comment(
        capsule_id=capsule_id, state=state, note=note,
        target_ref=target_ref, before_commit=before_commit,
    )
    try:
        comment_issue(repo, number, body)
        if close and state == "fixed":
            close_issue(repo, number, reason="completed")
    except GhError as exc:
        click.echo(f"gh issue verdict failed: {exc}", err=True)
        sys.exit(3)
    action = "verdict posted" + (" + issue closed" if (close and state == "fixed") else "")
    click.echo(f"{action} · {state} · {repo}#{number}", err=True)
    click.echo(f"https://github.com/{repo}/issues/{number}")


@capsule_group.command("watch")
@click.argument("issue_ref")
@click.option("--repo", "repo_opt", default=None, help="GitHub repo if the issue ref is a bare number.")
@click.option("--timeout", type=int, default=0, help="Poll up to N seconds for resolution (0 = one-shot).")
@click.option("--interval", type=int, default=30, show_default=True, help="Poll interval in seconds.")
@click.option("--json", "as_json", is_flag=True, help="Emit the resolution state as JSON.")
def watch_cmd(issue_ref, repo_opt, timeout, interval, as_json):
    """Client-side subscription: is the capsule's issue resolved yet? (auto-unblock cue)."""

    import time

    from ..core.capsule.share import (
        GhError,
        GhUnavailableError,
        gh_available,
        issue_state,
    )

    if not gh_available():
        click.echo("GitHub CLI (`gh`) not found. Install via `brew install gh`.", err=True)
        sys.exit(2)
    repo, number = _resolve_issue(issue_ref, repo_opt)

    deadline = (time.monotonic() + timeout) if timeout > 0 else None
    while True:
        try:
            info = issue_state(repo, number)
        except (GhError, GhUnavailableError) as exc:
            click.echo(str(exc), err=True)
            sys.exit(3)
        resolved = info["state"] == "CLOSED" or info["verdict"] == "fixed"
        if resolved or deadline is None or time.monotonic() >= deadline:
            break
        time.sleep(max(5, interval))

    unblocked = info["state"] == "CLOSED" or info["verdict"] == "fixed"
    if as_json:
        click.echo(json.dumps({**info, "repo": repo, "number": number, "unblocked": unblocked}, indent=2))
        return
    state_line = f"{repo}#{number} · {info['state']} · verdict={info['verdict'] or 'none'}"
    if unblocked:
        click.echo(f"✅ resolved — {state_line}", err=True)
        click.echo("UNBLOCKED: re-pose your original intent against the new HEAD to pick up the fix.")
    else:
        click.echo(f"⏳ not resolved yet — {state_line}", err=True)
        click.echo("PENDING")


__all__ = ["capsule_group"]
