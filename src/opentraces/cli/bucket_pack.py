"""``bucket pack`` / ``bucket unpack``: lossless superseded-record packing."""

from __future__ import annotations

import sys

import click

from ._envelope import envelope
from ._help import OpentracesCommand
from ._options import dump_json as _dump_json


def _emit(schema: str, result: dict, as_json: bool, title: str) -> None:
    ok = bool(result.get("ok", True))
    fields = {k: v for k, v in result.items() if k not in {"schema_version", "status"}}
    payload = envelope(schema, status="ok" if ok else "error", **fields)
    if as_json:
        click.echo(_dump_json(payload))
    else:
        click.echo(f"{title} (dry_run={result.get('dry_run')}):")
        for key, value in result.items():
            if key not in {"schema_version", "errors", "ok", "dry_run"}:
                click.echo(f"  {key}: {value}")
        for err in result.get("errors") or []:
            click.echo(f"  error: {err['path']}: {err['detail']}", err=True)
    if not ok:
        sys.exit(3)


@click.command("pack", cls=OpentracesCommand, hidden=True)
@click.option("--dry-run", "dry_run", is_flag=True, help="Report what would be packed without writing.")
@click.option(
    "--min-age",
    "min_age_seconds",
    type=int,
    default=3600,
    show_default=True,
    help="Skip superseded records younger than this many seconds.",
)
@click.option("--project", "project_slug", metavar="SLUG", default=None, help="Scope to one project slug.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_pack_cmd(dry_run: bool, min_age_seconds: int, project_slug: str | None, as_json: bool) -> None:
    """Pack superseded TraceRecord objects into one archive per trace.

    Every re-capture of a growing session leaves the previous full record on
    disk. ``bucket pack`` moves those superseded records into
    ``objects/traces/v1/<project>/<trace>/superseded.otpack`` (compressed tar,
    byte-exact members, sha256-checked before each loose file is removed).
    Needs a codec: ``pip install 'opentraces[pack]'`` or Python >= 3.14.
    The current record stays loose. ``bucket unpack`` reverses it.

    JSON output: counts and ``bytes_before``/``bytes_after`` of
    ``objects/traces/v1``.
    """
    from ..core.bucket_pack_codecs import CodecUnavailable
    from ..core.bucket_trace_pack import pack_superseded_trace_records

    try:
        result = pack_superseded_trace_records(
            dry_run=dry_run,
            min_age_seconds=max(0, min_age_seconds),
            project_slug=project_slug,
        )
    except CodecUnavailable as exc:
        raise click.ClickException(str(exc)) from exc
    _emit("opentraces.bucket.pack.v1", result, as_json, "Bucket pack")


@click.command("unpack", cls=OpentracesCommand, hidden=True)
@click.option("--dry-run", "dry_run", is_flag=True, help="Report what would be restored without writing.")
@click.option("--project", "project_slug", metavar="SLUG", default=None, help="Scope to one project slug.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_unpack_cmd(dry_run: bool, project_slug: str | None, as_json: bool) -> None:
    """Restore packed superseded TraceRecord objects as loose files, byte-exact."""
    from ..core.bucket_trace_pack import unpack_superseded_trace_records

    result = unpack_superseded_trace_records(dry_run=dry_run, project_slug=project_slug)
    _emit("opentraces.bucket.unpack.v1", result, as_json, "Bucket unpack")
