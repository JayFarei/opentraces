"""``ot trail blame pr`` — render PR bodies + manage GitHub PRs from trace lineage.

Three subcommands forming the ``trail blame pr`` group:

* ``render`` — runs the ``pr-intent-summary-v1`` workflow over the current
  branch context and prints the markdown PR body to stdout (or a file).
* ``create`` — calls ``gh pr create`` with the rendered body. Idempotent:
  if a PR already exists on the branch, falls through to ``update``.
* ``update`` — calls ``gh pr edit <number> --body-file -``.

All three share one renderer so the body is byte-identical regardless of
which subcommand the user runs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from ._help import OpentracesCommand, OpentracesGroup


PR_INTENT_WORKFLOW_NAME = "pr-intent-summary"
PR_INTENT_WORKFLOW_TEMPLATE = "pr-intent-summary-v1"


def _resolve_project_dir(project_dir: Path | None) -> Path:
    return Path(project_dir or Path.cwd()).resolve()


def _ensure_workflow_installed() -> None:
    """Best-effort: install the workflow from its template if not already present."""

    from ..core.workflows import (
        create_workflow,
        load_workflow,
    )

    try:
        load_workflow(PR_INTENT_WORKFLOW_NAME)
        return
    except FileNotFoundError:
        pass
    try:
        create_workflow(
            PR_INTENT_WORKFLOW_NAME,
            template=PR_INTENT_WORKFLOW_TEMPLATE,
            description="PR body intent summary, branch-context consumer (auto-installed).",
        )
    except FileExistsError:
        return


def _render_for_branch(
    *,
    project_dir: Path,
    base: str,
    head: str | None,
    force: bool = False,
    llm_polish: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Run the workflow and render the markdown body. Returns (body, meta)."""

    from opentraces import __version__
    from ..consumers.branch_pr import (
        bucket_remote_url,
        render_pr_markdown,
        run_branch_workflow,
    )

    _ensure_workflow_installed()
    result = run_branch_workflow(
        PR_INTENT_WORKFLOW_NAME,
        project_dir=project_dir,
        base=base,
        head=head or "HEAD",
        force=force,
        llm_polish=llm_polish,
    )
    body = render_pr_markdown(
        result.rows,
        opentraces_version=__version__,
        bucket_remote_url=bucket_remote_url(),
        scope_digest=result.scope.scope_digest,
        workflow_name=result.workflow_name,
    )
    # Derive the PR title from the branch's structured commit list — the
    # latest non-merge commit subject. Done here (not by re-parsing the
    # rendered markdown) so it works regardless of which body layout the
    # renderer chose.
    non_merge = [c for c in result.scope.commits if not c.is_merge]
    derived_title = non_merge[-1].subject if non_merge else (
        f"opentraces: branch {result.scope.scope_digest[:12]}"
    )
    meta = {
        "row_count": len(result.rows),
        "cache_hit": result.cache_hit,
        "scope_digest": result.scope.scope_digest,
        "merge_base": result.scope.merge_base,
        "head": result.scope.head,
        "base": result.scope.base,
        "output_path": str(result.output_path),
        "derived_title": derived_title,
    }
    return body, meta


