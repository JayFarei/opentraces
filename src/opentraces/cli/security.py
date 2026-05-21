"""``opentraces security`` CLI group.

Exposes the sanitization pipeline to shells and language-agnostic callers.
Workflow scripts that prefer subprocesses can pipe a JSON envelope through
``opentraces security sanitize`` and read sanitised JSON back from stdout;
in-process Python callers should import ``opentraces.security.sanitize_record``
(or its siblings) directly.

Three subcommands:

  * ``opentraces security sanitize`` — runs the configured (or explicit) tools
    against a JSON payload on stdin and writes the result + a report to stdout.
  * ``opentraces security tools list`` — enumerates the static registry of
    privacy/security tools and their current enable state.
  * ``opentraces security tools info <name>`` — full descriptor for one tool.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any

import click

from ._help import OpentracesCommand, OpentracesGroup
from ..core.config import load_config
from ..security import (
    FieldType,
    list_tools,
    sanitize_dict,
    sanitize_record,
    sanitize_text,
)
from ..security.tools._registry import get as get_tool


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


@click.group("security", cls=OpentracesGroup)
def security_group() -> None:
    """Optional privacy/security utilities."""


# ---------------------------------------------------------------------------
# `security sanitize`
# ---------------------------------------------------------------------------


_PAYLOAD_HINT = (
    'expected JSON on stdin: {"text": "..."} | {"record": {...TraceRecord...}} | {"row": {...}}'
)


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise click.UsageError(f"empty stdin — {_PAYLOAD_HINT}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"invalid JSON on stdin: {exc}") from exc
    if not isinstance(data, dict):
        raise click.UsageError(f"top-level JSON must be an object — {_PAYLOAD_HINT}")
    return data


def _resolve_tool_arg(tools_csv: str | None, use_config: bool, cfg) -> tuple[list[str] | None, Any]:
    """Apply the CLI's --tools / --use-config flags to ``sanitize_*`` resolution.

    Returns ``(tools, cfg_for_call)`` to forward to the sanitize call.
    Exactly one of the two flags must be set.
    """
    if tools_csv and use_config:
        raise click.UsageError("pass either --tools or --use-config, not both")
    if not tools_csv and not use_config:
        raise click.UsageError("pass --tools <names> or --use-config")
    if tools_csv:
        names = [n.strip() for n in tools_csv.split(",") if n.strip()]
        if not names:
            raise click.UsageError("--tools requires at least one name")
        return names, None
    return None, cfg


def _findings_to_dicts(findings) -> list[dict[str, Any]]:
    return [asdict(f) for f in findings]


@security_group.command("sanitize", cls=OpentracesCommand)
@click.option(
    "--tools",
    "tools_csv",
    default=None,
    help=(
        "Comma-separated optional tool names to run (e.g. 'regex,trufflehog'). "
        "Mutually exclusive with --use-config."
    ),
)
@click.option(
    "--use-config",
    is_flag=True,
    help="Run only tools explicitly enabled in the loaded config.",
)
@click.option(
    "--field-type",
    type=click.Choice([ft.value for ft in FieldType]),
    default=FieldType.GENERAL.value,
    show_default=True,
    help="Field type hint applied to single-text and dict payloads.",
)
def sanitize_cmd(tools_csv: str | None, use_config: bool, field_type: str) -> None:
    """Sanitise JSON read from stdin.

    \b
    Payload shapes:
      {"text": "..."}             single string, returns {"sanitized": "...", "findings": [...]}
      {"row":  {...JSON dict...}} sanitises the dict's string leaves
      {"record": {...TraceRecord JSON...}} full record path (returns the mutated record)
    """
    cfg = load_config() if use_config else None
    tools, cfg_for_call = _resolve_tool_arg(tools_csv, use_config, cfg)

    payload = _read_stdin_json()
    ft = FieldType(field_type)

    if "text" in payload:
        text = payload.get("text")
        if not isinstance(text, str):
            raise click.UsageError('"text" must be a string')
        sanitized, findings = sanitize_text(text, field_type=ft, tools=tools, cfg=cfg_for_call)
        out = {"sanitized": sanitized, "findings": _findings_to_dicts(findings)}
        click.echo(json.dumps(out))
        return

    if "row" in payload:
        row = payload.get("row")
        if not isinstance(row, dict):
            raise click.UsageError('"row" must be an object')
        sanitized, report = sanitize_dict(row, tools=tools, cfg=cfg_for_call, field_type=ft)
        out = {
            "sanitized": sanitized,
            "report": {
                "tools_applied": report.tools_applied,
                "redactions_applied": report.redactions_applied,
                "findings": _findings_to_dicts(report.findings),
                "errors": [{"tool": t, "error": e} for t, e in report.errors],
            },
        }
        click.echo(json.dumps(out))
        return

    if "record" in payload:
        from opentraces_schema import TraceRecord

        rec_data = payload.get("record")
        if not isinstance(rec_data, dict):
            raise click.UsageError('"record" must be an object')
        try:
            record = TraceRecord.model_validate(rec_data)
        except Exception as exc:  # noqa: BLE001 — schema validation
            raise click.UsageError(f"invalid TraceRecord: {exc}") from exc
        record, report = sanitize_record(record, tools=tools, cfg=cfg_for_call)
        out = {
            "sanitized": record.model_dump(mode="json"),
            "report": {
                "tools_applied": report.tools_applied,
                "redactions_applied": report.redactions_applied,
                "findings": _findings_to_dicts(report.findings),
                "verdicts": [
                    {"name": v.name, "decision": v.decision, "summary": v.summary, "payload": v.payload}
                    for v in report.verdicts
                ],
                "errors": [{"tool": t, "error": e} for t, e in report.errors],
            },
        }
        click.echo(json.dumps(out))
        return

    raise click.UsageError(_PAYLOAD_HINT)


# ---------------------------------------------------------------------------
# `security tools`
# ---------------------------------------------------------------------------


@security_group.group("tools", cls=OpentracesGroup)
def tools_group() -> None:
    """Inspect the security/privacy tool registry."""


@tools_group.command("list", cls=OpentracesCommand)
@click.option("--json", "json_out", is_flag=True, help="Machine-readable JSON output.")
def tools_list(json_out: bool) -> None:
    """List every registered tool with its current enable state."""
    cfg = load_config()
    infos = list_tools(cfg)
    if json_out:
        click.echo(json.dumps([asdict(i) for i in infos]))
        return
    for info in infos:
        marker = "on " if info.enabled else "off"
        detail = info.detail or ""
        click.echo(f"  [{marker}] {info.name:18s} {info.kind:11s}  {info.state:10s}  {detail}")


@tools_group.command("info", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "json_out", is_flag=True, help="Machine-readable JSON output.")
def tools_info(name: str, json_out: bool) -> None:
    """Show the descriptor for one tool."""
    cfg = load_config()
    try:
        tool = get_tool(name)
    except KeyError:
        raise click.ClickException(f"unknown security tool: {name!r}")
    info = tool.describe(cfg)
    if json_out:
        click.echo(json.dumps(asdict(info)))
        return
    click.echo(f"name:         {info.name}")
    click.echo(f"display:      {info.display_name}")
    click.echo(f"kind:         {info.kind}")
    click.echo(f"enabled:      {info.enabled}")
    click.echo(f"state:        {info.state}")
    if info.detail:
        click.echo(f"detail:       {info.detail}")
    if info.setup_cmd:
        click.echo(f"setup cmd:    {info.setup_cmd}")
    if info.disable_cmd:
        click.echo(f"disable cmd:  {info.disable_cmd}")
