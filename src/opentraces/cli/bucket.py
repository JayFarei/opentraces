"""CLI troubleshooting commands for the local OpenTraces bucket."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup
from ._options import dump_json as _dump_json
from ._progress import build_cli_progress, progress_option
from ._security_flags import (
    BUCKET_SECURITY_POLICIES,
    SECURITY_TOOL_NAMES,
    apply_bucket_security_policy,
    apply_security_tool_flag_changes,
    security_tool_change_payload,
)
from ..core.config import load_config, save_config


@click.group("bucket", cls=OpentracesGroup)
def bucket_group() -> None:
    """Inspect and troubleshoot the local trace bucket."""


@bucket_group.group("security", cls=OpentracesGroup)
def bucket_security_group() -> None:
    """Inspect, configure, and apply the private bucket security filter.

    The filter must run on a record before it is remote-sync eligible:
    ``status`` inspects posture, ``run`` applies the configured filter to
    existing records, and ``policy`` configures which tools are enabled.
    """


@bucket_security_group.command("status", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_security_status_cmd(as_json: bool) -> None:
    """Show bucket security posture and the exact remediation, if any."""
    from ..core.bucket_security import bucket_security_overview

    cfg = load_config()
    overview = bucket_security_overview(cfg)
    policy = security_tool_change_payload(
        cfg, scope="bucket", changes={"enabled": [], "disabled": []}
    )["security"]
    payload = {"status": "ok", "security": {**overview, "policy": policy.get("policy")}}
    if as_json:
        click.echo(_dump_json(payload))
        return

    click.echo(f"Bucket security policy: {policy.get('policy', 'custom')}")
    enabled_tools = policy.get("enabled") or []
    click.echo("  configured tools: " + (", ".join(enabled_tools) if enabled_tools else "none"))
    click.echo(f"  total records:    {overview['total']}")
    click.echo(f"  filtered:         {overview['filtered']}")
    click.echo(f"  unfiltered:       {overview['unfiltered']}")
    click.echo(f"  version stale:    {overview['stale']}")
    remediation = overview.get("remediation")
    if remediation:
        click.echo(f"  remediation:      {remediation['reason']}")
        for step in remediation.get("next_steps") or []:
            click.echo(f"    next: {step}")
    else:
        click.echo("  remote-sync eligible: yes")


@bucket_security_group.command("run", cls=OpentracesCommand)
@click.option("--all", "run_all", is_flag=True, help="Filter every bucket record.")
@click.option("--trace", "trace_id", default=None, help="Filter one trace by id.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_security_run_cmd(run_all: bool, trace_id: str | None, as_json: bool) -> None:
    """Apply the configured bucket security filter to existing records."""
    from ..core.bucket_security import apply_bucket_security_filter

    if bool(run_all) == bool(trace_id):
        click.echo("Use exactly one of --all or --trace <id>.", err=True)
        sys.exit(2)

    cfg = load_config()
    summary = apply_bucket_security_filter(trace_id=trace_id, cfg=cfg)
    payload = {"status": summary.get("status", "ok"), "security_run": summary}
    if as_json:
        click.echo(_dump_json(payload))
        if summary.get("status") in {"not_found", "not_configured"}:
            sys.exit(2)
        return

    status = summary.get("status")
    if status == "not_found":
        click.echo(f"Trace not found in bucket: {trace_id}", err=True)
        sys.exit(2)
    if status == "not_configured":
        click.echo("Bucket security filter is not configured; nothing applied.")
        for step in (summary.get("remediation") or {}).get("next_steps") or []:
            click.echo(f"  next: {step}")
        sys.exit(2)
    click.echo("Bucket security filter applied:")
    click.echo(f"  scanned:          {summary['total']}")
    click.echo(f"  newly filtered:   {summary['filtered']}")
    click.echo(f"  already filtered: {summary['already_filtered']}")
    click.echo(f"  still blocked:    {summary['still_blocked']}")
    click.echo(f"  failed:           {summary['failed']}")
    if summary["still_blocked"]:
        click.echo(
            "  note: still-blocked records ran the filter but remain ineligible "
            "(e.g. privacy tier 'off'); inspect with 'opentraces security tools list'.",
            err=True,
        )


@bucket_security_group.command("policy", cls=OpentracesCommand)
@click.option(
    "--policy",
    type=click.Choice(tuple(BUCKET_SECURITY_POLICIES)),
    default=None,
    help="Set an exact bucket security policy.",
)
@click.option(
    "--tool",
    "tools",
    multiple=True,
    type=click.Choice(SECURITY_TOOL_NAMES),
    help="Security tool to enable or disable for bucket capture/sync.",
)
@click.option("--enable", is_flag=True, help="Enable every --tool.")
@click.option("--disable", is_flag=True, help="Disable every --tool.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_security_policy_cmd(
    policy: str | None,
    tools: tuple[str, ...],
    enable: bool,
    disable: bool,
    as_json: bool,
) -> None:
    """Inspect or change which security tools the bucket filter uses."""
    cfg = load_config()
    try:
        if enable and disable:
            raise ValueError("Use either --enable or --disable, not both")
        if policy and tools:
            raise ValueError("Use either --policy or --tool, not both")
        if policy and (enable or disable):
            raise ValueError("--policy already sets the policy; do not pass --enable/--disable")
        if tools and not (enable or disable):
            raise ValueError("--tool requires --enable or --disable")
        if not tools and (enable or disable):
            raise ValueError("--enable/--disable require at least one --tool")

        if policy:
            changes = apply_bucket_security_policy(cfg, policy)
            save_config(cfg)
        elif tools:
            changes = apply_security_tool_flag_changes(
                cfg,
                enable=tools if enable else (),
                disable=tools if disable else (),
            )
            save_config(cfg)
        else:
            changes = {"enabled": [], "disabled": []}
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)

    payload = security_tool_change_payload(cfg, scope="bucket", changes=changes)
    if as_json:
        click.echo(_dump_json(payload))
        return

    security = payload["security"]
    click.echo(f"Bucket security policy: {security.get('policy', 'custom')}")
    enabled_tools = security.get("enabled") or []
    click.echo("  enabled: " + (", ".join(enabled_tools) if enabled_tools else "none"))
    if changes["enabled"]:
        click.echo("  changed on: " + ", ".join(changes["enabled"]))
    if changes["disabled"]:
        click.echo("  changed off: " + ", ".join(changes["disabled"]))
        # Bucket security flags are machine-global (shared with capture-time
        # sanitization), so a policy can turn off tools enabled for other uses.
        click.echo(
            "  warning: these tools are now OFF machine-wide (bucket security "
            "shares global config); re-enable with 'bucket security policy --tool "
            "<name> --enable' if other workflows need them.",
            err=True,
        )


@bucket_group.command("status", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@progress_option
def bucket_status_cmd(as_json: bool, progress_mode: str) -> None:
    """Show local bucket health, sync eligibility, and trail freshness."""
    from ..core.bucket_store import bucket_status_fast

    # Issue #55 — `bucket status` is a pure read: no manifest.json write, no
    # per-trace envelope materialization. Self-heal is explicit via
    # `bucket manifest --heal` / `bucket repair`.
    #
    # Plan 087 — size-independent read. The steady-state path reads the persisted
    # manifest O(1) and aggregates sync/security counts from the per-row status
    # accelerator with a freshness stamp, replacing the O(N) per-TraceRecord scan
    # that ran for minutes on a large bucket. The scan survives only as the
    # fallback for a not-yet-built bucket (absent/error). The progress reporter
    # covers that fallback; the sink is stderr-only and auto-quiet in CI / non-TTY
    # so the `--json` stdout payload stays a single clean object.
    reporter = build_cli_progress("bucket status", progress_mode)
    try:
        payload = bucket_status_fast(progress=reporter)
    finally:
        reporter.done()
    if as_json:
        click.echo(_dump_json(payload))
        return
    bucket = payload["bucket"]
    config = payload.get("config") or {}
    remote_config = config.get("remote") or {}
    trace_records = bucket.get("trace_records") or {}
    trail = bucket.get("trail") or {}
    raw_sources = bucket.get("raw_sources") or {}
    trail_events = bucket.get("trail_events") or {}
    sync = bucket.get("sync") or {}
    click.echo(f"Bucket: {bucket.get('root')}")
    click.echo(f"  storage:    {config.get('storage', 'local')}")
    if remote_config.get("enabled"):
        click.echo(f"  remote:     {remote_config.get('url') or 'configured'}")
        click.echo(f"  sync policy: {remote_config.get('sync_policy', 'daemon')}")
    _trace_count = bucket.get("trace_count")
    if _trace_count is None:
        _trace_count = trace_records.get("object_count")
    click.echo(f"  traces:     {_trace_count if _trace_count is not None else 'unavailable'}")
    click.echo(f"  raw sources: {raw_sources.get('object_count', 0)}")
    click.echo(f"  trail events: {trail_events.get('event_count', 0)}")
    click.echo(f"  syncable:   {trace_records.get('syncable_count', 0)}")
    click.echo(f"  stale sec:  {trace_records.get('security_stale_count', 0)}")
    click.echo(f"  unfiltered: {trace_records.get('unfiltered_count', 0)}")
    click.echo(f"  last ingest: {trace_records.get('last_write_at') or 'never'}")
    click.echo(f"  trail sync: {trail.get('last_projection_sync_at') or 'never'}")
    click.echo(f"  trail stale: {trail.get('stale_count', 0)}")
    click.echo(
        "  remote eligible: "
        + ("yes" if sync.get("eligible") else "no")
    )
    for reason in sync.get("blocked_reasons") or []:
        click.echo(f"    blocked: {reason}")
    freshness = payload.get("freshness") or {}
    if freshness.get("stale"):
        reasons = ", ".join(freshness.get("reasons") or []) or "stale"
        click.echo(f"  freshness:  STALE ({reasons})")
        hint = freshness.get("rebuild_recommended")
        if hint:
            click.echo(f"    refresh with: {hint}")


@bucket_group.command("manifest", cls=OpentracesCommand)
@click.option(
    "--heal",
    is_flag=True,
    help=(
        "Persist manifest.json and materialize any missing per-trace "
        "envelopes (self-heal). Without --heal this is a side-effect-free read."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_manifest_cmd(heal: bool, as_json: bool) -> None:
    """Print the local bucket manifest (read-only; pass --heal to materialize)."""
    from ..core.bucket_store import bucket_manifest, bucket_manifest_path

    # Issue #55 — default is a pure read: compute + print the manifest (incl.
    # an in-memory reconcile of record-only orphans) without writing any byte
    # under the bucket. `--heal` restores the materializing behavior (write
    # manifest.json + per-trace envelopes), idempotent on a no-op.
    manifest = bucket_manifest(write=heal, heal=heal, include_objects=False)
    if as_json:
        click.echo(_dump_json({"status": "ok", "manifest": manifest}))
        return
    click.echo(f"Bucket manifest: {bucket_manifest_path()}")
    click.echo(f"  digest: {manifest.get('digest')}")


@bucket_group.group("remote", cls=OpentracesGroup)
def bucket_remote_group() -> None:
    """Manage the configured private bucket remote."""


@bucket_remote_group.command("status", cls=OpentracesCommand)
@click.option(
    "--root",
    "remote_root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Fake remote root override. Defaults to configured bucket remote.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_remote_status_cmd(remote_root: Path | None, as_json: bool) -> None:
    """Compare local bucket digest with the configured private remote."""
    from ..core.bucket_remote import remote_status

    payload = {"status": "ok", "remote": remote_status(fake_root=remote_root)}
    if as_json:
        click.echo(_dump_json(payload))
        return
    remote = payload["remote"]
    click.echo(f"Bucket remote: {remote.get('state')}")
    if remote.get("remote_root"):
        click.echo(f"  root: {remote.get('remote_root')}")
    if remote.get("repo_id"):
        click.echo(f"  repo: {remote.get('repo_id')}")
    if remote.get("local_digest"):
        click.echo(f"  local:  {remote.get('local_digest')}")
    if remote.get("remote_digest"):
        click.echo(f"  remote: {remote.get('remote_digest')}")
    if remote.get("advice"):
        click.echo(f"  advice: {remote.get('advice')}")
    _echo_security_gate(remote.get("security_gate"))


def _echo_security_gate(gate: dict | None) -> None:
    """Render the additive security-gate block in human mode (issue #94)."""
    if not gate:
        return
    if gate.get("state") == "unknown":
        click.echo("  security gate: unknown (no persisted bucket manifest)")
    else:
        click.echo(
            "  security gate: "
            f"{'eligible' if gate.get('eligible') else 'blocked'} "
            f"(unfiltered={gate.get('unfiltered_count')}, "
            f"stale={gate.get('security_stale_count')})"
        )
    reasons = gate.get("blocking_reasons") or []
    if reasons:
        click.echo(f"    blocking: {', '.join(reasons)}")
    for command in gate.get("advice") or []:
        click.echo(f"    -> {command}")


@bucket_remote_group.command("diff", cls=OpentracesCommand)
@click.option(
    "--root",
    "remote_root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Fake remote root override. Defaults to configured bucket remote.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_remote_diff_cmd(remote_root: Path | None, as_json: bool) -> None:
    """Compare local and remote bucket manifests."""
    from ..core.bucket_remote import remote_diff

    remote = remote_diff(fake_root=remote_root)
    payload = {"status": "ok", "remote": remote}
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo(f"Bucket remote diff: {remote.get('state')}")
    click.echo(f"  different: {remote.get('different')}")


@bucket_remote_group.command("push", cls=OpentracesCommand)
@click.option(
    "--root",
    "remote_root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Fake remote root override. Defaults to configured bucket remote.",
)
@click.option("--force", is_flag=True, help="Overwrite a remote-ahead or diverged bucket.")
@click.option(
    "--unsafe-push",
    "unsafe_push",
    is_flag=True,
    hidden=True,
    default=False,
    help=(
        "Test-only: include Context Tree blobs/heads in the push even when "
        "the substrate gate is ineligible. Plan 079 round-trip scaffolding."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_remote_push_cmd(
    remote_root: Path | None, force: bool, unsafe_push: bool, as_json: bool
) -> None:
    """Mirror the local bucket into the configured private remote."""
    from ..core.bucket_remote import BucketRemoteError, remote_push

    try:
        remote = remote_push(
            fake_root=remote_root, force=force, unsafe_push=unsafe_push
        )
    except (BucketRemoteError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "remote": remote}
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo(f"Bucket remote pushed: {remote.get('remote_root') or remote.get('repo_id')}")
    click.echo(
        f"  files: {remote.get('files_copied', remote.get('files_uploaded', 0))}"
    )


@bucket_remote_group.command("pull", cls=OpentracesCommand)
@click.option(
    "--root",
    "remote_root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Fake remote root override. Defaults to configured bucket remote.",
)
@click.option("--force", is_flag=True, help="Overwrite a local-ahead or diverged bucket.")
@click.option(
    "--eager",
    is_flag=True,
    help=(
        "Pull every referenced blob upfront (plan 080 §6). Default is "
        "selective: per-trace envelopes + event mirror; blobs stay lazy "
        "and are fetched on demand by ctx show / ctx diff."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_remote_pull_cmd(
    remote_root: Path | None,
    force: bool,
    eager: bool,
    as_json: bool,
) -> None:
    """Restore the local bucket from the configured private remote."""
    from ..core.bucket_remote import BucketRemoteError, remote_pull

    try:
        remote = remote_pull(fake_root=remote_root, force=force, eager=eager)
    except (BucketRemoteError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "remote": remote}
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo(f"Bucket remote pulled: {remote.get('remote_root') or remote.get('repo_id')}")
    click.echo(
        f"  files: {remote.get('files_copied', remote.get('files_downloaded', 0))}"
    )
    if eager:
        click.echo("  eager: true (all blobs fetched)")


@bucket_group.command("replay", cls=OpentracesCommand)
@click.option(
    "--repo",
    "repo",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Git repository to receive the bucket-exported Trace Trails ref.",
)
@click.option(
    "--repo-id",
    default=None,
    help="Bucket TrailEvents repo id. Required when the bucket has multiple exports.",
)
@click.option("--force", is_flag=True, help="Replace an existing differing Trace Trails ref.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_replay_cmd(
    repo: Path,
    repo_id: str | None,
    force: bool,
    as_json: bool,
) -> None:
    """Replay bucket-exported Trace Trails into a Git repository."""
    from ..core.bucket_store import restore_trail_events_to_repo

    try:
        replay = restore_trail_events_to_repo(repo, repo_id=repo_id, force=force)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "replay": replay}
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo(f"Bucket replay: {replay.get('state')}")
    click.echo(f"  repo: {replay.get('repo')}")
    click.echo(f"  repo id: {replay.get('repo_id')}")
    click.echo(f"  events: {replay.get('events_imported', 0)}")


# ---------------------------------------------------------------------------
# Plan 080 — bucket rebuild (extended to all substrates)
# ---------------------------------------------------------------------------


_REBUILD_SUBSTRATES = ("context-tree", "trail", "traces", "all")


@bucket_group.command("rebuild", cls=OpentracesCommand)
@click.option(
    "--substrate",
    "substrate",
    type=click.Choice(_REBUILD_SUBSTRATES),
    default="all",
    show_default=True,
    help=(
        "Substrate to rebuild from canonical state. "
        "'context-tree' rebuilds context projections, "
        "'trail' rebuilds Trail event mirrors, "
        "'traces' rebuilds per-trace envelopes, "
        "'all' rebuilds every substrate."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_rebuild_cmd(substrate: str, as_json: bool) -> None:
    """Idempotently rebuild a derived bucket projection from the event log.

    Per plan 080 §7, the rebuild verb spans every substrate that the bucket
    derives from the canonical Git event log + blob store. ``--substrate
    all`` (the default) iterates each substrate in dependency order
    (blobs/events first, then per-trace envelopes, then context-tree
    projections). The output envelope reports a per-substrate breakdown so
    callers can target a single substrate when triaging a stale projection.
    """

    from ..core.bucket_store import project_context_tree_to_bucket
    from ..core.config import get_project_dir, load_config, opted_in_projects

    cfg = load_config()
    project_paths = [Path(path) for path in opted_in_projects(cfg)]

    substrates_to_run: list[str]
    if substrate == "all":
        substrates_to_run = ["trail", "traces", "context-tree"]
    else:
        substrates_to_run = [substrate]

    rebuild_results: dict[str, dict] = {}

    for sub in substrates_to_run:
        if sub == "context-tree":
            traces_projected = 0
            blobs_written = 0
            heads_written = 0
            idempotent_noop = True
            per_project: list[dict] = []
            for project_path in project_paths:
                if not project_path.exists():
                    continue
                try:
                    project_slug = get_project_dir(project_path).name
                    result = project_context_tree_to_bucket(
                        project_path, project_slug=project_slug
                    )
                except Exception as exc:  # pragma: no cover
                    per_project.append({"project": str(project_path), "error": str(exc)})
                    continue
                traces_projected += int(result.get("traces_projected", 0) or 0)
                blobs_written += int(result.get("blobs_written", 0) or 0)
                heads_written += int(result.get("heads_written", 0) or 0)
                if not result.get("idempotent_noop", True):
                    idempotent_noop = False
                per_project.append({"project": str(project_path), **result})
            rebuild_results[sub] = {
                "traces_projected": traces_projected,
                "blobs_written": blobs_written,
                "heads_written": heads_written,
                "idempotent_noop": idempotent_noop,
                "per_project": per_project,
            }
        elif sub in ("trail", "traces"):
            # Plan 080 Phase B / B1: these are owned by the bucket_store
            # layout primitives. Call the B1 stub if present; raise a
            # clear ClickException otherwise so the surface contract is
            # discoverable.
            try:
                from ..core import bucket_store as _bs

                fn = getattr(_bs, f"rebuild_bucket_{sub.replace('-', '_')}", None)
                if fn is None:
                    raise click.ClickException(
                        f"Phase B Track B1 stub: rebuild_bucket_{sub.replace('-', '_')} not yet implemented"
                    )
                rebuild_results[sub] = dict(fn())
            except click.ClickException:
                raise
            except NotImplementedError as exc:
                raise click.ClickException(
                    f"Phase B Track B1 stub: not yet implemented ({exc})"
                ) from exc

    envelope: dict = {
        "status": "ok",
        "rebuild": {
            "substrate": substrate,
            "per_substrate": rebuild_results,
        },
    }
    # When a single substrate was requested keep the legacy flat shape too
    # so existing callers (rebuild --substrate context-tree) don't break.
    if substrate != "all" and substrate in rebuild_results:
        envelope["rebuild"].update(rebuild_results[substrate])
    if as_json:
        click.echo(_dump_json(envelope))
        return
    click.echo(f"Bucket rebuild: substrate={substrate}")
    for sub, result in rebuild_results.items():
        click.echo(f"  {sub}:")
        for key in (
            "traces_projected",
            "blobs_written",
            "heads_written",
            "idempotent_noop",
            "events_mirrored",
            "envelopes_written",
        ):
            if key in result:
                click.echo(f"    {key}: {result[key]}")


# ---------------------------------------------------------------------------
# Plan 080 — new bucket-management verbs (repair / verify / prune / prefetch)
# ---------------------------------------------------------------------------


@bucket_group.command("repair", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_repair_cmd(as_json: bool) -> None:
    """Re-project the full bucket from canonical (event log + blob store).

    Per plan 080 §20 Resolution G, ``bucket repair`` is the documented
    crash-recovery primitive: it regenerates per-trace envelopes and
    ``manifest.json`` from the canonical event log + blob store. The
    operation is idempotent — safe to re-run, never drops user data.
    """
    try:
        from ..core.bucket_store import bucket_repair
    except ImportError as exc:
        raise click.ClickException(
            f"Phase B Track B1 stub: bucket_repair not yet implemented ({exc})"
        ) from exc

    try:
        result = bucket_repair()
    except NotImplementedError as exc:
        raise click.ClickException(
            f"Phase B Track B1 stub: bucket_repair not yet implemented ({exc})"
        ) from exc

    payload = {"status": "ok", "repair": dict(result)}
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo("Bucket repair:")
    for key, value in result.items():
        click.echo(f"  {key}: {value}")


@bucket_group.command("verify", cls=OpentracesCommand)
@click.option(
    "--sample",
    "sample_size",
    type=int,
    default=100,
    show_default=True,
    help="Number of blobs to sample for content-integrity check.",
)
@click.option(
    "--full",
    "as_full",
    is_flag=True,
    help="Verify every blob (bounded but slow). Overrides --sample.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_verify_cmd(sample_size: int, as_full: bool, as_json: bool) -> None:
    """Blob content integrity + dangling-ref detection.

    Per plan 080 §7 and §20 Resolution G, ``bucket verify`` recomputes
    each sampled blob's hash and compares it against its path, then walks
    every per-trace envelope's references and reports dangling pointers.
    Read-only: never mutates bucket state.

    JSON output: ``{ok, sampled, errors: [...]}``.
    """
    try:
        from ..core.bucket_store import bucket_verify
    except ImportError as exc:
        raise click.ClickException(
            f"Phase B Track B1 stub: bucket_verify not yet implemented ({exc})"
        ) from exc

    requested_sample = 0 if as_full else max(0, int(sample_size))
    try:
        result = bucket_verify(sample=requested_sample, full=as_full)
    except NotImplementedError as exc:
        raise click.ClickException(
            f"Phase B Track B1 stub: bucket_verify not yet implemented ({exc})"
        ) from exc

    result_dict = dict(result)
    ok = bool(result_dict.get("ok", True))
    errors = list(result_dict.get("errors") or [])
    sampled = int(result_dict.get("sampled", 0) or 0)

    envelope = {
        "status": "ok" if ok else "error",
        "ok": ok,
        "sampled": sampled,
        "errors": errors,
        "verify": result_dict,
    }
    if as_json:
        click.echo(_dump_json(envelope))
        if not ok:
            sys.exit(3)
        return
    click.echo(f"Bucket verify: ok={ok}")
    click.echo(f"  sampled: {sampled}")
    click.echo(f"  errors: {len(errors)}")
    for err in errors[:10]:
        click.echo(f"    - {err}")
    if len(errors) > 10:
        click.echo(f"    ... and {len(errors) - 10} more")
    if not ok:
        sys.exit(3)


@bucket_group.command("prune", cls=OpentracesCommand)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Report what would be deleted without removing anything.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_prune_cmd(dry_run: bool, as_json: bool) -> None:
    """Reachability-based orphan blob cleanup.

    Per plan 080 §20 Resolution G, ``bucket prune`` walks the per-trace
    envelopes + event log to build the reachable-blob set, then deletes
    blobs not in that set. It NEVER touches events or ``trace.json``
    files — only orphan blobs and atomic-write tempfile leftovers.

    JSON output: ``{would_delete, deleted}``.
    """
    try:
        from ..core.bucket_store import bucket_prune
    except ImportError as exc:
        raise click.ClickException(
            f"Phase B Track B1 stub: bucket_prune not yet implemented ({exc})"
        ) from exc

    try:
        result = bucket_prune(dry_run=dry_run)
    except NotImplementedError as exc:
        raise click.ClickException(
            f"Phase B Track B1 stub: bucket_prune not yet implemented ({exc})"
        ) from exc

    result_dict = dict(result)
    would_delete = int(result_dict.get("would_delete", 0) or 0)
    deleted = int(result_dict.get("deleted", 0) or 0)
    envelope = {
        "status": "ok",
        "would_delete": would_delete,
        "deleted": deleted,
        "prune": result_dict,
    }
    if as_json:
        click.echo(_dump_json(envelope))
        return
    click.echo(f"Bucket prune (dry_run={dry_run}):")
    click.echo(f"  would_delete: {would_delete}")
    click.echo(f"  deleted: {deleted}")


@bucket_group.command("prefetch", cls=OpentracesCommand)
@click.argument("trace_id")
@click.option(
    "--remote",
    "remote",
    default=None,
    help="Remote HF repo override (defaults to the configured bucket remote).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_prefetch_cmd(trace_id: str, remote: str | None, as_json: bool) -> None:
    """Eager-pull one trace's blobs from the configured remote.

    Per plan 080 Soft Q-N, ``bucket prefetch <trace>`` writes into the
    local bucket directly (``bucket/blobs/v1/<project>/context/``).
    Mental model: "warm my bucket from remote." Use before ``ctx show``
    on a cold cache to avoid per-blob HTTP round-trips.
    """
    from ..core.bucket_remote import BucketRemoteError
    from ..core.bucket_store import bucket_prefetch

    try:
        result = bucket_prefetch(trace_id, remote=remote)
    except (FileNotFoundError, ValueError, BucketRemoteError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)

    result_dict = dict(result)
    envelope = {"status": "ok", "trace_id": trace_id, "prefetch": result_dict}
    if as_json:
        click.echo(_dump_json(envelope))
        return
    click.echo(f"Bucket prefetch: trace={trace_id}")
    for key, value in result_dict.items():
        click.echo(f"  {key}: {value}")