def attach(parent_group: click.Group) -> None:
    """Attach the ``pr`` subgroup to an existing parent group.

    Called by ``cli/__init__.py`` with the ``blame`` group so the final
    surface is ``opentraces trail blame pr render | create | update``.
    Lives under ``blame`` because every consumer (PR, future Slack,
    dashboard, CI) renders the same blame-shaped data for a different
    place and time.
    """

    @parent_group.group("pr", cls=OpentracesGroup)
    def trail_pr_group() -> None:
        """Render and publish PR bodies from blame-shaped trace lineage."""

    @trail_pr_group.command(
        "render",
        cls=OpentracesCommand,
        examples=[
            "opentraces trail blame pr render --base main",
            "opentraces trail blame pr render --base main --output /tmp/pr.md",
            "opentraces trail blame pr render --base main --force-rebuild",
        ],
        see_also=[
            ("opentraces trail blame pr create", "create a GitHub PR with this body."),
            ("opentraces trail blame pr update", "update the existing PR's body."),
        ],
        option_groups=[
            ("Scope", ["base", "head", "project_dir"]),
            ("Output", ["output", "force_rebuild"]),
        ],
    )
    @click.option("--base", default="main", show_default=True, help="Base branch.")
    @click.option("--head", default=None, help="Head ref (default: HEAD).")
    @click.option(
        "--project",
        "project_dir",
        type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
        default=None,
        help="Project directory (default: CWD).",
    )
    @click.option(
        "--output", "-o",
        "output",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help="Write body to this file instead of stdout.",
    )
    @click.option(
        "--force-rebuild",
        "force_rebuild",
        is_flag=True,
        help="Re-run the workflow even if a cached result exists for this branch.",
    )
    @click.option(
        "--llm/--no-llm",
        "llm_polish",
        default=True,
        show_default=True,
        help=(
            "Enrich the PR body with an LLM-polished headline + commit one-liners. "
            "Falls back to deterministic-only render if ANTHROPIC_API_KEY is missing."
        ),
    )
    def render_cmd(
        base: str,
        head: str | None,
        project_dir: Path | None,
        output: Path | None,
        force_rebuild: bool,
        llm_polish: bool,
    ) -> None:
        """Render the PR body for the current branch."""

        project = _resolve_project_dir(project_dir)
        try:
            body, meta = _render_for_branch(
                project_dir=project,
                base=base,
                head=head,
                force=force_rebuild,
                llm_polish=llm_polish,
            )
        except Exception as exc:
            click.echo(f"trail blame pr render failed: {exc}", err=True)
            sys.exit(2)

        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(body, encoding="utf-8")
            click.echo(
                f"Wrote {len(body)} chars to {output} "
                f"(rows={meta['row_count']}, cache_hit={meta['cache_hit']}, "
                f"digest={meta['scope_digest'][:12]})",
                err=True,
            )
            return
        click.echo(body, nl=False)

    @trail_pr_group.command(
        "create",
        cls=OpentracesCommand,
        examples=[
            "opentraces trail blame pr create --base main",
            "opentraces trail blame pr create --base main --draft",
            "opentraces trail blame pr create --base main --title 'feat: bucket sync'",
        ],
        see_also=[
            ("opentraces trail blame pr render", "preview the body before creating."),
            ("opentraces trail blame pr update", "update an existing PR's body."),
        ],
        option_groups=[
            ("Scope", ["base", "head", "project_dir"]),
            ("PR options", ["title", "draft", "update_if_exists"]),
        ],
    )
    @click.option("--base", default="main", show_default=True)
    @click.option("--head", default=None)
    @click.option(
        "--project", "project_dir",
        type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
        default=None,
    )
    @click.option(
        "--title",
        default=None,
        help="PR title (default: derive from the latest non-merge commit subject).",
    )
    @click.option("--draft", is_flag=True, help="Create as a draft PR.")
    @click.option(
        "--update-if-exists/--no-update-if-exists",
        "update_if_exists",
        default=True,
        show_default=True,
        help="If a PR already exists on this branch, update its body instead of erroring.",
    )
    @click.option(
        "--llm/--no-llm",
        "llm_polish",
        default=True,
        show_default=True,
        help="Enrich the PR body with an LLM-polished headline + commit one-liners.",
    )
    def create_cmd(
        base: str,
        head: str | None,
        project_dir: Path | None,
        title: str | None,
        draft: bool,
        update_if_exists: bool,
        llm_polish: bool,
    ) -> None:
        """Create (or update) a GitHub PR with this branch's lineage body."""

        from ..consumers.branch_pr import (
            GhError,
            GhUnavailableError,
            create_pr,
            find_pr_for_branch,
            gh_available,
            update_pr,
        )

        project = _resolve_project_dir(project_dir)
        if not gh_available():
            click.echo(
                "GitHub CLI (`gh`) not found. Install via `brew install gh` "
                "or see https://cli.github.com/.",
                err=True,
            )
            sys.exit(2)

        try:
            body, meta = _render_for_branch(
                project_dir=project, base=base, head=head, llm_polish=llm_polish,
            )
        except Exception as exc:
            click.echo(f"trail blame pr create failed during render: {exc}", err=True)
            sys.exit(2)

        # Idempotency: detect existing PR on the branch.
        try:
            existing = find_pr_for_branch(project)
        except GhUnavailableError as exc:
            click.echo(str(exc), err=True)
            sys.exit(2)

        if existing is not None:
            if not update_if_exists:
                click.echo(
                    f"PR #{existing.number} already exists for this branch: "
                    f"{existing.url}\nPass --update-if-exists to refresh its body.",
                    err=True,
                )
                sys.exit(2)
            try:
                info = update_pr(
                    project_dir=project,
                    number=existing.number,
                    body=body,
                )
            except GhError as exc:
                click.echo(f"gh pr edit failed: {exc}", err=True)
                sys.exit(3)
            click.echo(f"Updated PR #{info.number}: {info.url}")
            return

        derived_title = title or meta["derived_title"]
        try:
            info = create_pr(
                project_dir=project,
                base=base,
                title=derived_title,
                body=body,
                draft=draft,
            )
        except GhError as exc:
            click.echo(f"gh pr create failed: {exc}", err=True)
            sys.exit(3)
        click.echo(f"Created PR #{info.number}: {info.url}")

    @trail_pr_group.command(
        "update",
        cls=OpentracesCommand,
        examples=[
            "opentraces trail blame pr update --base main",
            "opentraces trail blame pr update --base main --number 42",
        ],
        see_also=[
            ("opentraces trail blame pr render", "preview the body before updating."),
            ("opentraces trail blame pr create", "create a new PR (idempotent)."),
        ],
        option_groups=[
            ("Scope", ["base", "head", "project_dir"]),
            ("Target", ["number"]),
        ],
    )
    @click.option("--base", default="main", show_default=True)
    @click.option("--head", default=None)
    @click.option(
        "--project", "project_dir",
        type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
        default=None,
    )
    @click.option(
        "--number", "-n",
        type=int,
        default=None,
        help="PR number to update (default: detect from current branch).",
    )
    @click.option(
        "--llm/--no-llm",
        "llm_polish",
        default=True,
        show_default=True,
        help="Enrich the PR body with an LLM-polished headline + commit one-liners.",
    )
    def update_cmd(
        base: str,
        head: str | None,
        project_dir: Path | None,
        number: int | None,
        llm_polish: bool,
    ) -> None:
        """Update an existing GitHub PR's body with this branch's lineage body."""

        from ..consumers.branch_pr import (
            GhError,
            GhUnavailableError,
            find_pr_for_branch,
            gh_available,
            update_pr,
        )

        project = _resolve_project_dir(project_dir)
        if not gh_available():
            click.echo(
                "GitHub CLI (`gh`) not found. Install via `brew install gh`.",
                err=True,
            )
            sys.exit(2)

        try:
            body, _meta = _render_for_branch(
                project_dir=project, base=base, head=head, llm_polish=llm_polish,
            )
        except Exception as exc:
            click.echo(f"trail blame pr update failed during render: {exc}", err=True)
            sys.exit(2)

        target = number
        if target is None:
            try:
                existing = find_pr_for_branch(project)
            except GhUnavailableError as exc:
                click.echo(str(exc), err=True)
                sys.exit(2)
            if existing is None:
                click.echo(
                    "No PR found for the current branch. Pass --number or "
                    "use `opentraces trail blame pr create`.",
                    err=True,
                )
                sys.exit(2)
            target = existing.number

        try:
            info = update_pr(
                project_dir=project, number=target, body=body,
            )
        except GhError as exc:
            click.echo(f"gh pr edit failed: {exc}", err=True)
            sys.exit(3)
        click.echo(f"Updated PR #{info.number}: {info.url}")


