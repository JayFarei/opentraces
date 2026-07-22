"""CLI troubleshooting commands for the local OpenTraces bucket."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ._envelope import envelope
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
from ..core.bucket_list import DEFAULT_LIMIT as _LIST_DEFAULT
from ..core.bucket_list import MAX_LIMIT as _LIST_MAX
from ..core.config import load_config, save_config


@click.group("bucket", cls=OpentracesGroup)
def bucket_group() -> None:
    """Inspect and troubleshoot the local trace bucket."""


@bucket_group.group("security", cls=OpentracesGroup, hidden=True)
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


@bucket_group.command("status", cls=OpentracesCommand, hidden=True)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@progress_option
def bucket_status_cmd(as_json: bool, progress_mode: str) -> None:
    """Lower-level bucket health readout (hidden; use ``opentraces status``).

    Issue #162 — the fleet safety dashboard now lives at the top-level
    ``opentraces status`` (the ``opentraces.bucket.status.v1`` contract).
    This command stays callable (hidden != removed) as the lower-level
    bucket-HEALTH readout that ``status``'s one-line verdict points to; its
    ``opentraces.bucket.health.v1`` envelope is byte-compatible with prior
    callers so nothing scripting it breaks.
    """
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
        # L5 uniform envelope (ADR-0007): promote {status, schema_version} to the
        # top level. The nested "bucket"/"config"/"freshness" blocks are unchanged,
        # so existing consumers reading payload["bucket"][...] are unaffected. This
        # is the bucket-HEALTH readout; the top-level `opentraces status` fleet
        # dashboard is the distinct opentraces.bucket.status.v1 contract.
        click.echo(_dump_json(envelope("opentraces.bucket.health.v1", **payload)))
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


@bucket_group.command("list", cls=OpentracesCommand)
@click.option("--unsynced", is_flag=True, help="Only traces not yet cleared for sync (a PROCESS state, not a content verdict).")
@click.option("--unfiltered", is_flag=True, help="Only scanned traces still holding unresolved redactable content.")
@click.option("--security-stale", "security_stale", is_flag=True, help="Only traces scanned by an older security version.")
@click.option("--unscanned", is_flag=True, help="Only traces whose security state is not yet known (the honest 'unknown' facet).")
@click.option("--project", "project", metavar="SLUG", default=None, help="Scope to one project slug.")
@click.option("--since", "since", metavar="WHEN", default=None, help="Only traces written at/after WHEN (ISO written_at); unknown-written_at rows are never dropped.")
@click.option("--count", "count_only", is_flag=True, help="Return only the matching count (ignores --limit); materializes no rows.")
@click.option("--limit", "limit", type=int, default=None, help=f"Max rows per page (default {_LIST_DEFAULT}, hard cap {_LIST_MAX}).")
@click.option("--cursor", "cursor", default=None, help="Opaque page cursor from a prior page's `cursor` field.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_list_cmd(
    unsynced: bool,
    unfiltered: bool,
    security_stale: bool,
    unscanned: bool,
    project: str | None,
    since: str | None,
    count_only: bool,
    limit: int | None,
    cursor: str | None,
    as_json: bool,
) -> None:
    """The per-trace inventory — bounded, paginated, hang-proof (issue #162).

    ``list`` is the read side ``bucket manifest`` never was: it reads the
    plan-087 per-row status accelerator ONCE and applies its predicates BEFORE
    projection, so a real fleet bucket returns a bounded page in an interactive
    budget instead of hanging. An ABSENT per-row status renders as an explicit
    ``unknown`` facet (never coerced to ``clean``); the envelope always carries
    ``unknown_status_count`` so a sparse-accelerator bucket is never mistaken
    for a scanned-clean one. ``list`` supersedes ``ctx list`` as the row surface.
    """
    from ..core.bucket_list import build_bucket_list

    payload = build_bucket_list(
        unsynced=unsynced,
        unfiltered=unfiltered,
        security_stale=security_stale,
        unscanned=unscanned,
        project=project,
        since=since,
        limit=_LIST_DEFAULT if limit is None else limit,
        cursor=cursor,
        count_only=count_only,
    )
    if as_json:
        click.echo(_dump_json(envelope(payload.pop("schema_version"), **payload)))
        return

    if count_only:
        click.echo(f"{payload['count']} trace(s) match "
                   f"({payload['unknown_status_count']} of unknown status).")
        return
    rows = payload["rows"]
    if not rows:
        click.echo("No traces match.")
    for row in rows:
        title = row.get("title") or "(untitled)"
        click.echo(
            f"  {row.get('trace_id')}  [{row.get('sync_state')}]  "
            f"{row.get('project_slug') or '-'}  {title}"
        )
    click.echo(
        f"  ({len(rows)} shown of {payload['total']} total; "
        f"{payload['unknown_status_count']} unknown status)"
    )
    if payload.get("cursor"):
        click.echo(f"  next page: --cursor {payload['cursor']}")


@bucket_group.command("manifest", cls=OpentracesCommand, hidden=True)
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
    """Print the local bucket manifest (hidden; use ``bucket list`` / ``bucket repair``).

    Issue #162 — the per-trace inventory read side moved to the bounded,
    accelerator-backed ``bucket list``; the ``--heal`` write side folded into
    ``bucket repair``. This command stays callable (hidden != removed) so its
    read path (already bounded from the #87 F0 hang cure) and ``--heal`` keep
    working for scripts pinned to them.
    """
    from ..core.bucket_store import (
        bucket_manifest,
        bucket_manifest_path,
        read_persisted_manifest_capped,
    )

    # #87 F0 hang cure — a read (no --heal) SERVES the persisted manifest.json
    # O(1) instead of recomputing it, exactly like ``bucket status`` /
    # ``ctx list`` already do. The full O(N) recompute (which re-scans every
    # trace record + raw source + trail/context substrate + a per-repo anchor
    # read) is kept ONLY for --heal or when no usable persisted manifest exists
    # (a fresh/empty world, or an unreadable/too-large file) — so small buckets
    # and the empty-world CLI sweep behave exactly as before. The served file is
    # the authoritative manifest a prior --heal / repair / push wrote; its
    # top-level keys are byte-compatible with the recomputed shape.
    manifest: dict[str, object] | None = None
    if not heal:
        state, persisted = read_persisted_manifest_capped()
        if state == "ok" and isinstance(persisted, dict):
            manifest = persisted
    if manifest is None:
        # Issue #55 — the recompute path is still a pure read when heal=False:
        # it reconciles record-only orphans in memory without writing any byte
        # under the bucket. `--heal` restores the materializing behavior.
        manifest = bucket_manifest(write=heal, heal=heal, include_objects=False)
    if as_json:
        click.echo(_dump_json({"status": "ok", "manifest": manifest}))
        return
    click.echo(f"Bucket manifest: {bucket_manifest_path()}")
    click.echo(f"  digest: {manifest.get('digest')}")


@bucket_group.group("remote", cls=OpentracesGroup, hidden=True)
def bucket_remote_group() -> None:
    """Manage the configured private bucket remote (hidden; use ``bucket sync``).

    Issue #162 — the canonical mover is ``bucket sync {push,pull,diff,status}``.
    This group stays callable (hidden != removed) so scripts pinned to
    ``bucket remote ...`` keep working byte-identically; each subcommand shares
    the exact command object mounted under ``bucket sync``.
    """


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

    payload = envelope(
        "opentraces.bucket.sync_status.v1", remote=remote_status(fake_root=remote_root)
    )
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
    payload = envelope("opentraces.bucket.sync_diff.v1", remote=remote)
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
    "--dry-run",
    "dry_run",
    is_flag=True,
    help=(
        "Compute the pushed/withheld egress partition WITHOUT egressing a "
        "single byte. Prints which traces are cleared for sync and which are "
        "withheld (and why), so the egress-safety decision is auditable first."
    ),
)
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
    remote_root: Path | None,
    force: bool,
    dry_run: bool,
    unsafe_push: bool,
    as_json: bool,
) -> None:
    """Mirror the local bucket into the configured private remote.

    Issue #162 — ``sync push`` egresses raw substrate, so it makes its
    withhold decision AUDITABLE. The envelope carries ``pushed[]`` (trace ids
    cleared for sync) and ``withheld[]`` (each ``{trace_id, reason, sub_reason}``).
    A trace is cleared ONLY when its accelerator row is positively
    ``syncable == true``; an absent row is withheld (conservative). The
    withhold reason is a PROCESS state (``not_cleared_for_sync``), never a
    content verdict. ``--dry-run`` computes the same partition without egressing.

    Egress safety (the load-bearing property): the real (non-dry-run) push
    REFUSES — ``status='refused'``, ``pushed=[]``, non-zero exit, ZERO bytes
    egressed — whenever the fresh push-time partition still withholds any
    trace. Enforcement runs against the SAME manifest the push egresses (the
    push recomputes it after re-scanning local stores), so the envelope can
    never claim a trace was withheld while its bytes actually left the machine.
    """
    from ..core.bucket_remote import BucketRemoteError, remote_push
    from ..core.bucket_sync import BucketPushWithheldError, sync_push_partition

    if dry_run:
        # Advisory preview from the PERSISTED accelerator (O(1), no recompute,
        # no egress). The real push enforces on a freshly-recomputed manifest;
        # both use the same partition function, so they agree on a current bucket.
        partition = sync_push_partition()
        payload = envelope(
            "opentraces.bucket.sync_push.v1",
            dry_run=True,
            pushed=partition["pushed"],
            withheld=partition["withheld"],
        )
        if as_json:
            click.echo(_dump_json(payload))
            return
        click.echo("Bucket sync push (dry run — nothing egressed):")
        click.echo(f"  cleared for sync: {len(partition['pushed'])}")
        click.echo(f"  withheld:         {len(partition['withheld'])} (not_cleared_for_sync)")
        return

    try:
        remote = remote_push(
            fake_root=remote_root, force=force, unsafe_push=unsafe_push
        )
    except BucketPushWithheldError as exc:
        # Refusal: nothing egressed. Emit the auditable partition so the caller
        # knows exactly which traces are not cleared and how to clear them.
        withheld = exc.partition.get("withheld") or []
        payload = envelope(
            "opentraces.bucket.sync_push.v1",
            status="refused",
            dry_run=False,
            pushed=[],
            withheld=withheld,
            next_steps=[
                "opentraces bucket security run --all",
                "opentraces bucket sync push --dry-run  # re-audit the partition",
            ],
        )
        if as_json:
            click.echo(_dump_json(payload))
        else:
            click.echo(
                f"Bucket sync push REFUSED — {len(withheld)} trace(s) not "
                "cleared for sync; nothing egressed.",
                err=True,
            )
            click.echo("  run 'opentraces bucket security run --all' to clear them", err=True)
        sys.exit(2)
    except (BucketRemoteError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    except Exception as exc:  # noqa: BLE001 - HF/network errors must not traceback
        from opentraces import cli as root_cli
        root_cli._fail_hf_or_reraise(exc, "")
    # The push cleared: report the FRESH push-time partition (withheld is empty
    # by construction — the gate refused otherwise), never the stale pre-read.
    partition = remote.get("push_partition") or {"pushed": [], "withheld": []}
    payload = envelope(
        "opentraces.bucket.sync_push.v1",
        dry_run=False,
        remote=remote,
        pushed=partition["pushed"],
        withheld=partition["withheld"],
    )
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo(f"Bucket remote pushed: {remote.get('remote_root') or remote.get('repo_id')}")
    click.echo(
        f"  files: {remote.get('files_copied', remote.get('files_uploaded', 0))}"
    )
    click.echo(f"  withheld: {len(partition['withheld'])} (not_cleared_for_sync)")


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
    except Exception as exc:  # noqa: BLE001 - HF/network errors must not traceback
        from opentraces import cli as root_cli
        root_cli._fail_hf_or_reraise(exc, "")
    payload = envelope("opentraces.bucket.sync_pull.v1", remote=remote)
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo(f"Bucket remote pulled: {remote.get('remote_root') or remote.get('repo_id')}")
    click.echo(
        f"  files: {remote.get('files_copied', remote.get('files_downloaded', 0))}"
    )
    if eager:
        click.echo("  eager: true (all blobs fetched)")


@bucket_group.command("replay", cls=OpentracesCommand, hidden=True)
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


@bucket_group.command("rebuild", cls=OpentracesCommand, hidden=True)
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

    rebuild_payload: dict = {
        "status": "ok",
        "rebuild": {
            "substrate": substrate,
            "per_substrate": rebuild_results,
        },
    }
    # When a single substrate was requested keep the legacy flat shape too
    # so existing callers (rebuild --substrate context-tree) don't break.
    if substrate != "all" and substrate in rebuild_results:
        rebuild_payload["rebuild"].update(rebuild_results[substrate])
    if as_json:
        click.echo(_dump_json(rebuild_payload))
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
@click.option(
    "--bucket-root",
    "bucket_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Repair a specific bucket layout instead of the live "
        "~/.opentraces/bucket. When given (a COPY), repair runs ONLY the "
        "canonical anchor re-derive over the envelopes present there — never "
        "the live bucket."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_repair_cmd(bucket_root: Path | None, as_json: bool) -> None:
    """Re-project the full bucket from canonical (event log + blob store).

    Per plan 080 §20 Resolution G, ``bucket repair`` is the documented
    crash-recovery primitive: it regenerates per-trace envelopes and
    ``manifest.json`` from the canonical event log + blob store. The
    operation is idempotent — safe to re-run, never drops user data.

    Epic #169 (B-attr): repair also re-derives ``trace.json`` patch anchors and
    the manifest ``anchored_count`` from the canonical ``git_anchor_created``
    events (the single source of truth). ``--bucket-root <dir>`` targets a
    bucket COPY so the re-derive can be verified without mutating the live
    bucket.

    Issue #162 — this is where ``bucket manifest --heal`` folds: ``repair`` is
    the canonical read-model rebuild (it persists ``manifest.json`` + the
    per-trace envelopes from canonical state), so ``status``'s freshness
    remediation points here, never at the demoted ``manifest --heal``.
    """
    try:
        from ..core.bucket_store import bucket_repair
    except ImportError as exc:
        raise click.ClickException(
            f"Phase B Track B1 stub: bucket_repair not yet implemented ({exc})"
        ) from exc

    try:
        result = bucket_repair(bucket_root=bucket_root)
    except NotImplementedError as exc:
        raise click.ClickException(
            f"Phase B Track B1 stub: bucket_repair not yet implemented ({exc})"
        ) from exc

    payload = envelope("opentraces.bucket.repair.v1", repair=dict(result))
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo("Bucket repair:")
    for key, value in result.items():
        click.echo(f"  {key}: {value}")


@bucket_group.command("reclaim", cls=OpentracesCommand)
@click.option(
    "--repo",
    "repo",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Git repository to scan (defaults to the current directory).",
)
@click.option(
    "--apply",
    "apply",
    is_flag=True,
    help="Actually remove the listed cruft. Without it, this is print-only.",
)
@click.option(
    "--anchor-search",
    "anchor_search",
    is_flag=True,
    help=(
        "EXPERIMENTAL, opt-in: also compact fat anchor-search history "
        "(issue #358). Off by default — this pass is O(corpus) and currently "
        "slow on large real buckets. See issue #362."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_reclaim_cmd(
    repo: Path | None, apply: bool, anchor_search: bool, as_json: bool
) -> None:
    """Reclaim leaked Trace Trails cruft (and, opt-in, fat anchor-search history).

    DRY-RUN BY DEFAULT (a plain run mutates nothing; ``--apply`` performs the
    writes):

    \b
    1. Leaked ``.git/**/opentraces/`` cruft (``--repo``-scoped, defaults to
       the current directory): leaked ``*.tmp`` files and orphan/duplicate
       accelerator pickles (per-worktree ``event_log_snapshot.pkl`` /
       ``event_index`` copies left by the pre-#169-C duplication bug), with
       per-candidate byte counts. NEVER touches the live
       ``event_log_snapshot.pkl``, the live ``event_index/base.pkl``, the
       canonical event ref, or anything under the bucket.
    2. Fat anchor-search history (issue #358) — ONLY with ``--anchor-search``.
       EXPERIMENTAL and opt-in: legacy per-patch and v2-fat
       ``git_anchor_search_completed`` events are the ~26 GB driver behind an
       oversized bucket, but this compaction pass is O(corpus) and currently
       too slow to run by default on large real buckets (issue #362). When
       enabled it compacts each affected project's canonical event chain to
       the v3-compact shape (an atomic ``update-ref`` swap), reconciles the
       bucket's events mirror, and regenerates only the trail companions a
       fat/legacy search event touched. A killed run is journaled and
       RESUMABLE — re-running finishes the interrupted work — but is NOT
       guaranteed readable in between: reads of an affected project (e.g.
       ``trail`` companions) can error until the re-run completes.
    """
    from ..core.bucket_reclaim import reclaim_repo

    target = Path(repo) if repo is not None else Path.cwd()
    cruft_report = reclaim_repo(target, apply=apply)
    data = cruft_report.as_dict()
    search_report = None
    if anchor_search:
        from ..core.bucket_reclaim_search import reclaim_anchor_search

        search_report = reclaim_anchor_search(apply=apply)
        data["anchor_search"] = search_report.as_dict()
    else:
        data["anchor_search"] = {
            "skipped": True,
            "reason": "experimental; pass --anchor-search to run (issue #358/#362)",
        }
    payload = envelope("opentraces.bucket.reclaim.v1", reclaim=data)
    if as_json:
        click.echo(_dump_json(payload))
        return

    click.echo(f"Bucket reclaim (apply={apply}):")
    click.echo(f"  repo: {data['repo']}")
    click.echo(f"  candidates: {data['candidate_count']} ({data['total_bytes']} bytes)")
    for cand in data["candidates"]:
        click.echo(f"    - [{cand['category']}] {cand['bytes']:>10} B  {cand['path']}")
    if apply:
        click.echo(f"  removed: {data['removed_count']} ({data['removed_bytes']} bytes)")
    else:
        click.echo("  (dry run — nothing deleted; pass --apply to reclaim)")
    for err in data["errors"]:
        click.echo(f"  error: {err}", err=True)

    if not anchor_search:
        click.echo("")
        click.echo(
            "Anchor-search compaction: skipped (experimental; pass "
            "--anchor-search to compact fat #358 history — slow on large buckets)"
        )
        return

    search_data = data["anchor_search"]
    click.echo("")
    click.echo(f"Anchor-search compaction (apply={apply}):")
    for proj in search_data["projects"]:
        suffix = f" — {proj['reason']}" if proj["reason"] else ""
        click.echo(f"    - {proj['project_slug']} [{proj['action']}]{suffix}")
        if proj["action"] in ("compacted", "mirror_only_compacted"):
            click.echo(
                f"        events: {proj['events_before']} -> {proj['events_after']}"
                f"  (legacy_collapsed={proj['legacy_events_collapsed']},"
                f" fat_rewritten={proj['fat_summaries_rewritten']})"
            )
            click.echo(
                f"        ref_rewritten={proj['ref_rewritten']}"
                f"  mirror_rewritten={proj['mirror_rewritten']}"
                f"  companions={proj['companion_count']}"
            )
            click.echo(f"        bytes: {proj['bytes_before']} -> {proj['bytes_after']}")
            if proj["ref_bytes_reclaimable_after_gc"]:
                click.echo(
                    f"        ref: {proj['ref_bytes_before']} -> {proj['ref_bytes_after']}"
                    f"  ({proj['ref_bytes_reclaimable_after_gc']} B still on disk in .git"
                    " until 'git gc' runs — not counted above)"
                )
    click.echo(f"  total bytes reclaimed: {search_data['bytes_reclaimed']}")
    if not apply:
        click.echo("  (dry run — nothing rewritten; pass --apply to reclaim)")
    for err in search_data["errors"]:
        click.echo(f"  error: {err}", err=True)


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

    payload = envelope(
        "opentraces.bucket.verify.v1",
        status="ok" if ok else "error",
        ok=ok,
        sampled=sampled,
        errors=errors,
        verify=result_dict,
    )
    if as_json:
        click.echo(_dump_json(payload))
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


@bucket_group.command("prune", cls=OpentracesCommand, hidden=True)
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
    prune_payload = {
        "status": "ok",
        "would_delete": would_delete,
        "deleted": deleted,
        "prune": result_dict,
    }
    if as_json:
        click.echo(_dump_json(prune_payload))
        return
    click.echo(f"Bucket prune (dry_run={dry_run}):")
    click.echo(f"  would_delete: {would_delete}")
    click.echo(f"  deleted: {deleted}")


@bucket_group.command("prefetch", cls=OpentracesCommand, hidden=True)
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
    prefetch_payload = {"status": "ok", "trace_id": trace_id, "prefetch": result_dict}
    if as_json:
        click.echo(_dump_json(prefetch_payload))
        return
    click.echo(f"Bucket prefetch: trace={trace_id}")
    for key, value in result_dict.items():
        click.echo(f"  {key}: {value}")


# ---------------------------------------------------------------------------
# Issue #162 — `bucket sync` is the canonical mover (was the `bucket remote`
# subgroup). Its FOUR EQUAL subverbs share the exact command objects mounted
# under the now-hidden `bucket remote`, so `diff` / `status` are first-class
# peers of `push` / `pull` and the two paths are byte-identical.
# ---------------------------------------------------------------------------
@bucket_group.group("sync", cls=OpentracesGroup)
def bucket_sync_group() -> None:
    """Mirror the raw bucket substrate against the configured remote.

    ``sync`` is the bidirectional mirror of the WHOLE bucket (traces, blobs,
    events, manifest) — distinct from ``dataset publish`` (the gated one-way
    egress of reviewed rows). ``diff`` / ``status`` inspect before you gate;
    ``push`` egresses raw substrate and withholds every trace not yet cleared
    for sync (see ``sync push --dry-run``); ``pull`` restores.
    """


# The same command objects the hidden `bucket remote` group holds — mounted
# here as the canonical, visible surface.
bucket_sync_group.add_command(bucket_remote_push_cmd, name="push")
bucket_sync_group.add_command(bucket_remote_pull_cmd, name="pull")
bucket_sync_group.add_command(bucket_remote_diff_cmd, name="diff")
bucket_sync_group.add_command(bucket_remote_status_cmd, name="status")


@bucket_group.command(
    "connect",
    cls=OpentracesCommand,
    examples=[
        "opentraces bucket connect",
        "opentraces bucket connect --local-only",
        "opentraces bucket connect --repo me/opentraces-bucket",
    ],
    see_also=[
        ("opentraces bucket sync push", "mirror the local bucket to the remote"),
        ("opentraces auth login", "authenticate before using the default HF target"),
    ],
)
@click.option(
    "--remote/--local-only",
    "remote_enabled",
    default=True,
    show_default=True,
    help="Configure private remote bucket sync, or opt out to local-only.",
)
@click.option(
    "--provider",
    type=click.Choice(["huggingface", "fake"]),
    default="huggingface",
    show_default=True,
    help="Remote bucket provider.",
)
@click.option(
    "--repo",
    default=None,
    help="Private HuggingFace bucket repo id. Defaults to <user>/opentraces-bucket.",
)
@click.option(
    "--fake-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Local directory used by the fake bucket remote harness.",
)
@click.option(
    "--sync-policy",
    type=click.Choice(["daemon", "manual"]),
    default="daemon",
    show_default=True,
    help="How the private remote bucket should be kept current.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.pass_context
def bucket_connect_cmd(
    ctx: click.Context,
    remote_enabled: bool,
    provider: str,
    repo: str | None,
    fake_root: Path | None,
    sync_policy: str,
    as_json: bool,
) -> None:
    """Configure the private bucket remote target (the rename of ``setup bucket``).

    Issue #162 — ``connect`` binds the remote (and needs HF auth for the
    default HuggingFace target); ``sync`` moves data. They stay distinct verbs.
    This is a thin wrapper over the same configure logic ``setup bucket`` runs;
    ``setup bucket`` stays callable (hidden).
    """
    from .setup_bucket import setup_bucket_cmd

    # Forward to the shared configure logic; ctx.invoke fills the remaining
    # `setup bucket` params (push/pull-now, security-tool toggles, migrate)
    # with their defaults — connect is the lean "bind the target" front door.
    ctx.invoke(
        setup_bucket_cmd,
        remote_enabled=remote_enabled,
        provider=provider,
        repo=repo,
        fake_root=fake_root,
        sync_policy=sync_policy,
        as_json=as_json,
    )
