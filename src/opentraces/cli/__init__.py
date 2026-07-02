"""CLI entry point for opentraces.

Every command emits structured JSON with next_steps and next_command fields.
Designed to be driven by Claude Code via bundled SKILL.md.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import click

from .. import __version__
from ..core.config import auth_identity, load_config, load_project_config, save_config, save_project_config
from ..core.trace_meta import short_trace_id
from ..core.trace_stage import (
    DEFAULT_AGENT,
    DEFAULT_REMOTE_NAME,
    DEFAULT_REVIEW_POLICY,
    OPENTRACES_ASCII,
    OPENTRACES_TAGLINE,
    SUPPORTED_AGENTS,
    normalize_agents,
    normalize_review_policy,
    resolve_visible_stage,
)
from ._envelope import envelope as _envelope
from ._help import OpentracesGroup

logger = logging.getLogger(__name__)

SENTINEL = "---OPENTRACES_JSON---"

# Global JSON mode flag, set by --json on the root group.
_json_mode = False


# -- D1: top-level --json propagation -----------------------------------------
#
# The root group exposes ``--json`` as a global flag, but historically only
# subcommands that use ``emit_json()`` (gated on the ``_json_mode`` global)
# honored it. Subcommands with their own per-command ``--json`` option (most
# of them) ignored the global one, so ``opentraces --json trace query`` would
# emit human text while ``opentraces trace query --json`` emitted JSON.
#
# Fix: hook ``click.Command.make_context``. When the parent context's root
# context has ``json_mode=True`` and the resolved sub-command exposes its own
# ``--json`` option, inject ``--json`` into the sub-command's args before it
# parses. Idempotent (skips if already present), recursive across nested
# groups (each ``make_context`` call is independent), and a no-op for groups
# / commands that don't have a ``--json`` option.

_original_make_context = click.Command.make_context


def _command_has_json_option(cmd: click.Command) -> bool:
    for param in cmd.params:
        if isinstance(param, click.Option) and "--json" in (param.opts or []):
            return True
    return False


def _root_json_mode(parent: click.Context | None) -> bool:
    if parent is None:
        return False
    root = parent.find_root()
    obj = root.obj
    if isinstance(obj, dict) and obj.get("json_mode"):
        return True
    return False


def _patched_make_context(self, info_name, args, parent=None, **extra):  # type: ignore[override]
    if (
        parent is not None
        and _root_json_mode(parent)
        and _command_has_json_option(self)
        and "--json" not in args
    ):
        args = list(args) + ["--json"]
    return _original_make_context(self, info_name, args, parent=parent, **extra)


click.Command.make_context = _patched_make_context  # type: ignore[assignment]


# -- Grouped help formatting --------------------------------------------------

COMMAND_SECTIONS = [
    (
        "Global Setup",
        [
            "setup",
            "upgrade",
            "uninstall",
            "auth",
            "config",
            "completions",
        ],
    ),
    (
        "Project Setup",
        [
            "init",
            "status",
            "doctor",
            "remove",
        ],
    ),
    (
        "Trace",
        [
            "trace",
        ],
    ),
    (
        "Trail",
        [
            "trail",
        ],
    ),
    (
        "Context",
        [
            "ctx",
        ],
    ),
    (
        "Bucket",
        [
            "bucket",
        ],
    ),
    (
        "Workflow",
        [
            "workflow",
            "skill-verifier",
        ],
    ),
    (
        "Dataset",
        [
            "dataset",
        ],
    ),
    (
        "Capsule",
        [
            "capsule",
        ],
    ),
    (
        "Security",
        [
            "security",
        ],
    ),
    (
        "Capture",
        [
            "capture-otlp",
        ],
    ),
    (
        "Maintenance",
        [
            "git-backfill",
        ],
    ),
]

# Sections whose entries should also list their non-hidden subcommands
# inline, so the root --help reveals the verbs each group exposes.
# Sections that carry a third tuple element (sub-categories) handle
# their own expansion via the sub-category map, so they're not in this set.
EXPANDED_SECTIONS = {
    "Trace",
    "Trail",
    "Context",
    "Bucket",
    "Workflow",
    "Dataset",
    "Security",
    "Capture",
}


# -- Color / presentation helpers (moved to _display.py) ----------------------
#
# Thin wrappers around click.style. click.echo auto-strips ANSI when stdout is
# not a TTY, and respects the NO_COLOR env var, so these are safe to sprinkle
# through human output without guarding every call.
#
# The implementations live in _display.py; re-exported here so the ~98
# external import sites that do "from opentraces.cli import <X>" keep working.

from ._display import (
    _STAGE_COLORS,
    _bold,
    _dim,
    _ok,
    _warn,
    _err,
    _stage_c,
    _describe_trace,
    _TIER_PRIORITY,
    _TIER_GLYPH,
    _git_chip,
    _status_cell,
)


def print_banner(*, tagline: str | None = OPENTRACES_TAGLINE, file=None) -> None:
    """Print the OT ASCII banner plus an optional tagline.

    Used at welcoming moments: ``--help``, the end of ``init``, and the end
    of ``setup`` subcommands. Respects ``--json`` mode (suppressed there).
    """
    if _json_mode:
        return
    click.echo(click.style(OPENTRACES_ASCII, fg="cyan", bold=True), file=file)
    if tagline:
        click.echo(f"\n  {_dim(tagline)}\n", file=file)


class GroupedGroup(OpentracesGroup):
    """Root group: ``COMMAND_SECTIONS``-based command listing.

    The banner + tagline + command-title bar are rendered by
    ``OpentracesGroup.format_help`` for every command and group; this
    subclass only overrides ``format_commands`` to swap the flat listing
    for the curated sections defined in ``COMMAND_SECTIONS``.
    """

    def _style_rows(self, rows: list[tuple[str, str]], name_width: int) -> list[tuple[str, str]]:
        # Prefix each command with a dim-magenta "ot" shorthand, pad the
        # name to a shared width so descriptions line up across all
        # sections, and italicize the descriptions for visual hierarchy.
        prefix = click.style("ot", fg="magenta", bold=True)
        styled = []
        for name, help_text in rows:
            padded = name.ljust(name_width)
            key = f"{prefix} {click.style(padded, fg='cyan', bold=True)}"
            styled.append((key, click.style(help_text, italic=True)))
        return styled

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        # Journey-first sectioned listing. Public roots are intentionally
        # sectioned here rather than left to Click's flat listing so the live
        # CLI remains the source of truth for docs and agent discovery.
        # A section entry may carry an optional 3rd element — a list of
        # (sub_label, [subcommand_names]) tuples — to bucket a group's
        # subcommands under labelled sub-headings.
        sections: list[tuple[str, list[tuple[str, str]], list[tuple[str, list[tuple[str, str]]]]]] = []
        for entry in COMMAND_SECTIONS:
            if len(entry) == 3:
                section_name, cmd_names, sub_categories = entry
            else:
                section_name, cmd_names = entry
                sub_categories = None
            rows: list[tuple[str, str]] = []
            sub_buckets: list[tuple[str, list[tuple[str, str]]]] = []
            expand = sub_categories is None and section_name in EXPANDED_SECTIONS
            for name in cmd_names:
                cmd = self.commands.get(name)
                if cmd is None or cmd.hidden:
                    continue
                rows.append((name, cmd.get_short_help_str(limit=formatter.width)))
                if sub_categories is not None and isinstance(cmd, click.Group):
                    for sub_label, sub_cmd_names in sub_categories:
                        bucket: list[tuple[str, str]] = []
                        for sub_cmd_name in sub_cmd_names:
                            sub = cmd.get_command(ctx, sub_cmd_name)
                            if sub is None or sub.hidden:
                                continue
                            bucket.append(
                                (
                                    f"{name} {sub_cmd_name}",
                                    sub.get_short_help_str(limit=formatter.width),
                                )
                            )
                        if bucket:
                            sub_buckets.append((sub_label, bucket))
                elif expand and isinstance(cmd, click.Group):
                    for sub_name in cmd.list_commands(ctx):
                        sub = cmd.get_command(ctx, sub_name)
                        if sub is None or sub.hidden:
                            continue
                        rows.append(
                            (
                                f"{name} {sub_name}",
                                sub.get_short_help_str(limit=formatter.width),
                            )
                        )
            if rows or sub_buckets:
                sections.append((section_name, rows, sub_buckets))
        # Cross-section width alignment: pick the widest name across all
        # sections so descriptions align in one column from top to bottom.
        name_width = max(
            (
                len(n)
                for _, rows, sub_buckets in sections
                for n, _ in rows + [item for _, bucket in sub_buckets for item in bucket]
            ),
            default=0,
        )
        for section_name, rows, sub_buckets in sections:
            heading = f"{section_name.upper()} COMMANDS"
            with self._section(formatter, heading):
                if rows:
                    formatter.write_dl(self._style_rows(rows, name_width))
                for sub_label, bucket in sub_buckets:
                    formatter.write_paragraph()
                    indent = " " * formatter.current_indent
                    formatter.write(
                        f"{indent}{click.style(sub_label + ':', dim=True, italic=True)}\n"
                    )
                    with formatter.indentation():
                        formatter.write_dl(self._style_rows(bucket, name_width))


def emit_json(data: dict) -> None:
    """Emit structured JSON for agent-native parsing.

    Two emission conventions (ADR-0007 lint L3):

    * **Explicit ``--json`` (``_json_mode``)** — emit PURE ``json.dumps``
      with no ``---OPENTRACES_JSON---`` sentinel and no leading human text,
      so ``opentraces --json <cmd> | jq`` is valid JSON.
    * **Legacy auto-non-TTY (no ``--json`` flag, piped stdout)** — keep the
      sentinel preamble so legacy consumers and otbox journey parsing that
      split on it keep working.
    * **Interactive human session (TTY, no ``--json``)** — suppressed so the
      terminal stays clean.
    """
    if _json_mode:
        click.echo(json.dumps(data, indent=2))
        return
    if sys.stdout.isatty():
        return
    click.echo(f"\n{SENTINEL}")
    click.echo(json.dumps(data, indent=2))


def human_echo(message: str = "", **kwargs) -> None:
    """Echo human-readable text, suppressed in --json mode."""
    if not _json_mode:
        click.echo(message, **kwargs)


def human_hint(hint: str | None) -> None:
    """Echo a Hint: line to human output when a hint is available."""
    if hint and not _json_mode:
        click.echo(f"{_warn('Hint:')} {hint}")


def error_response(code: str, kind: str, message: str, hint: str | None = None, retryable: bool = False) -> dict[str, object]:
    return {
        "status": "error",
        "error": {
            "code": code,
            "kind": kind,
            "message": message,
            "hint": hint,
            "retryable": retryable,
        },
    }


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


# rc for a command that refused to prompt because it could not run
# interactively (ADR-0007 lint L2). Distinct, documented exit code so an
# agent can branch on "you must supply a flag" without parsing prose.
RC_INTERACTIVE_REQUIRED = 2


def require_interactive(command_name: str, hint: str) -> None:
    """Refuse (zero prompt bytes) when we cannot prompt a human.

    ADR-0007 lint L2: a command that would drop into an interactive
    ``click.prompt``/``click.confirm`` must NOT do so under explicit
    ``--json`` or on a non-interactive terminal (closed/piped stdin or
    stdout). Instead it emits a structured ``INTERACTIVE_REQUIRED`` error
    naming the explicit flags/subcommands that do the same work
    non-interactively, and exits ``RC_INTERACTIVE_REQUIRED`` (2). Emits ZERO
    prompt bytes.

    A genuine interactive human session (real TTY, no ``--json``) returns
    immediately so today's prompt behavior is unchanged.
    """
    if not _json_mode and _is_interactive_terminal():
        return
    payload = error_response(
        "INTERACTIVE_REQUIRED",
        command_name,
        f"'{command_name}' needs an interactive terminal; refusing to prompt.",
        hint=hint,
    )
    emit_json(payload)
    if not _json_mode and sys.stdout.isatty():
        # emit_json suppressed on a TTY-stdout / piped-stdin session; still
        # surface a one-line refusal (no prompt bytes) so the human sees it.
        click.echo(f"{command_name}: {hint}", err=True)
    sys.exit(RC_INTERACTIVE_REQUIRED)


def _masked_input(prompt: str = "Token: ") -> str:
    """Read input showing * for each character typed."""
    import tty
    import termios

    if not sys.stdin.isatty():
        return input(prompt)

    sys.stderr.write(prompt)
    sys.stderr.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    chars = []
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                break
            if ch in ("\x7f", "\x08"):  # backspace
                if chars:
                    chars.pop()
                    sys.stderr.write("\b \b")
                    sys.stderr.flush()
            elif ch == "\x03":  # ctrl-c
                raise KeyboardInterrupt
            else:
                chars.append(ch)
                sys.stderr.write("*")
                sys.stderr.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stderr.write("\n")
    return "".join(chars)



_auth_identity = auth_identity


def _default_repo(identity: dict | None) -> str:
    if identity is not None:
        return f"{identity.get('name', 'me')}/{DEFAULT_REMOTE_NAME}"
    return DEFAULT_REMOTE_NAME


def _require_project_opted_in(action: str) -> None:
    """Hard gate: exit 2 if cwd has not run ``opentraces init``.

    Used by project-bound actions to surface a clear error rather than
    silently acting on an uninitialized project.
    """
    from ..core.config import NotOptedInError, project_is_opted_in

    cwd = Path.cwd()
    if not project_is_opted_in(cwd):
        raise NotOptedInError(cwd, action=action)


def _parse_agent_selection(agent_text: str) -> list[str]:
    return normalize_agents([part.strip() for part in agent_text.split(",") if part.strip()])


def _prompt_agents_with_click(default_agents: list[str] | None = None) -> list[str]:
    default_value = ",".join(default_agents or list(SUPPORTED_AGENTS[:1]))
    click.echo("Supported agents:")
    for agent in SUPPORTED_AGENTS:
        click.echo(f"  - {agent}")
    while True:
        agent_text = click.prompt(
            "Agents (comma-separated)",
            default=default_value,
        )
        selected_agents = _parse_agent_selection(agent_text)
        if selected_agents:
            return selected_agents
        click.echo("Select at least one supported agent.")


def _agent_placeholder() -> str:
    return ",".join(SUPPORTED_AGENTS[:2]) or DEFAULT_AGENT


@click.group(
    cls=GroupedGroup,
    invoke_without_command=True,
    # Let descriptions use the full terminal width instead of Click's
    # default 80-column cap. Click takes ``min(terminal_width,
    # max_content_width)`` when formatting, so a generous ceiling here
    # means narrow terminals still wrap correctly while wide ones get
    # the room they have. Children inherit ``max_content_width`` from
    # this root context.
    context_settings={"max_content_width": 10_000},
)
@click.version_option(version=__version__)
@click.option("--json", "json_mode", is_flag=True, help="Emit only machine-readable JSON output")
@click.pass_context
def main(ctx: click.Context, json_mode: bool) -> None:
    # Intentionally no docstring: the tagline under the banner is the
    # description, so a Click ``help`` string here would duplicate it.
    global _json_mode
    _json_mode = json_mode
    ctx.ensure_object(dict)
    ctx.obj["json_mode"] = json_mode

    if ctx.invoked_subcommand is not None:
        return

    click.echo(ctx.get_help())
    return


# -- Auth / login helpers (moved to _auth_impl.py) ----------------------------
#
# Implementations live in _auth_impl.py; re-exported here for backward-compat.

from ._auth_impl import (
    HF_OAUTH_CLIENT_ID,
    HF_OAUTH_SCOPES,
    HF_DEVICE_CODE_URL,
    HF_TOKEN_URL,
    HF_DEVICE_GRANT_TYPE,
    _login_impl,
    _logout_impl,
    _auth_status_impl,
    _login_with_device_code,
    _login_with_token,
    _validate_and_save,
    _resolve_username_prefix,
    _choose_remote_interactively,
    _choose_remote_interactively_async,
)


def _current_project_session_dir(project_dir: Path, cfg=None) -> Path | None:
    """Return the Claude Code session directory for the current repo, if present.

    Uses ``encode_claude_path`` — the same encoding corpus discovery uses —
    so repo paths containing characters outside ``[alnum, '-']`` (``_``,
    ``.``, …) resolve to the directory Claude Code actually writes. The
    old ``'/' -> '-'`` slug diverged for such paths, which made ``init
    --import-existing`` count 0 sessions and silently import nothing.
    """
    from ..core.config import get_projects_path
    from ..core.repo_identity import encode_claude_path

    if cfg is None:
        cfg = load_config()
    projects_path = get_projects_path(cfg)
    session_dir = projects_path / encode_claude_path(project_dir)
    return session_dir if session_dir.exists() else None


def _capture_sessions_into_project(
    session_dir: Path,
    project_dir: Path,
    cfg=None,
    *,
    on_progress=None,
) -> tuple[int, int]:
    """Import existing session files into the project's local inbox.

    ``on_progress``, if provided, is called as ``on_progress(done, total)``
    after each file; ``total`` is the number of session files we plan to
    attempt. Useful for wiring a progress bar in the CLI.
    """
    from ..core.config import (
        load_project_config, get_project_traces_dir, get_project_state_path,
        project_is_opted_in, is_project_excluded,
    )
    from ..capture import get_parser
    from ..core.pipeline import process_trace
    from ..core.state import StateManager, TraceStatus, ProcessedFile

    # Defence in depth: even if an entry-point forgot to gate, refuse to
    # create staging dirs or write traces into an uninitialized project.
    if not project_is_opted_in(project_dir):
        return 0, 0

    if cfg is None:
        cfg = load_config()

    # Per-project exclusion gate, mirroring the ingest choke-point
    # contract (core/ingest.py::ingest_one_session): an excluded project
    # never stages a trace and its marker is never touched —
    # `is_project_excluded` reads the marker raw, no migration write.
    # `project_is_opted_in` alone is just marker-exists, which an
    # excluded-but-enrolled marker passes (issue #60 item 3).
    if is_project_excluded(cfg, str(Path(project_dir).resolve())):
        return 0, 0

    proj_config = load_project_config(project_dir)
    review_policy = normalize_review_policy(proj_config.get("review_policy"))

    staging = get_project_traces_dir(project_dir)
    staging.mkdir(parents=True, exist_ok=True)

    parser = get_parser("claude-code")()

    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)

    parsed_count = 0
    error_count = 0

    session_files = sorted(session_dir.glob("*.jsonl"))
    total = len(session_files)
    if on_progress is not None:
        on_progress(0, total)

    for idx, session_file in enumerate(session_files, start=1):
        should_process, offset = state.should_reprocess(str(session_file))
        if not should_process:
            if on_progress is not None:
                on_progress(idx, total)
            continue

        try:
            record = parser.parse_session(session_file, byte_offset=offset)
            if record is None:
                continue

            result = process_trace(record, project_dir, cfg)
            staging_file = staging / f"{result.record.trace_id}.jsonl"
            staging_file.write_text(result.record.to_jsonl_line() + "\n")

            from ..core.trace_stage import decide_post_parse_status
            decided_status, block_reason = decide_post_parse_status(
                result, review_policy=review_policy
            )

            if decided_status == TraceStatus.BLOCKED:
                state.block_trace(
                    result.record.trace_id,
                    reason=block_reason or "security finding",
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )
            elif decided_status == TraceStatus.COMMITTED:
                state.set_trace_status(
                    result.record.trace_id,
                    TraceStatus.COMMITTED,
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )
                task_desc = ""
                if result.record.task:
                    task_desc = (result.record.task.description or "")[:80] if hasattr(result.record.task, "description") else ""
                state.create_commit_group(
                    [result.record.trace_id],
                    task_desc or short_trace_id(result.record.trace_id, 12),
                )
            else:
                state.set_trace_status(
                    result.record.trace_id,
                    TraceStatus.STAGED,
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )

            stat = session_file.stat()
            state.mark_file_processed(ProcessedFile(
                file_path=str(session_file),
                inode=stat.st_ino,
                mtime=stat.st_mtime,
                last_byte_offset=stat.st_size,
            ))
            parsed_count += 1
        except Exception as e:
            error_count += 1
            click.echo(f"  Error: {session_file.name}: {e}", err=True)
        finally:
            if on_progress is not None:
                on_progress(idx, total)

    return parsed_count, error_count


@main.group()
def config() -> None:
    """Manage opentraces configuration."""
    pass


@config.command("show")
def config_show() -> None:
    """Display current configuration (secrets masked).

    TTY-aware: humans get a sectioned, colorized layout; pipes / --json
    get the same JSON dump as before so downstream tools don't break.
    """
    from ..core.config import CONFIG_PATH

    cfg = load_config()
    data = cfg.model_dump()
    if data.get("custom_redact_strings"):
        data["custom_redact_strings"] = ["***" for _ in data["custom_redact_strings"]]
    if data.get("hf_token"):
        data["hf_token"] = "***"

    if _json_mode or not sys.stdout.isatty():
        click.echo(json.dumps(data, indent=2))
        return

    _render_config_pretty(data, CONFIG_PATH)
    emit_json(data)


def _render_config_pretty(data: dict, config_path) -> None:
    """Sectioned human-readable rendering of the global config dict."""
    label_w = 22

    def _kv(key: str, value, dim_value: bool = False) -> None:
        rendered = _dim(str(value)) if dim_value else str(value)
        click.echo(f"  {_dim(key.ljust(label_w))}  {rendered}")

    def _section(title: str, hint: str | None = None) -> None:
        click.echo()
        head = click.style(title, fg="magenta", bold=True)
        suffix = f"  {_dim(hint)}" if hint else ""
        click.echo(f"{head}{suffix}")

    # Global block
    _section("GLOBAL", str(config_path))
    _kv("config version", data.get("config_version", "?"))
    token = data.get("hf_token")
    _kv("hf token", "*** (set)" if token else _dim("(not set)"))
    _kv("classifier sensitivity", data.get("classifier_sensitivity", "medium"))
    _kv("dataset visibility", data.get("dataset_visibility", "private"))
    custom_path = data.get("projects_path")
    if custom_path:
        _kv("projects path", custom_path)

    # Projects
    projects = data.get("projects") or {}
    excluded = set(data.get("excluded_projects") or [])
    _section("REGISTERED PROJECTS", f"({len(projects)})")
    if not projects:
        click.echo(f"  {_dim('(none — run opentraces init in a project to opt in)')}")
    else:
        for path in sorted(projects.keys()):
            mark = click.style("✓", fg="red") if path in excluded else click.style("✓", fg="green")
            click.echo(f"  {mark} {path}")
    if excluded:
        click.echo(f"  {_dim(f'{len(excluded)} excluded')}")

    # Redaction
    redact = data.get("custom_redact_strings") or []
    if redact:
        _section("REDACTION")
        _kv("custom strings", f"{len(redact)} (masked)")

    # Security · TruffleHog
    sec = data.get("security") or {}
    th = sec.get("trufflehog") or {}
    _section("SECURITY · TRUFFLEHOG")
    _kv("enabled", "yes" if th.get("enabled") else _dim("no"))
    _kv("verify secrets", "yes" if th.get("verify_secrets") else _dim("no"))

    # Security · LLM Review
    rl = sec.get("llm_review") or {}
    _section("SECURITY · LLM REVIEW")
    _kv("enabled", "yes" if rl.get("enabled") else _dim("no"))
    _kv("api format", rl.get("api_format", "?"))
    _kv("base url", rl.get("base_url") or _dim("(unset)"))
    _kv("model", rl.get("model") or _dim("(unset)"))
    api_key_env = rl.get("api_key_env")
    if api_key_env:
        present = "set" if os.environ.get(api_key_env) else click.style("NOT SET", fg="red")
        _kv("api key env", f"${api_key_env} ({present})")
    else:
        _kv("api key env", _dim("(unset — local server)"))
    _kv("timeout", f"{rl.get('timeout', '?')}s")
    _kv("prompt version", rl.get("prompt_version", "?"))
    click.echo()


# Project-marker fields with a boolean SHAPE (checked truthy elsewhere, e.g.
# core/config.py::is_project_excluded reads `marker.get("excluded")`).
# `_PORTABLE_FIELDS` carries no type info of its own (the marker is a plain
# dict, not a Pydantic model like the global Config), so this is the explicit
# type map `config set --project` coerces against.
_PORTABLE_BOOLEAN_FIELDS = frozenset({"excluded"})


def _coerce_portable_value(key: str, raw: str) -> object:
    """Type-aware coercion for ``config set --project`` (ADR-0007 config
    fold, issue #160 V6). Without this, ``--project excluded false``
    persists the truthy string ``"false"`` instead of the boolean
    ``False`` — a footgun since ``is_project_excluded`` reads the marker
    for a falsy value, so the string form never actually opts a project
    back in. Non-boolean portable keys are returned unchanged (strings).
    """
    if key not in _PORTABLE_BOOLEAN_FIELDS:
        return raw
    lowered = raw.strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    raise ValueError(
        f"'{raw}' is not a valid boolean for '{key}' "
        "(use true/false, 1/0, or yes/no)"
    )


@config.command("set")
@click.argument("key", required=False)
@click.argument("value", required=False)
@click.option(
    "--project", "scope_project", is_flag=True,
    help="Write to <repo>/.opentraces.json instead of the global config.",
)
@click.option(
    "--global", "scope_global", is_flag=True,
    help="Write to ~/.opentraces/config.json (default).",
)
@click.option("--append", "append_value", is_flag=True, help="Append to a list-typed key.")
def config_set(
    key: str | None,
    value: str | None,
    scope_project: bool,
    scope_global: bool,
    append_value: bool,
) -> None:
    """Set a configuration value.

      ot config set <key> <value> [--append] [--project|--global]

    Default scope is global; --project writes to <repo>/.opentraces.json.
    --append appends to list-typed keys (e.g. custom_redact_strings).
    """
    if scope_project and scope_global:
        click.echo("--project and --global are mutually exclusive.", err=True)
        sys.exit(2)

    if key is None or value is None:
        click.echo("Usage: ot config set <key> <value> [--project|--global]", err=True)
        sys.exit(2)

    # hf_token is deliberately never persisted via config set: save_config
    # excludes it (it lives in the credential store / env), and writing it to a
    # project marker would leak a secret into a committed file. Reject loudly
    # instead of silently dropping the value behind a success message.
    if key == "hf_token":
        click.echo(
            "hf_token cannot be set with 'config set' (it is never written to "
            "config.json or a project marker). Use 'opentraces auth login' to "
            "store your HuggingFace token, or export HF_TOKEN in your environment.",
            err=True,
        )
        sys.exit(2)

    if scope_project:
        # Write to repo marker via load/save_project_config helpers.
        from ..core.config import (
            _PORTABLE_FIELDS,
            load_project_config,
            save_project_config,
        )
        # The marker only persists the portable policy fields; any other key
        # would be silently dropped on write. Reject it instead of reporting a
        # misleading success (same false-success class as hf_token above).
        if key not in _PORTABLE_FIELDS:
            click.echo(
                f"'{key}' is not a project-scoped config key (it would be "
                f"silently dropped from the marker).\n"
                f"Valid --project keys: {', '.join(_PORTABLE_FIELDS)}.\n"
                f"For other keys use --global.",
                err=True,
            )
            sys.exit(2)
        proj_dir = Path.cwd()
        proj_cfg = load_project_config(proj_dir)
        if append_value:
            existing = proj_cfg.get(key)
            if not isinstance(existing, list):
                existing = [] if existing is None else [existing]
            if value not in existing:
                existing.append(value)
            proj_cfg[key] = existing
            stored_value: object = existing
        else:
            try:
                stored_value = _coerce_portable_value(key, value)
            except ValueError as e:
                click.echo(str(e), err=True)
                emit_json(error_response("INVALID_CONFIG_VALUE", "config-set", str(e)))
                sys.exit(2)
            proj_cfg[key] = stored_value
        save_project_config(proj_dir, proj_cfg)
        click.echo(f"Set {key}={stored_value} (project)")
        emit_json({"status": "ok", "scope": "project", "key": key, "value": stored_value})
        return

    # Global scope (default).
    cfg = load_config()
    # Validate key against the Config model.
    if key not in type(cfg).model_fields:
        click.echo(
            f"Unknown config key '{key}'. Use 'ot config show' to see valid keys.",
            err=True,
        )
        sys.exit(2)
    if append_value:
        existing = getattr(cfg, key, None) or []
        if not isinstance(existing, list):
            click.echo(f"--append only valid for list-typed keys; '{key}' is {type(existing).__name__}.", err=True)
            sys.exit(2)
        if value not in existing:
            existing.append(value)
        setattr(cfg, key, existing)
    else:
        # Coerce simple scalar types from the string value.
        field_info = type(cfg).model_fields[key]
        annotation = field_info.annotation
        try:
            if annotation is bool or "bool" in str(annotation):
                coerced = value.lower() in {"true", "1", "yes", "on"}
            elif annotation is int or "int" in str(annotation):
                coerced = int(value)
            else:
                coerced = value
            setattr(cfg, key, coerced)
        except (ValueError, TypeError) as e:
            click.echo(f"Invalid value for {key}: {e}", err=True)
            sys.exit(2)

    save_config(cfg)
    click.echo(f"Set {key}={value} (global)")
    emit_json({"status": "ok", "scope": "global", "key": key, "value": value})


@config.command("tracking-mode")
@click.argument("mode", required=False, type=click.Choice(["global", "manual"]))
def config_tracking_mode(mode: str | None) -> None:
    """Show or set the project tracking mode (plan 081).

      ot config tracking-mode            # show current mode
      ot config tracking-mode global     # auto-enroll projects on capture
      ot config tracking-mode manual     # explicit 'opentraces init' opt-in per project

    Global mode (the default) auto-enrolls Claude/Codex AND Pi projects (git or
    not) with a private + review-required policy the first time a capture hook or
    the Pi extension fires there. Capture is opt-out: switch to manual mode, or
    set a per-project `excluded` marker / `opentraces remove`, to turn it off.
    Raw provider bodies stay default-off regardless of tracking mode.
    """
    cfg = load_config()
    if mode is None:
        # L3 (epic #129): suppress the bare human value under explicit --json so
        # stdout stays pure JSON.
        human_echo(cfg.capture.tracking_mode)
        emit_json({"status": "ok", "tracking_mode": cfg.capture.tracking_mode})
        return
    cfg.capture.tracking_mode = mode
    save_config(cfg)
    click.echo(f"Set tracking-mode={mode}")
    emit_json({"status": "ok", "tracking_mode": mode})


@config.command("get")
@click.argument("key", required=True)
def config_get(key: str) -> None:
    """Read a single global configuration value.

      ot config get <key>

    The single-setting counterpart to 'ot config set <key> <value>' — the
    canon `set [KEY] [VALUE]` shape implies a key-addressing getter, but
    only the whole-config 'ot config show' dump existed until now. Prints
    the bare value in human mode; under --json returns the uniform
    {status, schema_version, key, value, source} envelope. Unknown keys
    exit non-zero with a structured error instead of a bare value.
    """
    cfg = load_config()
    if key not in type(cfg).model_fields:
        message = f"Unknown config key '{key}'. Use 'opentraces config show' to see valid keys."
        human_echo(message)
        emit_json(error_response("UNKNOWN_CONFIG_KEY", "config-get", message))
        sys.exit(2)

    value = getattr(cfg, key)
    if hasattr(value, "model_dump"):
        # A nested config section (e.g. `bucket`, `security`) — dump to a
        # plain JSON-serializable dict rather than leak a Pydantic repr.
        value = value.model_dump()
    if key == "hf_token" and value:
        value = "***"
    elif key == "custom_redact_strings" and value:
        value = ["***" for _ in value]

    human_echo(json.dumps(value, indent=2) if isinstance(value, (dict, list)) else str(value))
    emit_json(_envelope(
        "opentraces.config.get.v1",
        key=key,
        value=value,
        source="global",
    ))


# --------------------------------------------------------------------------- #
# Plan-043 phase 6: identity finalization helper
# --------------------------------------------------------------------------- #

def _plan043_finalize_identity(project_dir: Path) -> None:
    """Record ``root_commit_sha`` and (if the user hasn't already answered)
    prompt for a first-run backfill over any discovered Claude JSONL corpus.

    Non-interactive sessions (``stdin`` not a tty) skip the prompt and leave
    the decision as ``None`` so the next interactive init will ask.

    Also handles the checkout-move case: if a ``~/.opentraces/projects/<slug>/``
    already records the same root-commit SHA under a different path, we
    print an informational line and reuse the existing slug by writing an
    updated ``project.json`` pointer (same slug, new path). This avoids
    creating duplicate attribution state.
    """
    from ..core import repo_identity as _ri
    from ..core.config import (
        get_first_run_backfill_decision,
        get_root_commit_sha,
        set_first_run_backfill_decision,
        set_root_commit_sha,
        get_project_dir,
    )

    root_sha = _ri.root_commit_sha(project_dir)
    if root_sha and get_root_commit_sha(project_dir) != root_sha:
        set_root_commit_sha(project_dir, root_sha)

    # Checkout-move: another slug already holds this root SHA.
    if root_sha:
        existing = _ri.discover_matching_project(root_sha)
        current_slug_dir = None
        try:
            current_slug_dir = get_project_dir(project_dir)
        except Exception:
            current_slug_dir = None
        if existing and current_slug_dir and existing.slug != current_slug_dir.name:
            click.echo(
                f"Found existing attribution data at {existing.old_path} "
                f"(slug={existing.slug}). Reusing that history under the new path."
            )
            # Update the existing slug's project.json to point at the new
            # path. We intentionally do NOT move data — the slug directory
            # stays put under ~/.opentraces/projects/.
            try:
                existing_slug_dir = existing.traces_dir.parent
                _ri.write_project_identity(
                    existing_slug_dir, project_dir=project_dir, root_sha=root_sha,
                )
            except Exception:
                pass
        elif current_slug_dir:
            # Stamp our own slug's project.json so future moves can find us.
            try:
                _ri.write_project_identity(
                    current_slug_dir, project_dir=project_dir, root_sha=root_sha
                )
            except Exception:
                pass

    # First-run backfill prompt.
    decision = get_first_run_backfill_decision(project_dir)
    if decision is not None:
        return  # already answered (Y, declined, or never)

    corpus = _ri.discover_claude_jsonl_corpus(project_dir) if root_sha else []
    if not corpus:
        return  # nothing to backfill; don't pester

    if not sys.stdin.isatty():
        return  # non-interactive: leave decision None, re-ask next time

    # Ask with pyclack when available so the prompt renders with the same
    # ◇ green-diamond styling as the rest of `init`. Fall back to click.
    answer: str | None = None
    try:
        from pyclack.prompts import select as _pk_select
        from pyclack.core import Option as _PkOption
        import asyncio as _asyncio

        answer = _asyncio.run(_pk_select(
            "Backfill commit attribution now?",
            [
                _PkOption(
                    value="Y", label="Backfill now",
                    hint=f"powers 'opentraces trail blame' over {len(corpus)} past session(s)",
                ),
                _PkOption(
                    value="declined", label="Skip for now",
                    hint="ask again next init",
                ),
                _PkOption(
                    value="never", label="Never",
                    hint="run 'opentraces backfill' manually later",
                ),
            ],
            initial_value="Y",
        ))
    except ImportError:
        try:
            raw = click.prompt(
                "Backfill commit attribution now? Powers 'opentraces trail blame' — "
                f"points each committed line back to the trace that wrote it. "
                f"({len(corpus)} past session(s)) [Y/n/never]",
                default="Y",
                show_default=False,
            )
        except click.Abort:
            return
        a = (raw or "").strip().lower()
        if a in ("n", "no"):
            answer = "declined"
        elif a == "never":
            answer = "never"
        else:
            answer = "Y"
    except Exception:
        return

    if answer == "declined":
        set_first_run_backfill_decision(project_dir, "declined")
        click.echo("(skipped; will ask again next init)")
        return
    if answer == "never":
        set_first_run_backfill_decision(project_dir, "never")
        click.echo("(won't ask again; enable manually with 'opentraces backfill')")
        return
    set_first_run_backfill_decision(project_dir, "Y")
    try:
        from ..core import backfill as _bf

        # Determinate progress bar: we don't know the commit count until
        # run_full starts, so the first on_progress call sets the length.
        bar_state: dict = {"bar": None}

        def _on_progress(done: int, total: int) -> None:
            b = bar_state["bar"]
            if b is None and total > 0:
                bar_state["bar"] = click.progressbar(
                    length=total, label="Backfilling commit attribution",
                )
                bar_state["bar"].__enter__()
                b = bar_state["bar"]
            if b is not None and done > 0:
                delta = done - getattr(b, "_ot_last", 0)
                if delta > 0:
                    b.update(delta)
                    b._ot_last = done  # type: ignore[attr-defined]

        try:
            report = _bf.run_full(project_dir, on_progress=_on_progress)
        finally:
            if bar_state["bar"] is not None:
                bar_state["bar"].__exit__(None, None, None)
        click.echo(
            f"Backfilled {report.commits_processed} commit(s); "
            f"{report.attributed_lines} line(s) attributed."
        )
    except Exception as e:  # pragma: no cover - surfaced in logs
        click.echo(f"(backfill failed: {e})", err=True)


@main.command(
    examples=[
        "opentraces init",
        "opentraces init --agent claude-code",
        "opentraces init --agent codex-cli",
        "opentraces init --agent pi --start-fresh",
        "opentraces init --start-fresh",
    ],
    see_also=[
        ("opentraces setup claude-code", "install Claude Code capture hooks"),
        ("opentraces setup codex-cli", "install Codex CLI hooks"),
        ("opentraces setup pi", "install/check the Pi package entry"),
        ("opentraces setup git", "install or remove the git post-commit hook"),
        ("opentraces dataset remote create", "create or bind a dataset remote"),
        ("opentraces auth login", "authenticate with HuggingFace"),
    ],
    option_groups=[
        ("Agents", ["agents", "import_existing"]),
    ],
)
@click.option(
    "--agent",
    "agents",
    multiple=True,
    type=click.Choice(sorted({*SUPPORTED_AGENTS, "claude", "codex"})),
    help="Agent runtime to connect",
)
@click.option(
    "--import-existing/--start-fresh",
    "import_existing",
    default=None,
    help="Import existing Claude Code traces for this repo only",
)
def init(
    agents: tuple[str, ...],
    import_existing: bool | None,
) -> None:
    """Initialize opentraces in the current project.

    Enrolls the current repository and connects selected local agent hooks.
    Dataset remotes and review policy belong to ``ot dataset ...``.
    """
    from ..core.config import _marker_path, load_project_config, save_project_config

    project_dir = Path.cwd()
    marker_file = _marker_path(project_dir)
    legacy_ot_dir = project_dir / ".opentraces"
    legacy_config_json = legacy_ot_dir / "config.json"
    legacy_config_yml = legacy_ot_dir / "config.yml"

    # Check if already initialized (new marker, or legacy in-repo dir).
    if marker_file.exists() or legacy_config_json.exists() or legacy_config_yml.exists():
        proj_config = load_project_config(project_dir)
        current_remote = proj_config.get("remote", "not set")
        # Plan-043 phase 6: on every init (even repeated), refresh root
        # commit identity + optionally prompt for first-run backfill.
        _plan043_finalize_identity(project_dir)
        # Backfill the global registry — projects that were initialized
        # before the registry existed (or before they got pruned) won't
        # appear in `opentraces list --projects` until we re-add them.
        from ..core.config import register_project as _register_project
        _cfg_for_register = load_config()
        if _register_project(_cfg_for_register, project_dir):
            save_config(_cfg_for_register)
            click.echo("(added to global opted-in registry)")
        requested_agents = normalize_agents(list(agents)) if agents else []
        current_agents = normalize_agents(proj_config.get("agents"))
        merged_agents = list(current_agents)
        added_agents: list[str] = []
        for agent in requested_agents:
            if agent not in merged_agents:
                merged_agents.append(agent)
                added_agents.append(agent)
        if added_agents:
            proj_config["agents"] = merged_agents
            save_project_config(project_dir, proj_config)
            _install_capture_hook(project_dir, added_agents)
            _install_skill(project_dir, added_agents)
            click.echo(f"Added agent capture: {', '.join(added_agents)}")
        click.echo(
            "Already initialized "
            f"(mode: {proj_config.get('review_policy', 'review')}, remote: {current_remote})"
        )
        click.echo("Run 'opentraces status' to inspect this inbox.")
        emit_json(
            {
                "status": "ok",
                "message": "Already initialized" if not added_agents else "Updated initialized project",
                "review_policy": proj_config["review_policy"],
                "push_policy": proj_config["push_policy"],
                "agents": proj_config["agents"],
                "agents_added": added_agents,
            }
        )
        return

    review_policy = DEFAULT_REVIEW_POLICY
    push_policy = "manual"
    selected_agents = normalize_agents(list(agents))

    if _is_interactive_terminal() and not agents:
        try:
            from pyclack.prompts import text
            import asyncio

            async def _interactive_setup() -> list[str]:
                if len(SUPPORTED_AGENTS) == 1:
                    chosen_agents = list(SUPPORTED_AGENTS)
                    click.echo(f"Supported agent detected: {chosen_agents[0]}")
                else:
                    click.echo("Supported agents:")
                    for agent in SUPPORTED_AGENTS:
                        click.echo(f"  - {agent}")

                    def _validate_agents(value: str) -> str | None:
                        if _parse_agent_selection(value):
                            return None
                        return "Select at least one supported agent"

                    chosen_agents_text = await text(
                        "Which agents should opentraces connect in this project?",
                        placeholder=_agent_placeholder(),
                        default_value=list(SUPPORTED_AGENTS)[0],
                        validate=_validate_agents,
                    )
                    chosen_agents = _parse_agent_selection(chosen_agents_text)
                return normalize_agents(chosen_agents)

            selected_agents = asyncio.run(_interactive_setup())
        except ImportError:
            if not agents:
                selected_agents = list(SUPPORTED_AGENTS) if len(SUPPORTED_AGENTS) == 1 else _prompt_agents_with_click()

    proj_config: dict = {
        "mode": "auto" if review_policy == "auto" else "review",
        "review_policy": review_policy,
        "push_policy": push_policy,
        "agents": selected_agents,
        "visibility": "private",
    }
    save_project_config(project_dir, proj_config)

    # Register this project in the global opted-in list. This is the
    # user-visible consent record: `opentraces status` reads it, and every
    # capture/publish path cross-checks `.opentraces/config.json` against
    # it before doing anything.
    from ..core.config import register_project as _register_project
    _cfg_for_register = load_config()
    if _register_project(_cfg_for_register, project_dir):
        save_config(_cfg_for_register)

    # Note: canonical traces/runtime state live in ~/.opentraces/projects/<slug>/.
    # The .opentraces.json marker is meant to be committed. Some live harnesses
    # may also write ignored local sidecars (for example .opentraces/pi/events/)
    # before ingest projects them into the shared bucket.

    hook_installed = _install_capture_hook(project_dir, selected_agents)

    skill_installed = _install_skill(project_dir, selected_agents)

    cfg = load_config()
    existing_session_dir = _current_project_session_dir(project_dir, cfg=cfg)
    existing_session_files = sorted(existing_session_dir.glob("*.jsonl")) if existing_session_dir else []
    existing_session_count = len(existing_session_files)
    imported_existing = 0
    import_errors = 0
    if existing_session_count and import_existing is None and _is_interactive_terminal():
        try:
            from pyclack.prompts import confirm
            import asyncio

            import_existing = asyncio.run(
                confirm(
                    f"Import {existing_session_count} existing Claude Code trace(s) for this repo now?",
                    initial_value=True,
                    active="Import now",
                    inactive="Start fresh",
                )
            )
        except ImportError:
            import_existing = click.confirm(
                f"Import {existing_session_count} existing Claude Code trace(s) for this repo now?",
                default=True,
            )

    if existing_session_count and import_existing:
        # Route through the single-source ingestion core (live-session
        # ingestion, Phase 1). ``scan_project`` runs the generation-aware
        # pipeline per session; the progress bar ticks once per session
        # processed.
        from ..core.ingest import scan_project

        with click.progressbar(
            length=existing_session_count,
            label="Importing Claude Code traces",
        ) as bar:
            report = scan_project(project_dir)
            # scan_project doesn't offer a per-session callback (one call
            # per tick of the outer watcher loop is enough), so we fill
            # the bar at the end. A future version could wire a callback
            # if latency on large backlogs becomes visible.
            bar.update(len(report.results))
            imported_existing = report.created + report.new_generations
            import_errors = report.errored

    # Plan-043 phase 6: record root commit + prompt for first-run backfill.
    _plan043_finalize_identity(project_dir)

    # Auto-provision the entity-parser binary so function-level attribution
    # works without the user discovering the explicit 'setup entity-parser'.
    # Best-effort + offline-safe: an unsupported platform or a failed/offline
    # fetch degrades silently to file/line-level attribution (a one-shot
    # marker stops it re-downloading on every init). Never blocks init.
    entity_provisioned = None
    try:
        from ..enrichment.entities import installer as _entity_installer
        entity_provisioned = _entity_installer.ensure_installed(best_effort=True)
    except Exception:  # noqa: BLE001 - optional enrichment must never break init
        entity_provisioned = None

    click.echo()
    print_banner(tagline=_ok("initialized"))
    click.echo(f"{_dim('Project: ')} {_bold(project_dir.name)}  {_dim(f'({review_policy} policy)')}")
    from ..core.config import get_project_traces_dir
    traces_dir = get_project_traces_dir(project_dir)
    click.echo(f"  Marker:  {marker_file}")
    click.echo(f"  Traces:  {traces_dir}")
    if hook_installed:
        hook_targets: list[str] = []
        if "claude-code" in selected_agents:
            hook_targets.append(".claude/settings.json")
        if "codex-cli" in selected_agents:
            hook_targets.append("~/.codex/hooks.json")
        if "pi" in selected_agents:
            hook_targets.append(".pi/settings.json")
        click.echo(f"  Hook:    {', '.join(hook_targets) if hook_targets else 'installed'}")
    if skill_installed:
        click.echo("  Skill:   .agents/skills/opentraces/SKILL.md")
    if entity_provisioned is not None:
        click.echo(f"  Entity parser: {entity_provisioned.version} ({entity_provisioned.source})")
    click.echo(f"  Agents:  {', '.join(selected_agents)}")
    click.echo(f"  Policy:  {review_policy}")
    click.echo(f"  Push:    {push_policy}")
    if existing_session_count:
        click.echo(f"  Existing Claude traces: {existing_session_count}")
        if imported_existing or import_errors:
            click.echo(f"  Imported existing: {imported_existing} ({import_errors} errors)")
        else:
            click.echo("  Existing traces were left untouched; new traces will capture automatically.")
    click.echo("\nRecommended flow:")
    if existing_session_count and imported_existing:
        click.echo("  1. Build or apply a dataset, then review it with 'opentraces dataset review'")
    elif existing_session_count:
        click.echo("  1. Decide whether to import past traces or just start from now on")
        click.echo(f"     Session dir: {existing_session_dir}")
    else:
        click.echo("  1. Start a connected agent; capture is automatic from now on")
    click.echo("  2. Query traces with 'opentraces trace query'")
    click.echo("  3. Publish dataset rows with 'opentraces dataset publish <name>'")

    emit_json({
        "status": "ok",
        "mode": proj_config["mode"],
        "review_policy": review_policy,
        "push_policy": push_policy,
        "remote": None,
        "agents": selected_agents,
        "hook_installed": hook_installed,
        "skill_installed": skill_installed,
        "existing_session_count": existing_session_count,
        "import_existing": import_existing,
        "imported_existing": imported_existing,
        "import_errors": import_errors,
        "config_path": str(marker_file),
        "staging_path": str(traces_dir),
        "next_steps": [
            "Search imported traces with opentraces trace query" if imported_existing else (
                "Import past traces or start a connected agent; future traces will be captured automatically"
                if existing_session_count
                else "Start a connected agent, traces will be captured automatically"
            ),
        ],
        "next_command": "opentraces trace query" if imported_existing else "opentraces trace query",
    })


@main.command(
    examples=[
        "opentraces remove",
        "opentraces remove --all",
    ],
    see_also=[
        ("opentraces init", "re-initialize the project later."),
    ],
)
@click.option("--all", "purge_all", is_flag=True, default=False,
              help="Also delete the audit ref (refs/opentraces/audit/*) "
                   "and trace-to-commit notes (refs/notes/opentraces) from "
                   "this repository.")
def remove(purge_all: bool) -> None:
    """Remove opentraces from the current project.

    Uninstalls the capture hook, deletes the repo marker, and unregisters
    the project from the global registry. Traces already pushed upstream
    are untouched. Use ``--all`` to additionally purge the git-side audit
    ref and trace-to-commit notes.
    """
    from ..core.config import (
        NotOptedInError,
        _marker_path,
        get_project_dir,
        unregister_project as _unregister_project,
    )

    project_dir = Path.cwd()
    marker_file = _marker_path(project_dir)
    legacy_ot_dir = project_dir / ".opentraces"

    removed_hook = _remove_capture_hook(project_dir)

    # Resolve the global per-project dir BEFORE deleting the marker
    # (slug derivation needs the marker's project_id). A non-opted-in dir
    # raises NotOptedInError here; that must NOT abort remove — a cleanup
    # command should fall through to its own "nothing to remove" path
    # rather than tell the user to 'init' first.
    global_dir = None
    try:
        global_dir = get_project_dir(project_dir)
    except (RuntimeError, NotOptedInError):
        pass

    removed_local = False
    if marker_file.exists():
        marker_file.unlink()
        removed_local = True
    if legacy_ot_dir.exists():
        shutil.rmtree(legacy_ot_dir)
        removed_local = True

    removed_global = False
    if global_dir is not None and global_dir.exists():
        shutil.rmtree(global_dir)
        removed_global = True

    # Drop this project from the global opted-in registry so it no
    # longer appears in `opentraces list --projects`.
    cfg_for_unregister = load_config()
    if _unregister_project(cfg_for_unregister, project_dir):
        save_config(cfg_for_unregister)

    if removed_local:
        click.echo(f"Removed project marker: {marker_file}")
    if removed_global:
        click.echo(f"Removed local trace state: {global_dir}")
    if not (removed_local or removed_global):
        click.echo("No opentraces marker or local state found.")

    if removed_hook:
        click.echo("Removed Claude Code SessionEnd hook.")

    purged_refs: list[str] = []
    if purge_all:
        # Best-effort purge via the shared path-parameterized helper (also
        # used by ``setup uninstall --purge``). Missing refs are a no-op.
        from ..core.uninstall import purge_git_refs
        result = purge_git_refs(project_dir)
        purged_refs = result["purged_refs"]
        if purged_refs:
            click.echo(
                f"Purged {len(purged_refs)} git ref(s): "
                f"{', '.join(purged_refs)}"
            )
        elif not result["git_not_found"]:
            click.echo("No audit or notes refs found to purge.")

    click.echo("Remote datasets were not changed.")
    emit_json({
        "status": "ok",
        "removed_local": removed_local,
        "removed_hook": removed_hook,
        "purged_refs": purged_refs,
        "remote_changed": False,
        "next_steps": ["Run 'opentraces init' to set this project up again"],
        "next_command": "opentraces init",
    })


@click.command("projects-list")
def projects_list_cmd() -> None:
    """List every project that has run ``opentraces init``.

    Reads from the global registry in ~/.opentraces/config.json. Each
    entry is cross-checked against on-disk ``.opentraces/config.json``
    presence so the list never lies about the real state.
    """
    from ..core.config import opted_in_projects, project_is_opted_in

    cfg = load_config()
    registered = opted_in_projects(cfg)

    if not registered:
        click.echo("No projects have opted in yet.")
        click.echo("Run 'opentraces init' inside a project to add it.")
        emit_json({"status": "ok", "projects": [], "count": 0})
        return

    from rich.console import Console as _Console
    from rich.table import Table as _Table
    from rich import box as _box

    console = _Console()
    console.print()
    console.print(f"[bold]Opted-in projects[/]  [dim]({len(registered)})[/]")
    console.print()

    table = _Table(box=_box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), header_style="dim")
    table.add_column("Path", overflow="fold")

    rows: list[dict] = []
    stale: list[str] = []
    for path_str in registered:
        exists = project_is_opted_in(Path(path_str))
        cell = path_str if exists else f"[yellow]{path_str}[/]"
        table.add_row(cell)
        rows.append({"path": path_str, "on_disk": exists})
        if not exists:
            stale.append(path_str)

    console.print(table)
    if stale:
        console.print(
            f"[dim]· {len(stale)} path(s) missing .opentraces.json — "
            f"run `opentraces remove` or re-init[/]",
            highlight=False,
        )
    console.print(
        "[dim]· copy a path and `cd` into it to continue[/]",
        highlight=False,
    )
    console.print()

    emit_json({"status": "ok", "projects": rows, "count": len(rows)})


def _detect_install_method() -> str:
    """Detect how opentraces was installed: pipx, brew, editable, or pip."""
    pkg_path = Path(__file__).resolve()
    pkg_str = str(pkg_path)

    # Editable / source install: not in site-packages
    if "site-packages" not in pkg_str:
        return "source"

    # Check if installed via brew (macOS Cellar, homebrew, or Linux linuxbrew)
    if "/Cellar/" in pkg_str or "/homebrew/" in pkg_str.lower() or "/linuxbrew/" in pkg_str.lower():
        return "brew"

    # Check if pipx manages this package
    if shutil.which("pipx"):
        pipx_home = os.environ.get("PIPX_HOME", str(Path.home() / ".local" / "pipx"))
        if pipx_home in pkg_str:
            return "pipx"

    # Default: regular pip
    return "pip"


def _run_upgrade_subprocess(cmd: list[str], method: str, timeout: int = 120) -> bool:
    """Run an upgrade subprocess with error handling. Returns True on success."""
    import subprocess

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        human_echo(f"{method} binary not found on PATH.")
        emit_json(error_response("UPGRADE_FAILED", "upgrade", f"{method} not found"))
        sys.exit(4)
    except subprocess.TimeoutExpired:
        human_echo(f"{method} upgrade timed out after {timeout}s.")
        emit_json(error_response("UPGRADE_FAILED", "upgrade", f"{method} timed out"))
        sys.exit(4)

    if result.returncode == 0:
        output = result.stdout.strip()
        human_echo(output if output else "CLI upgraded.")
        return True

    combined = (result.stderr + result.stdout).lower()
    # "already at latest" is not an error — match specific phrases to avoid false positives
    if any(phrase in combined for phrase in ("already up to date", "already up-to-date", "already at latest", "already installed opentraces", "already installed")):
        human_echo("Already on the latest version.")
        return True

    human_echo(f"{method} upgrade failed: {result.stderr.strip()}")
    emit_json(error_response("UPGRADE_FAILED", "upgrade", result.stderr.strip()))
    sys.exit(4)


def _heal_brew_tap() -> str | None:
    """Ensure the canonical opentraces Homebrew tap is correctly wired before a
    brew upgrade. Best-effort; never raises.

    opentraces moved to a dedicated tap (``jayfarei/opentraces`` ->
    ``JayFarei/homebrew-opentraces``). Installs from before the move have a tap
    clone that still points at the old generic ``homebrew-tap`` (a historical
    GitHub redirect), which no longer carries the opentraces formula — so
    ``brew upgrade jayfarei/opentraces/opentraces`` would fail to find it.
    Repoint a stale clone in place (or tap fresh) so the upgrade resolves.
    Returns a short status string if it healed anything, else None.
    """
    import shutil
    import subprocess

    brew = shutil.which("brew")
    if brew is None:
        return None
    canon_tap = "jayfarei/opentraces"
    canon_remote = "https://github.com/JayFarei/homebrew-opentraces.git"

    def _b(args, timeout=60):
        try:
            return subprocess.run(
                [brew, *args], capture_output=True, text=True, timeout=timeout
            )
        except Exception:  # noqa: BLE001
            return None

    def _git(args, timeout=60):
        try:
            return subprocess.run(
                ["git", *args], capture_output=True, text=True, timeout=timeout
            )
        except Exception:  # noqa: BLE001
            return None

    try:
        rp = _b(["--repository", canon_tap])
        tap_dir = (
            Path(rp.stdout.strip())
            if rp is not None and rp.returncode == 0 and rp.stdout.strip()
            else None
        )
        healed: str | None = None
        if tap_dir is not None and tap_dir.is_dir():
            remote = ""
            r = _git(["-C", str(tap_dir), "remote", "get-url", "origin"], timeout=20)
            if r is not None and r.returncode == 0:
                remote = (r.stdout or "").strip()
            has_formula = (tap_dir / "Formula" / "opentraces.rb").exists() or (
                tap_dir / "opentraces.rb"
            ).exists()
            if "homebrew-opentraces" not in remote or not has_formula:
                # Stale clone (still points at the old homebrew-tap). Repoint +
                # hard-reset in place — avoids the `brew tap` "remote mismatch".
                _git(["-C", str(tap_dir), "remote", "set-url", "origin", canon_remote], timeout=20)
                if (_git(["-C", str(tap_dir), "fetch", "--quiet", "origin"], timeout=90) is not None):
                    _git(["-C", str(tap_dir), "reset", "--hard", "FETCH_HEAD"], timeout=20)
                    healed = "repointed stale tap to the dedicated homebrew-opentraces repo"
        else:
            res = _b(["tap", canon_tap, canon_remote])
            if res is not None and res.returncode == 0:
                healed = "tapped jayfarei/opentraces"
        # Homebrew 6.x requires third-party taps to be trusted; on older brew
        # `trust` is an unknown subcommand and the call is a harmless no-op.
        _b(["trust", canon_tap])
        return healed
    except Exception:  # noqa: BLE001
        return None


def _render_integration_repair_summary(summary: dict) -> None:
    repaired = summary.get("repaired") or []
    errors = summary.get("errors") or []
    if repaired:
        names = ", ".join(str(item.get("name")) for item in repaired)
        human_echo(f"Re-rendered installed integrations: {names}.")
    else:
        human_echo("No installed integrations needed re-rendering.")
    for item in errors:
        human_echo(f"Warning: integration repair failed for {item.get('name')}: {item.get('error')}")


def _run_fresh_upgrade_repair(*, refresh_project_skill: bool) -> dict:
    """Run repair in a fresh CLI process after package-manager upgrade."""
    binary = shutil.which("opentraces") or sys.argv[0]
    mode = "--skill-only" if refresh_project_skill else "--integrations-only"
    cmd = [binary, "setup", "upgrade", mode]
    human_echo("Refreshing installed integrations with the upgraded CLI...")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "error",
            "delegated": True,
            "command": cmd,
            "error": str(exc),
        }
    if result.stdout.strip():
        human_echo(result.stdout.strip())
    if result.stderr.strip():
        human_echo(result.stderr.strip())
    combined = f"{result.stdout}\n{result.stderr}".lower()
    already_latest = any(
        phrase in combined
        for phrase in (
            "already up to date",
            "already up-to-date",
            "already at latest",
            "already installed opentraces",
            "already installed",
        )
    )
    if result.returncode != 0 and not already_latest:
        return {
            "status": "error",
            "delegated": True,
            "command": cmd,
            "returncode": result.returncode,
        }
    return {
        "status": "delegated",
        "delegated": True,
        "command": cmd,
        "returncode": result.returncode,
    }


def _upgrade_impl(skill_only: bool, *, integrations_only: bool = False) -> None:
    """Upgrade opentraces CLI and refresh installed integration glue."""
    current_version = __version__

    if skill_only and integrations_only:
        human_echo("Choose either --skill-only or --integrations-only, not both.")
        emit_json(error_response(
            "INVALID_UPGRADE_MODE",
            "upgrade",
            "--skill-only and --integrations-only are mutually exclusive",
        ))
        sys.exit(2)

    delegated_repair: dict | None = None
    if not skill_only and not integrations_only:
        from ..core.config import project_is_opted_in as _upgrade_project_is_opted_in

        refresh_project_skill = _upgrade_project_is_opted_in(Path.cwd())
        method = _detect_install_method()
        human_echo(f"Current version: {current_version}")
        human_echo(f"Install method:  {method}")

        if method == "source":
            human_echo("Source install detected. Pull the latest and run: pip install -e .")
            human_echo("Skipping CLI upgrade, updating skill and hook only.")
        elif method == "brew":
            human_echo("Upgrading via brew...")
            # Self-heal the tap first: opentraces moved to its own tap, and
            # pre-move installs may have a stale clone pointing at the old
            # generic homebrew-tap (which no longer carries the formula).
            healed = _heal_brew_tap()
            if healed:
                human_echo(f"  ({healed})")
            # Use the fully-qualified tap formula: a bare ``opentraces`` is
            # ambiguous when more than one tap defines it (e.g. a legacy
            # ``jayfarei/tap`` alongside the canonical ``jayfarei/opentraces``),
            # and brew refuses to upgrade rather than guess.
            _run_upgrade_subprocess(
                ["brew", "upgrade", "jayfarei/opentraces/opentraces"], "brew"
            )
            delegated_repair = _run_fresh_upgrade_repair(
                refresh_project_skill=refresh_project_skill,
            )
        elif method == "pipx":
            human_echo("Upgrading via pipx...")
            _run_upgrade_subprocess(["pipx", "upgrade", "opentraces"], "pipx")
            delegated_repair = _run_fresh_upgrade_repair(
                refresh_project_skill=refresh_project_skill,
            )
        else:
            human_echo("Upgrading via pip...")
            _run_upgrade_subprocess(
                [sys.executable, "-m", "pip", "install", "--upgrade", "opentraces"], "pip"
            )
            delegated_repair = _run_fresh_upgrade_repair(
                refresh_project_skill=refresh_project_skill,
            )

        if delegated_repair and delegated_repair.get("status") == "error":
            emit_json(error_response(
                "INTEGRATION_REPAIR_FAILED",
                "upgrade",
                "CLI upgraded, but post-upgrade integration repair failed",
                "Run: opentraces setup upgrade --integrations-only",
            ))
            sys.exit(5)
        if delegated_repair:
            emit_json({
                "status": "ok",
                "cli_upgraded": True,
                "skill_refreshed": refresh_project_skill,
                "hook_refreshed": refresh_project_skill,
                "integration_repair": delegated_repair,
                "next_steps": ["Run 'opentraces doctor' to verify deployed integration versions"],
                "next_command": "opentraces doctor",
            })
            return

    from ..core.integration_repair import repair_installed_integrations

    project_dir = Path.cwd()
    # Refresh / provision the entity-parser binary (integration glue): picks up a
    # bumped manifest version, and retries a prior offline/failed fetch. Best-
    # effort + offline-safe, so it never breaks the upgrade.
    try:
        from ..enrichment.entities import installer as _entity_installer
        _entity_installer.ensure_installed(best_effort=True, retry_failed=True)
    except Exception:  # noqa: BLE001 - optional enrichment must never break upgrade
        pass
    if delegated_repair is None:
        integration_repair = repair_installed_integrations(project_dir)
        _render_integration_repair_summary(integration_repair)
    else:
        integration_repair = delegated_repair

    if integrations_only:
        emit_json({
            "status": "ok",
            "cli_upgraded": False,
            "skill_refreshed": False,
            "hook_refreshed": False,
            "integration_repair": integration_repair,
            "next_steps": ["Run 'opentraces doctor' to verify deployed integration versions"],
            "next_command": "opentraces doctor",
        })
        return

    # Refresh skill and hook in current project
    from ..core.config import project_is_opted_in

    if not project_is_opted_in(project_dir):
        if skill_only:
            human_echo("Not an opentraces project. Run 'opentraces init' first.")
            sys.exit(3)
        human_echo("No project found in current directory. Skill refresh skipped.")
        emit_json({
            "status": "ok",
            "cli_upgraded": not skill_only,
            "skill_refreshed": False,
            "integration_repair": integration_repair,
            "next_steps": ["Run 'opentraces init' in your project to set up"],
            "next_command": "opentraces init",
        })
        return

    proj_config = load_project_config(project_dir)
    agents = proj_config.get("agents") or ["claude-code"]

    skill_refreshed = _install_skill(project_dir, agents)
    if not skill_refreshed:
        human_echo("Warning: could not find skill source to install.")

    try:
        from ..capture.skill.install import SkillInstaller
        global_skill = SkillInstaller().install()
        if global_skill.ok:
            human_echo("  Refreshed global skill: ~/.agents/skills/opentraces/")
    except Exception as e:
        human_echo(f"  Could not refresh global skill: {e}")

    hook_refreshed = _install_capture_hook(project_dir, agents) if not proj_config.get("no_hook") else False

    human_echo("Project updated." if (skill_refreshed or hook_refreshed) else "Project skill and hook unchanged.")

    emit_json({
        "status": "ok",
        "cli_upgraded": not skill_only,
        "skill_refreshed": skill_refreshed,
        "hook_refreshed": hook_refreshed,
        "integration_repair": integration_repair,
        "next_steps": ["Run 'opentraces context' to check project state"],
        "next_command": "opentraces context",
    })


def _install_capture_hook(project_dir: Path, agents: list[str]) -> bool:
    """Install supported agent hooks for auto-parsing."""
    installed_any = False

    if "claude-code" in agents:
        claude_dir = project_dir / ".claude"
        settings_path = claude_dir / "settings.json"

        hook_entry = {
            "type": "command",
            "command": "$(command -v opentraces || command -v OTD || command -v OT) _capture --project-dir .",
            "timeout": 60,
        }

        try:
            claude_dir.mkdir(parents=True, exist_ok=True)

            settings = {}
            if settings_path.exists():
                try:
                    settings = json.loads(settings_path.read_text())
                except Exception:
                    settings = {}

            hooks = settings.setdefault("hooks", {})
            session_end = hooks.setdefault("SessionEnd", [])

            # Check if hook already installed
            for group in session_end:
                for h in group.get("hooks", []):
                    if "opentraces" in h.get("command", ""):
                        human_echo("  Claude Code hook already installed")
                        installed_any = True
                        break
                if installed_any:
                    break

            if not installed_any:
                # Add the hook
                session_end.append({"hooks": [hook_entry]})
                settings_path.write_text(json.dumps(settings, indent=2) + "\n")
                human_echo("  Installed Claude Code SessionEnd hook")
                installed_any = True
        except Exception as e:
            human_echo(f"  Could not install Claude Code hook: {e}")
            human_echo("  Add manually to .claude/settings.json")

    if "codex-cli" in agents:
        try:
            from ..capture._base import HookInstallError
            from ..capture.codex_cli.install import CodexCliHookInstaller

            result = CodexCliHookInstaller().install()
            if result.ok:
                target = result.config_files[0] if result.config_files else "~/.codex/hooks.json"
                if result.added:
                    human_echo(f"  Installed Codex CLI hooks: {target}")
                else:
                    human_echo(f"  Codex CLI hooks already installed: {target}")
                installed_any = True
        except HookInstallError as exc:
            human_echo(f"  Could not install Codex CLI hooks: {exc.message}")
            human_echo("  Run 'opentraces setup codex-cli' after fixing ~/.codex/hooks.json")
        except Exception as exc:
            human_echo(f"  Could not install Codex CLI hooks: {exc}")

    if "pi" in agents:
        try:
            from ..capture._base import HookInstallError
            from ..capture.pi.install import PiHookInstaller

            result = PiHookInstaller(project=True, cwd=project_dir, local=True).install()
            if result.ok:
                target = result.config_files[0] if result.config_files else project_dir / ".pi" / "settings.json"
                if result.added:
                    human_echo(f"  Installed Pi package entry: {target}")
                else:
                    human_echo(f"  Pi package entry already present: {target}")
                installed_any = True
        except HookInstallError as exc:
            human_echo(f"  Could not install Pi package entry: {exc.message}")
            human_echo("  Run 'opentraces setup pi' after fixing Pi settings")
        except Exception as exc:
            human_echo(f"  Could not install Pi package entry: {exc}")

    return installed_any


def _remove_capture_hook(project_dir: Path) -> bool:
    """Remove the OpenTraces Claude Code hook if present."""
    settings_path = project_dir / ".claude" / "settings.json"
    if not settings_path.exists():
        return False

    try:
        settings = json.loads(settings_path.read_text())
    except Exception:
        return False

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False

    session_end = hooks.get("SessionEnd")
    if not isinstance(session_end, list):
        return False

    changed = False
    filtered_groups = []
    for group in session_end:
        if not isinstance(group, dict):
            filtered_groups.append(group)
            continue
        group_hooks = group.get("hooks", [])
        if not isinstance(group_hooks, list):
            filtered_groups.append(group)
            continue

        kept_hooks = []
        for hook in group_hooks:
            command = hook.get("command", "") if isinstance(hook, dict) else ""
            if "opentraces _capture" in command:
                changed = True
                continue
            kept_hooks.append(hook)

        if kept_hooks:
            updated_group = dict(group)
            updated_group["hooks"] = kept_hooks
            filtered_groups.append(updated_group)
        elif "hooks" not in group or len(group) > 1:
            updated_group = {k: v for k, v in group.items() if k != "hooks"}
            if updated_group:
                filtered_groups.append(updated_group)

    if not changed:
        return False

    if filtered_groups:
        hooks["SessionEnd"] = filtered_groups
    else:
        hooks.pop("SessionEnd", None)

    if not hooks:
        settings.pop("hooks", None)

    tmp_path = settings_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(str(tmp_path), str(settings_path))
    return True


# Agent directory mapping: agent name -> skill directory relative to project root
AGENT_SKILL_DIRS = {
    "claude-code": ".claude/skills",
}


def _resolve_skill_source() -> Path | None:
    """Find SKILL.md from installed package or source tree."""
    # Installed package (wheel with force-include)
    pkg_path = Path(__file__).parent.parent / "skill" / "SKILL.md"
    if pkg_path.exists():
        return pkg_path
    # Editable install / source tree: skill/ at repo root
    repo_path = Path(__file__).parent.parent.parent.parent / "skill" / "SKILL.md"
    if repo_path.exists():
        return repo_path
    return None


def _install_skill(project_dir: Path, agents: list[str]) -> bool:
    """Install the opentraces skill into .agents/ and symlink per selected agent."""
    skill_source = _resolve_skill_source()
    if not skill_source:
        return False

    try:
        # 1. Copy into .agents/skills/opentraces/
        agents_skill_dir = project_dir / ".agents" / "skills" / "opentraces"
        agents_skill_dir.mkdir(parents=True, exist_ok=True)
        target = agents_skill_dir / "SKILL.md"
        shutil.copy2(str(skill_source), str(target))

        # 2. Symlink into each selected agent's skill directory
        for agent in agents:
            agent_skills_path = AGENT_SKILL_DIRS.get(agent)
            if not agent_skills_path:
                continue
            agent_skill_dir = project_dir / agent_skills_path / "opentraces"
            agent_skill_dir.mkdir(parents=True, exist_ok=True)
            symlink = agent_skill_dir / "SKILL.md"
            if symlink.exists() or symlink.is_symlink():
                symlink.unlink()
            symlink.symlink_to(os.path.relpath(str(target), str(symlink.parent)))
            human_echo(f"  Linked skill: {agent_skills_path}/opentraces/SKILL.md")

        human_echo("  Installed skill: .agents/skills/opentraces/SKILL.md")
        return True
    except Exception as e:
        human_echo(f"  Could not install skill: {e}")
        return False
@main.command(
    examples=[
        "opentraces status",
        "opentraces status --json",
        "opentraces status --limit 0",
    ],
    see_also=[
        ("opentraces trace query", "search retained traces with filters."),
        ("opentraces doctor", "check pipeline and integration health."),
    ],
)
@click.option(
    "--limit",
    type=int,
    default=10,
    show_default=True,
    help="Show N most-recent traces. Use 0 to list all.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit structured JSON (same as the global --json flag).",
)
def status(limit: int, as_json: bool) -> None:
    """Show status of the current opentraces project.

    Summarises inbox counts by stage, the active remote, and the most
    recent traces. A fast snapshot of what's waiting, staged, and shipped.
    """
    global _json_mode
    if as_json:
        _json_mode = True
    import time as _time
    from ..core.config import (
        load_project_config, get_project_traces_dir, get_project_state_path,
        project_is_opted_in,
    )
    from ..core.state import StateManager

    project_dir = Path.cwd()

    if not project_is_opted_in(project_dir):
        if _json_mode:
            emit_json(error_response(
                "PROJECT_NOT_OPTED_IN",
                "config",
                "Not an opentraces project. Run 'opentraces init' first.",
            ))
        else:
            click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    # Per-project opt-out: report the excluded state cleanly and stop. A
    # bare committed ``{"excluded": true}`` marker carries no project_id,
    # so none of the per-project state below exists — and an enrolled
    # project that opted out has capture disabled, which is the one fact
    # status must surface.
    from ..core.config import is_project_excluded
    if is_project_excluded(load_config(), str(project_dir)):
        if _json_mode:
            emit_json({
                "status": "ok",
                "project": project_dir.name,
                "excluded": True,
                "next_steps": [
                    "Re-enable capture with "
                    "'opentraces config set excluded false --project'"
                ],
            })
        else:
            click.echo(f"{_bold(project_dir.name)}  {_dim('excluded — capture disabled')}")
            click.echo(_dim(
                "  This project opts out via \"excluded\": true in .opentraces.json."
            ))
            click.echo(_dim(
                "  Re-enable with 'opentraces config set excluded false --project'."
            ))
        return

    proj_config = load_project_config(project_dir)
    remote = proj_config.get("remote", None)
    project_name = project_dir.name

    staging_dir = get_project_traces_dir(project_dir)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)

    # Stage counts come from state.json directly — O(entries) in memory,
    # no file I/O. Reading every staged JSONL here used to take seconds
    # on big inboxes and make the command feel frozen.
    counts = {stage: 0 for stage in ("inbox", "staged", "pushed", "rejected")}
    for entry in state._state.get("traces", {}).values():  # noqa: SLF001
        visible_stage = resolve_visible_stage(entry.get("status"))
        counts[visible_stage] = counts.get(visible_stage, 0) + 1
    # Staged files without a state entry fall under "inbox".
    total_files = sum(1 for _ in staging_dir.glob("*.jsonl")) if staging_dir.exists() else 0
    tracked = sum(counts.values())
    counts["inbox"] += max(0, total_files - tracked)

    if not _json_mode:
        # Project header — compact single-line banner + details, set off by a rule.
        from rich.console import Console as _HdrConsole
        from rich.rule import Rule as _HdrRule
        _hdr = _HdrConsole()
        _hdr.print()
        _hdr.print(
            f"  [bold]{project_name}[/]  "
            f"[dim]inbox · {proj_config.get('review_policy', 'review')} · "
            f"{', '.join(proj_config['agents'])}[/]",
            highlight=False,
        )
        visibility = proj_config.get("visibility", "private")
        if remote:
            _hdr.print(f"  [dim]remote:[/] {remote} [dim]({visibility})[/]", highlight=False)
        else:
            _hdr.print("  [dim]remote:[/] [yellow]not set[/]", highlight=False)
        _hdr.print(_HdrRule(style="dim"))
        _hdr.print()

    # Machine-readable mirror of visible rows for --json consumers.
    session_summary: list[dict] = []

    # Session list — sort by record timestamp_end desc (actual age), not
    # file mtime, so the table order matches the "Age" column.
    if total_files == 0:
        if not _json_mode:
            click.echo("0 traces in inbox")
    else:
        from opentraces_schema import TraceRecord
        from rich.console import Console as _Console
        from rich.table import Table as _Table
        from rich import box as _box
        from ..core.state import TraceStatus

        def _ts_of(record) -> float:
            if not record.timestamp_end:
                return 0.0
            try:
                if hasattr(record.timestamp_end, "timestamp"):
                    return record.timestamp_end.timestamp()
                from datetime import datetime as _dt
                return _dt.fromisoformat(
                    str(record.timestamp_end).replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError, AttributeError):
                return 0.0

        all_files = list(staging_dir.glob("*.jsonl"))
        loaded: list[tuple[Path, TraceRecord]] = []
        for sf in all_files:
            try:
                rec = TraceRecord.model_validate_json(sf.read_text().strip().splitlines()[0])
                loaded.append((sf, rec))
            except Exception:
                loaded.append((sf, None))

        # Newest-first by record timestamp_end; unparseable records sort last.
        loaded.sort(key=lambda pr: _ts_of(pr[1]) if pr[1] else -1.0, reverse=True)

        if limit and limit > 0 and len(loaded) > limit:
            loaded = loaded[:limit]

        shown = len(loaded)
        if not _json_mode:
            if shown < total_files:
                pages = (total_files + shown - 1) // shown if shown else 1
                click.echo(
                    f"{_bold(f'showing {shown} of {total_files}')} traces  "
                    f"{_dim(f'(page 1 of ~{pages}; use --limit N or --limit 0 for more)')}"
                )
            else:
                click.echo(f"{_bold(str(total_files))} trace{'s' if total_files != 1 else ''}")
            click.echo()

        now = _time.time()
        console = _Console()
        table = _Table(
            box=_box.SIMPLE_HEAD,
            show_edge=False,
            padding=(0, 1),
            header_style="dim",
        )
        table.add_column("ID", no_wrap=True)
        table.add_column("Age", no_wrap=True, justify="right")
        table.add_column("Task", overflow="ellipsis", no_wrap=True, max_width=40)
        table.add_column("Regex", no_wrap=True, justify="center")
        table.add_column("TH", no_wrap=True, justify="center")
        table.add_column("LLM", no_wrap=True, justify="center")
        table.add_column("Human", no_wrap=True, justify="center")
        table.add_column("Push", no_wrap=True, justify="center")

        _global_cfg = load_config()
        th_enabled = bool(_global_cfg.security.trufflehog.enabled)
        llm_enabled = bool(getattr(_global_cfg.security, "llm_review", None)
                           and _global_cfg.security.llm_review.enabled)

        rows_rendered = 0
        git_link_hits = 0
        _reviewed = {TraceStatus.APPROVED, TraceStatus.COMMITTED,
                     TraceStatus.UPLOADING, TraceStatus.UPLOADED,
                     TraceStatus.REJECTED}

        for sf, record in loaded:
            if record is None:
                table.add_row(
                    f"[dim]{sf.stem[:8]}[/]", "", "[red]? parse error[/]",
                    "[red]?[/]", "[red]?[/]", "[red]?[/]", "[red]?[/]", "[red]?[/]",
                )
                continue
            try:
                entry = state.get_trace(record.trace_id)
                visible_stage = resolve_visible_stage(entry.status if entry else None)

                ts = _ts_of(record)
                if ts:
                    diff_seconds = now - ts
                    if diff_seconds < 3600:
                        rel_time = f"{int(diff_seconds / 60)}m ago"
                    elif diff_seconds < 86400:
                        rel_time = f"{int(diff_seconds / 3600)}h ago"
                    elif diff_seconds < 172800:
                        rel_time = "yesterday"
                    else:
                        rel_time = f"{int(diff_seconds / 86400)}d ago"
                else:
                    rel_time = "unknown"

                title, source = _describe_trace(record)
                if len(title) > 40:
                    title = title[:39] + "…"
                status_cell, status_plain = _status_cell(entry, record)

                t1_ran = bool(record.security.scanned)
                meta_all = getattr(record, "metadata", None) or {}
                sec_meta = meta_all.get("security") or {}
                th_meta = (sec_meta.get("tools") or {}).get("trufflehog") or {}
                th_ran = bool(th_meta) or (th_enabled and t1_ran)
                llm_payload = meta_all.get("llm_review") or {}
                llm_ran = bool(llm_payload.get("review_key"))

                def _tier_cell(enabled: bool, ran: bool) -> str:
                    if not enabled:
                        return "[dim]·[/]"
                    return "[green]✓[/]" if ran else "[yellow]○[/]"

                entry_status = (
                    TraceStatus(entry.status) if entry and isinstance(entry.status, str)
                    else (entry.status if entry else None)
                )
                human_ran = entry_status in _reviewed if entry_status else False
                pushed = False
                if entry:
                    pushed = (
                        entry_status == TraceStatus.UPLOADED
                        or bool(getattr(entry, "uploaded_to", None))
                    )

                short_id = short_trace_id(record.trace_id)
                table.add_row(
                    short_id,
                    f"[dim]{rel_time}[/]",
                    title,
                    _tier_cell(True, t1_ran),
                    _tier_cell(th_enabled, th_ran),
                    _tier_cell(llm_enabled, llm_ran),
                    "[green]✓[/]" if human_ran else "[yellow]○[/]",
                    "[green]✓[/]" if pushed else "[yellow]○[/]",
                )
                rows_rendered += 1
                chip = _git_chip(record)
                if chip is not None:
                    git_link_hits += 1
                session_summary.append({
                    "trace_id": record.trace_id,
                    "short_id": short_id,
                    "stage": visible_stage,
                    "status": status_plain,
                    "task": title,
                    "task_source": source,
                    "age": rel_time,
                    "security": {
                        "regex": t1_ran,
                        "trufflehog": {"enabled": th_enabled, "ran": th_ran},
                        "llm": {"enabled": llm_enabled, "ran": llm_ran},
                        "human": human_ran,
                        "pushed": pushed,
                    },
                })
            except Exception:
                table.add_row(
                    f"[dim]{short_trace_id(record.trace_id)}[/]", "", "[red]? error[/]",
                    "[red]?[/]", "[red]?[/]", "[red]?[/]", "[red]?[/]", "[red]?[/]",
                )

        if not _json_mode:
            console.print(table)
            console.print()
            console.print(
                "  [green]✓[/][dim] reviewed[/]    "
                "[yellow]○[/][dim] pending[/]    "
                "[dim]· disabled[/]    "
                "[dim]· copy an ID to continue (e.g. `ot show <id>`)[/]",
                highlight=False,
            )

            # Setup hints — only shown when coverage is low across the visible
            # window. Dim and terse so they don't nag once hooks are active.
            if rows_rendered > 0:
                hints = []
                git_hook_installed = (project_dir / ".git" / "hooks" / "post-commit").exists()

                if git_link_hits == 0:
                    if git_hook_installed:
                        hints.append(
                            "no git links yet  "
                            f"{_dim('— links populate on next git commit')}"
                        )
                    else:
                        hints.append(
                            "no git links  "
                            f"{_dim('— run')} opentraces setup git "
                            f"{_dim('to install the post-commit hook')}"
                        )

                if hints:
                    console.print()
                    for h in hints:
                        console.print(f"  {_warn('hint:')} {h}", highlight=False)

    # Footer summary — set apart with a dim rule so the eye finds it last.
    if not _json_mode:
        try:
            from rich.console import Console as _Console
            from rich.rule import Rule as _Rule
            _footer_console = _Console()
            _footer_console.print()
            _footer_console.print(_Rule(style="dim"))
        except Exception:
            click.echo()
            click.echo(_dim("─" * 60))
        click.echo(
            f"  {_stage_c('inbox', 'inbox')} {_bold(str(counts['inbox']))}    "
            f"{_stage_c('staged', 'staged')} {_bold(str(counts['staged']))}    "
            f"{_stage_c('pushed', 'pushed')} {_bold(str(counts['pushed']))}    "
            f"{_stage_c('rejected', 'rejected')} {_bold(str(counts['rejected']))}"
        )
        click.echo()

    # Agent-facing affordances (B3b): status is the documented entry point
    # of the next_steps graph, so it must name where to go from here. Keep
    # them state-aware and runnable as written.
    next_steps: list[str] = []
    if counts["inbox"] or counts["staged"] or counts["pushed"]:
        next_steps.append(
            "Search captured traces with 'opentraces trace query <terms>'"
        )
        next_steps.append(
            "Inspect a trace with 'opentraces trace map <trace-id>'"
        )
        next_steps.append(
            "Explain a commit's trace lineage with "
            "'opentraces trail blame commit <sha>'"
        )
        next_steps.append(
            "Project traces into a dataset with 'opentraces dataset new' "
            "then 'opentraces dataset run' / 'opentraces dataset publish'"
        )
    else:
        next_steps.append(
            "Start a connected agent; traces will be captured automatically"
        )
    emit_json({
        "status": "ok",
        "project": project_name,
        "review_policy": proj_config["review_policy"],
        "push_policy": proj_config["push_policy"],
        "agents": proj_config["agents"],
        "remote": remote,
        "counts": counts,
        "total_staged": total_files,
        "sessions": session_summary,
        "next_steps": next_steps,
        "next_command": (
            "opentraces trace query"
            if (counts["inbox"] or counts["staged"] or counts["pushed"])
            else "opentraces status"
        ),
    })


# Register subcommand modules (side-effect: @main.command() bindings)
from . import installers as _installers_module  # noqa: F401,E402
from . import _debug as __debug_module  # noqa: F401,E402
from . import inspect as _inspect_module  # noqa: F401,E402

# Standalone Click groups/commands declared without @main.group decoration
# need explicit registration. Step 13: completions noun + hidden __complete.
from .completions import completions as _completions_group  # noqa: E402
from ._complete import complete_cmd as _complete_cmd  # noqa: E402

main.add_command(_completions_group)
main.add_command(_complete_cmd)

# Plan-043 phase 1 — hidden `ot backfill` verb for the attribution cache.
from .backfill import backfill_cmd as _backfill_cmd  # noqa: E402

main.add_command(_backfill_cmd)

# Retro-correlation of inbox traces to historical commits (git_links +
# refs/notes/opentraces). Complements the live post-commit hook.
from .git_backfill import git_backfill_cmd as _git_backfill_cmd  # noqa: E402

main.add_command(_git_backfill_cmd)

# Plan-043 phase 5 — `ot graph` GitButler-style renderer.
from .graph import graph_cmd as _graph_cmd  # noqa: E402

# Plan-043 phase 4 — `ot blame <sha>` per-commit attribution lookup.
from .blame import blame_group as _blame_group  # noqa: E402

# Plan-054 — Trace Trails VCS-anchored lineage.
from .trail import trail_group as _trail_group  # noqa: E402

_trail_group.add_command(_graph_cmd, name="graph")
_trail_group.add_command(_blame_group, name="blame")

# `trail blame pr` subgroup — PR body rendering driven by the
# `pr-intent-summary-v1` workflow. First non-dataset workflow consumer.
# Lives under `blame` because every consumer (PR, future Slack, dashboard,
# CI) renders the same blame-shaped data for a different destination.
from .trail_pr import attach as _attach_trail_pr  # noqa: E402

_attach_trail_pr(_blame_group)

main.add_command(_trail_group)

# The watcher CLI surface lives under ``ot setup watcher`` (group with
# install/uninstall/start/stop/restart/status/tick subcommands). It is a
# system-level service, so its full lifecycle is co-located with the
# rest of the global ``setup`` namespace rather than getting its own
# top-level verb.


# ---------------------------------------------------------------------------
# Step 11 — auth group (parallel surface to flat login/logout/whoami).
# Both surfaces share the _login_impl / _logout_impl / _auth_status_impl
# Authentication helpers.
# ---------------------------------------------------------------------------

@main.group("auth")
def _auth_group() -> None:
    """HuggingFace identity (login, logout, whoami)."""


@_auth_group.command("login")
@click.option(
    "--token",
    is_flag=True,
    help="Paste a personal access token instead (headless / CI fallback).",
)
@click.option(
    "--device-timeout",
    type=click.IntRange(min=1),
    default=None,
    help="Seconds to wait for browser/device authorization before returning with follow-up steps.",
)
def _auth_login(token: bool, device_timeout: int | None) -> None:
    """Log in to HuggingFace Hub.

    By default, opens a browser to authorize opentraces via HuggingFace's
    OAuth device flow. The granted scopes (``openid profile write-repos
    manage-repos``) allow opentraces to read, push, create, delete, and
    change visibility on datasets in namespaces you belong to, with no
    token paste required. Use ``--token`` only when you cannot open a
    browser (e.g. CI, remote shells).
    """
    _login_impl(token, device_timeout=device_timeout)


@_auth_group.command("logout")
def _auth_logout() -> None:
    """Log out from HuggingFace Hub."""
    _logout_impl()


@_auth_group.command("whoami")
def _auth_whoami() -> None:
    """Show the active HuggingFace identity."""
    _auth_status_impl()


from .installers import setup_group as _setup_group  # noqa: E402

# Import the extracted `setup watcher` sub-group module for its side effect: the
# `@setup_group.group`/`@...command` decorators register the watcher commands on
# `setup_group` at CLI load. (Moved out of installers.py in the cli/setup
# decomposition; installers no longer imports it, so this is the registration
# trigger.)
from . import setup_watcher as _setup_watcher  # noqa: E402,F401
from . import setup_runtime as _setup_runtime  # noqa: E402,F401
from . import setup_agents as _setup_agents  # noqa: E402,F401
from . import setup_bucket as _setup_bucket  # noqa: E402,F401
from . import setup_infra as _setup_infra  # noqa: E402,F401
from . import setup_secrets as _setup_secrets  # noqa: E402,F401
# Imported BEFORE the legacy-root _drop_command loop below: review_llm_cmd is a
# @main.command("llm-review") that the loop then drops from the root (it stays as
# `setup llm-review`), exactly as when it lived in installers.py.
from . import setup_review_llm as _setup_review_llm  # noqa: E402,F401
# Registration trigger for the `doctor` command surface (a stray side-effect
# import that previously sat in the installers review-llm section; relocated here
# with the other registration imports). Unrelated to review-llm.
from . import doctor_cli as _doctor_cli  # noqa: E402,F401

# issue #160: mount the SAME `doctor` command object under `setup` (visible)
# so the install-health read-twin is discoverable right beside the verbs it
# reports on. Root `opentraces doctor` is unchanged (same object, one more
# path — the object stays unhidden, so both mounts are visible).
_setup_group.add_command(_doctor_cli.doctor_cmd, name="doctor")


@_setup_group.command("auth", hidden=True)  # plan 087 — redundant with `auth login` (the canonical home); callable, off --help
@click.option(
    "--token",
    is_flag=True,
    help="Paste a personal access token instead (headless / CI fallback).",
)
@click.option(
    "--device-timeout",
    type=click.IntRange(min=1),
    default=None,
    help="Seconds to wait for browser/device authorization before returning with follow-up steps.",
)
def _setup_auth(token: bool, device_timeout: int | None) -> None:
    """Log in to HuggingFace Hub for dataset remotes."""
    _login_impl(token, device_timeout=device_timeout)


# ---------------------------------------------------------------------------
# Trace and dataset command groups.
# ---------------------------------------------------------------------------
from .trace import (  # noqa: E402
    trace_group as _trace_group,
    trace_list as _trace_list_cmd,
    trace_resume as _trace_resume_cmd,
)
main.add_command(_trace_group, name="trace")
_trail_group.add_command(_trace_resume_cmd, name="resume")

from .dataset import dataset_group as _dataset_group  # noqa: E402
from .workflow import workflow_group as _workflow_group  # noqa: E402
from .skill_verifier import skill_verifier_group as _skill_verifier_group  # noqa: E402
from .bucket import bucket_group as _bucket_group  # noqa: E402
from .security import security_group as _security_group  # noqa: E402

# Plan-077 — Context Tree substrate: ``opentraces ctx`` navigation surface.
from .ctx import ctx_group as _ctx_group  # noqa: E402

# Plan-082 — Agent-to-agent bug capsule: ``opentraces capsule`` share surface.
from .capsule import capsule_group as _capsule_group  # noqa: E402

main.add_command(_bucket_group, name="bucket")
main.add_command(_dataset_group, name="dataset")
main.add_command(_workflow_group, name="workflow")
main.add_command(_skill_verifier_group, name="skill-verifier")
main.add_command(_security_group, name="security")
main.add_command(_ctx_group, name="ctx")
main.add_command(_capsule_group, name="capsule")

# Plan 078: OTLP receiver capture source (third sibling of JSONL + proxy).
from .capture_otlp import (  # noqa: E402
    capture_otlp_group as _capture_otlp_group,
    setup_capture_otlp_cmd as _setup_capture_otlp_cmd,
)
main.add_command(_capture_otlp_group, name="capture-otlp")
_setup_group.add_command(_setup_capture_otlp_cmd, name="capture-otlp")


@main.command("list")
@click.option(
    "--projects", "list_projects", is_flag=True,
    help="List every project that has run `ot init` instead of traces.",
)
@click.option("--remote", "remote_filter", default=None,
              help="Filter to traces missing on the named remote.")
@click.option("--stage", default=None, help="Filter by visible stage")
@click.option("--model", default=None, help="Filter by model")
@click.option("--agent", default=None, help="Filter by agent")
@click.option("--limit", type=int, default=20, help="Max rows to show")
@click.option("--by-commit", is_flag=True, help="Group by commit")
@click.pass_context
def list_cmd(
    ctx, list_projects: bool, remote_filter: str | None,
    stage: str | None, model: str | None, agent: str | None,
    limit: int, by_commit: bool,
) -> None:
    """List traces (or projects with --projects).

    Default: list traces in the local inbox. With --projects, list every
    directory that has run ot init. With --remote <name>, filter traces to
    those missing on that remote.
    """
    # Plan 058 V21: a deprecation warning is wired by ``_wrap_legacy_with_warning``
    # at registration time, so we don't emit it here.
    if list_projects:
        ctx.invoke(projects_list_cmd)
        return
    if remote_filter:
        # Per-remote pending list — uses pending_for() from Step 2.
        from ..core.config import get_project_state_path
        from ..core.state import StateManager
        state_path = get_project_state_path(Path.cwd())
        state = StateManager(state_path=state_path)
        traces = state.pending_for(remote_filter)
        if not traces:
            click.echo(f"No traces pending for remote '{remote_filter}'.")
            emit_json({"status": "ok", "traces": [], "remote": remote_filter})
            return
        for t in traces:
            click.echo(f"  {short_trace_id(t.trace_id, 12)}  status={t.status.value}")
        emit_json({
            "status": "ok",
            "remote": remote_filter,
            "traces": [{"trace_id": t.trace_id, "status": t.status.value} for t in traces],
        })
        return
    # Default: delegate to the trace.list impl.
    ctx.invoke(_trace_list_cmd, stage=stage, model=model, agent=agent, limit=limit, by_commit=by_commit)


@main.command(
    "add",
    examples=[
        "opentraces add abc12",
        "opentraces add abc12 def34",
        "opentraces add --all",
    ],
    see_also=[
        ("opentraces push", "upload staged traces upstream."),
        ("opentraces list", "review what's in the inbox."),
        ("opentraces reject", "mark a trace local-only instead of staging."),
    ],
)
@click.argument("trace_ids", nargs=-1)
@click.option("--all", "stage_all", is_flag=True, help="Stage every Inbox-status trace for push.")
def add_cmd(trace_ids: tuple[str, ...], stage_all: bool) -> None:
    """Stage trace(s) for the next push.

    Variadic: pass one or more ids, or --all to stage every Inbox trace.
    Refuses BLOCKED + REJECTED traces with a clear pointer to ot redact /
    ot reject (Step 8 approval gate).
    """
    from ..core.state import TraceStatus
    from .trace import _trace_commit_impl, _load_project_state

    if not trace_ids and not stage_all:
        click.echo("Pass one or more trace ids, or --all to stage every Inbox trace.", err=True)
        sys.exit(2)

    state, _staging_dir = _load_project_state()

    # Resolve --all to the explicit list of staged trace ids.
    if stage_all:
        staged = state.get_traces_by_status(TraceStatus.STAGED)
        trace_ids = tuple(t.trace_id for t in staged)
        if not trace_ids:
            click.echo("Nothing to stage — inbox is empty.")
            return

    # Step 8 gate: refuse BLOCKED and REJECTED before doing any work.
    refused: list[tuple[str, str, str]] = []  # (id, status, reason)
    for tid in trace_ids:
        # Allow short-id prefix lookup (trace_commit_impl already does
        # full-id lookup; we duplicate a minimal check here for the gate).
        entry = state.get_trace(tid)
        if entry is None:
            # Try short-id prefix match
            matches = [
                e for e in state.get_traces_by_status(TraceStatus.BLOCKED)
                + state.get_traces_by_status(TraceStatus.REJECTED)
                if e.trace_id.startswith(tid)
            ]
            if matches:
                entry = matches[0]
        if entry is None:
            continue  # not blocked/rejected; let _trace_commit_impl handle the not-found
        if entry.status == TraceStatus.BLOCKED:
            refused.append((tid, "blocked", entry.block_reason or "security finding"))
        elif entry.status == TraceStatus.REJECTED:
            refused.append((tid, "rejected", "marked local-only"))

    if refused:
        for tid, status, reason in refused:
            if status == "blocked":
                click.echo(
                    f"Refusing to stage {tid[:12]}: {reason}\n"
                    f"  Run `ot redact {tid[:12]} <pattern>` to clean the offending content,\n"
                    f"  then `ot reset {tid[:12]} && ot add {tid[:12]}` — or `ot reject {tid[:12]}` to keep local-only.",
                    err=True,
                )
            else:
                click.echo(
                    f"Refusing to stage {tid[:12]}: {reason}\n"
                    f"  Run `ot reset {tid[:12]}` first to bring it back to Inbox.",
                    err=True,
                )
        sys.exit(2)

    # Otherwise dispatch to the existing per-trace commit impl.
    for tid in trace_ids:
        _trace_commit_impl(tid)


@main.command(
    "redact",
    examples=[
        "opentraces redact abc12 'sk-live-1234'",
        "opentraces redact abc12 --regex 'sk-[a-z]+-[0-9]+'",
        "opentraces redact abc12 'password' --field observations.stdout",
    ],
    see_also=[
        ("opentraces add", "stage the trace once it's clean."),
        ("opentraces reset", "move a BLOCKED trace back to Inbox after redacting."),
    ],
    option_groups=[
        ("Match", ["use_regex"]),
        ("Scope", ["field", "step_index"]),
    ],
)
@click.argument("trace_id")
@click.argument("pattern")
@click.option("--regex", "use_regex", is_flag=True, help="Interpret PATTERN as a regex.")
@click.option("--field", "field", default=None, help="Restrict to one field path (e.g. observations.stdout).")
@click.option("--step", "step_index", type=int, default=None, help="Restrict to one step index.")
def redact_cmd(trace_id: str, pattern: str, use_regex: bool, field: str | None, step_index: int | None) -> None:
    """Find and replace text content in a trace.

    Default: literal-string find-and-replace across every field of every
    step. Use --regex for pattern matching, --field to scope to one field
    (dotted path supported), --step to scope to one step. Replaces matches
    inline with [REDACTED]. Atomic in-place rewrite. Permanent, no undo.
    """
    from ..core.config import get_project_state_path, get_project_traces_dir
    from ..core.review import redact_pattern_and_persist
    from ..core.state import StateManager

    project_dir = Path.cwd()
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)
    staging_dir = get_project_traces_dir(project_dir)

    entry = state.get_trace(trace_id)
    if entry is None:
        # Try short-id prefix
        matches = [
            t for t in state._state.get("traces", {}).values()
            if t.get("trace_id", "").startswith(trace_id)
        ]
        if not matches:
            click.echo(f"Trace not found: {trace_id}", err=True)
            sys.exit(6)
        trace_id = matches[0]["trace_id"]
        entry = state.get_trace(trace_id)

    result = redact_pattern_and_persist(
        staging_dir, trace_id, pattern,
        regex=use_regex, field=field, step=step_index,
    )

    if hasattr(result, "error_code") and result.error_code:
        click.echo(f"redact failed: {result.error_message}", err=True)
        sys.exit(2)

    click.echo(f"Redacted {short_trace_id(trace_id, 12)} ({getattr(result, 'replacements', '?')} replacement(s))")
    emit_json({
        "status": "ok",
        "trace_id": trace_id,
        "replacements": getattr(result, "replacements", None),
    })


@main.group(invoke_without_command=True)
@click.pass_context
def remote(ctx) -> None:
    """Legacy project remote commands; use ``ot dataset remote``."""
    if ctx.invoked_subcommand is None:
        project_dir = Path.cwd()
        proj_config, remotes = _read_remotes(project_dir)
        active = proj_config.get("active_remote")
        legacy = proj_config.get("remote") if not remotes else None
        if not remotes and not legacy:
            click.echo("No remote connected.")
            click.echo("  opentraces remote add <owner/name>     connect to an existing HF dataset")
            click.echo("  opentraces remote create <owner/name>  create a new one")
            emit_json({"status": "ok", "remote": None})
            return
        if legacy:
            vis = proj_config.get("visibility", "private")
            click.echo(f"{legacy} ({vis})")
            emit_json({"status": "ok", "remote": legacy, "visibility": vis})
            return
        name = active or next(iter(remotes))
        cfg = remotes[name]
        click.echo(f"{name} ({cfg.get('visibility', 'private')})")
        emit_json({"status": "ok", "remote": name, "visibility": cfg.get("visibility")})


# ---------------------------------------------------------------------------
# Helpers: URL expansion, remotes-dict access, repo_id resolution, HF probes.
# HF helpers are factored as module-level so tests can monkeypatch without
# reaching into ``huggingface_hub``.
# ---------------------------------------------------------------------------


def _expand_hf_url(url: str) -> str:
    """Expand short-form ``user/repo`` to ``hf://user/repo``.

    Full URLs (anything containing ``://``) are returned unchanged.
    """
    if "://" in url:
        return url
    if "/" in url:
        return f"hf://{url}"
    return url


def _read_remotes(project_dir: Path) -> tuple[dict, dict]:
    """Return (proj_config_dict, remotes_dict). remotes is the live ref.

    Strips the legacy ``remote`` / ``visibility`` keys that
    ``load_project_config`` synthesizes for back-compat callers — if we
    handed them straight back to ``save_project_config`` they would be
    interpreted as legacy migration triggers and re-create an ``origin``
    entry on the next write.
    """
    proj_config = load_project_config(project_dir)
    proj_config.pop("remote", None)
    proj_config.pop("visibility", None)
    remotes = proj_config.get("remotes")
    if remotes is None:
        proj_config["remotes"] = {}
        remotes = proj_config["remotes"]
    return proj_config, remotes


def _normalize_repo_id(repo: str, username_hint: str | None = None) -> str:
    """Normalize a user-provided identifier to ``owner/name``.

    Accepts ``owner/name``, bare ``name`` (prefixed with the authenticated
    HF user), or ``hf://owner/name``. Bare names require an authenticated
    user to resolve the prefix.
    """
    if "://" in repo:
        repo = repo.split("://", 1)[1]
    if "/" in repo:
        return repo
    if not username_hint:
        raise click.UsageError(
            f"'{repo}' is a short name. Use '<owner>/{repo}' or "
            "'opentraces auth login' first so we can resolve your HF user."
        )
    return f"{username_hint}/{repo}"


def _remote_probe(repo_id: str, token: str | None) -> dict | None:
    """Return dataset metadata dict if the repo exists on HF, None if not.

    Raises on transport errors so callers can distinguish "missing" from
    "network broken". Includes the canonical ``id`` from HF so callers can
    normalise casing (HF matching is case-insensitive but we want the
    locally-persisted repo_id to match the upstream canonical form).
    """
    from huggingface_hub import HfApi
    try:
        from huggingface_hub.errors import RepositoryNotFoundError
    except ImportError:  # older huggingface_hub
        from huggingface_hub.utils import RepositoryNotFoundError  # type: ignore

    api = HfApi(token=token)
    try:
        info = api.dataset_info(repo_id)
    except RepositoryNotFoundError:
        return None
    return {
        "id": getattr(info, "id", repo_id),
        "private": bool(getattr(info, "private", False)),
    }


def _remote_create(repo_id: str, private: bool, token: str | None) -> bool:
    """Create an HF dataset. Returns True on create, False if it already existed."""
    from huggingface_hub import HfApi
    try:
        from huggingface_hub.errors import HfHubHTTPError
    except ImportError:
        from huggingface_hub.utils import HfHubHTTPError  # type: ignore

    api = HfApi(token=token)
    try:
        api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=False)
    except HfHubHTTPError as e:
        msg = str(e).lower()
        if "already" in msg or "409" in msg or "conflict" in msg:
            return False
        raise
    return True


def _remote_delete(repo_id: str, token: str | None) -> None:
    """Delete an HF dataset."""
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    api.delete_repo(repo_id=repo_id, repo_type="dataset")


def _remote_set_visibility(repo_id: str, private: bool, token: str | None) -> None:
    """Flip an HF dataset between private and public."""
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    api.update_repo_settings(repo_id=repo_id, repo_type="dataset", private=private)


def _classify_hf_repo_error(exc: Exception, repo_id: str) -> tuple[str, str, str, str]:
    """Turn a huggingface_hub exception into actionable CLI guidance.

    Returns ``(code, kind, message, hint)``. ``kind`` is one of
    ``remote_missing``, ``namespace_forbidden``, ``auth``, ``unknown``.
    Callers decide how to present (human echo + JSON envelope).
    """
    try:
        from huggingface_hub.errors import (
            RepositoryNotFoundError,
            HfHubHTTPError,
        )
    except ImportError:  # older huggingface_hub
        from huggingface_hub.utils import (  # type: ignore
            RepositoryNotFoundError,
            HfHubHTTPError,
        )

    if isinstance(exc, RepositoryNotFoundError):
        return (
            "REMOTE_NOT_FOUND",
            "remote_missing",
            f"No dataset at {repo_id} on HuggingFace.",
            f"Run: opentraces dataset remote create <name> {repo_id}",
        )

    status = None
    if isinstance(exc, HfHubHTTPError):
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)

    msg = str(exc).lower()
    if status == 403 or "403" in msg or "forbidden" in msg or "don't have the rights" in msg:
        owner = repo_id.split("/", 1)[0] if "/" in repo_id else repo_id
        return (
            "NAMESPACE_FORBIDDEN",
            "namespace_forbidden",
            f"You don't have write access to the '{owner}' namespace on HuggingFace.",
            (
                f"Join the '{owner}' org, or pick a different namespace "
                f"(e.g. your own user) with 'opentraces dataset remote create'."
            ),
        )

    if status == 401 or "401" in msg or "unauthor" in msg or "invalid token" in msg:
        return (
            "AUTH_FAILED",
            "auth",
            "HuggingFace rejected the current credentials.",
            "Run: opentraces auth login",
        )

    return (
        "HF_ERROR",
        "unknown",
        f"HuggingFace request failed: {exc}",
        None,
    )


def _fail_hf_or_reraise(exc: Exception, repo_id: str) -> None:
    """Exit 3 with actionable guidance for HuggingFace / network errors.

    Shared by the dataset + bucket remote CLI surfaces so a rejected/forbidden
    token (or a network outage) produces a clean message instead of a raw
    traceback. Re-raises anything that is NOT an HF HTTP error or a network
    error so genuine programming bugs still surface.
    """
    try:
        from huggingface_hub.errors import HfHubHTTPError
    except ImportError:  # older huggingface_hub
        from huggingface_hub.utils import HfHubHTTPError  # type: ignore
    # OSError covers requests' ConnectionError/Timeout (RequestException -> IOError).
    if not isinstance(exc, (HfHubHTTPError, OSError)):
        raise exc
    _code, _kind, message, hint = _classify_hf_repo_error(exc, repo_id)
    click.echo(message, err=True)
    if hint:
        click.echo(hint, err=True)
    sys.exit(3)


@remote.command("add")
@click.argument("repo")
def remote_add(repo: str) -> None:
    """Connect to an existing HF dataset.

    REPO is ``owner/name``, a bare ``name`` (prefixed with your HF user),
    or ``hf://owner/name``. The dataset must already exist on HuggingFace;
    use ``opentraces remote create`` to make a new one.
    """
    cfg = load_config()
    identity = _auth_identity(cfg.hf_token) if cfg.hf_token else None
    username = identity.get("name") if identity else None
    try:
        repo_id = _normalize_repo_id(repo, username)
    except click.UsageError as e:
        click.echo(str(e), err=True)
        sys.exit(2)

    project_dir = Path.cwd()
    proj_config, remotes = _read_remotes(project_dir)
    if repo_id in remotes:
        click.echo(f"{repo_id} is already connected.", err=True)
        emit_json(error_response("ALREADY_CONNECTED", "remote",
                                 f"Remote {repo_id} already connected", None))
        sys.exit(2)

    try:
        info = _remote_probe(repo_id, cfg.hf_token)
    except Exception as e:
        click.echo(f"Failed to verify dataset on HuggingFace: {e}", err=True)
        emit_json(error_response("HF_ERROR", "remote", str(e), None))
        sys.exit(3)
    if info is None:
        click.echo(f"No dataset at {repo_id} on HuggingFace.", err=True)
        click.echo(f"  hint: opentraces remote create {repo_id}", err=True)
        emit_json(error_response("NOT_FOUND", "remote",
                                 f"Dataset {repo_id} does not exist on HF",
                                 f"Run: opentraces remote create {repo_id}"))
        sys.exit(3)

    visibility = "private" if info.get("private") else "public"
    full_url = _expand_hf_url(repo_id)
    remotes[repo_id] = {"url": full_url, "visibility": visibility}
    if not proj_config.get("active_remote"):
        proj_config["active_remote"] = repo_id
    save_project_config(project_dir, proj_config)
    click.echo(f"Connected to {repo_id} ({visibility})")
    emit_json({"status": "ok", "remote": repo_id, "visibility": visibility, "url": full_url})


@remote.command("create")
@click.argument("repo")
@click.option("--private/--public", "is_private", default=True,
              help="Dataset visibility on HuggingFace (default: private).")
def remote_create(repo: str, is_private: bool) -> None:
    """Create a new HF dataset and connect it.

    Fails if REPO already exists on HuggingFace; use
    ``opentraces remote add`` instead.
    """
    cfg = load_config()
    if not cfg.hf_token:
        click.echo("Not authenticated. Run 'opentraces auth login' first.", err=True)
        sys.exit(3)
    identity = _auth_identity(cfg.hf_token)
    username = identity.get("name") if identity else None
    try:
        repo_id = _normalize_repo_id(repo, username)
    except click.UsageError as e:
        click.echo(str(e), err=True)
        sys.exit(2)

    project_dir = Path.cwd()
    proj_config, remotes = _read_remotes(project_dir)
    if repo_id in remotes:
        click.echo(f"{repo_id} is already connected.", err=True)
        sys.exit(2)

    try:
        created = _remote_create(repo_id, is_private, cfg.hf_token)
    except Exception as e:
        code, kind, message, hint = _classify_hf_repo_error(e, repo_id)
        click.echo(message, err=True)
        if hint:
            click.echo(f"  hint: {hint}", err=True)
        emit_json(error_response(code, kind, message, hint))
        sys.exit(3)
    if not created:
        click.echo(f"{repo_id} already exists on HuggingFace.", err=True)
        click.echo(f"  hint: opentraces remote add {repo_id}", err=True)
        emit_json(error_response("ALREADY_EXISTS", "remote",
                                 f"Dataset {repo_id} already exists",
                                 f"Run: opentraces remote add {repo_id}"))
        sys.exit(3)

    visibility = "private" if is_private else "public"
    full_url = _expand_hf_url(repo_id)
    remotes[repo_id] = {"url": full_url, "visibility": visibility}
    if not proj_config.get("active_remote"):
        proj_config["active_remote"] = repo_id
    save_project_config(project_dir, proj_config)
    click.echo(f"Created {repo_id} ({visibility}) and connected.")
    emit_json({"status": "ok", "remote": repo_id, "visibility": visibility,
               "url": full_url, "created": True})


@remote.command("remove")
@click.argument("repo", required=False, default=None)
@click.option("--delete-remote", is_flag=True,
              help="Also delete the dataset on HuggingFace (irreversible).")
@click.option("--yes", "confirmed", is_flag=True, help="Skip the confirmation prompt.")
def remote_remove(repo: str | None, delete_remote: bool, confirmed: bool) -> None:
    """Disconnect a remote from this project (local-only by default).

    By itself, this does NOT delete the dataset on HuggingFace, the
    repository and its data stay intact and can be reconnected later with
    ``opentraces remote add``. To also delete upstream, pass
    ``--delete-remote`` (or use ``opentraces remote delete <repo>``).

    REPO is optional when exactly one remote is connected.
    """
    from ..core.config import get_project_state_path
    from ..core.state import StateManager, UnknownRemoteError

    project_dir = Path.cwd()
    proj_config, remotes = _read_remotes(project_dir)

    if not remotes:
        click.echo("No remote connected.", err=True)
        sys.exit(2)

    if repo is None:
        if len(remotes) == 1:
            repo = next(iter(remotes))
        else:
            click.echo("Multiple remotes connected; specify which to remove:", err=True)
            for r in sorted(remotes):
                click.echo(f"  {r}", err=True)
            sys.exit(2)

    if repo not in remotes:
        click.echo(f"No remote '{repo}'.", err=True)
        sys.exit(2)

    if delete_remote and not confirmed:
        # ADR-0007 lint L2: destructive confirm must not prompt under --json /
        # non-TTY; refuse with a structured error naming the --yes bypass.
        require_interactive(
            "remote remove",
            "pass --yes to confirm the irreversible remote deletion non-interactively",
        )
        click.echo(f"About to delete {repo} on HuggingFace (irreversible).")
        if not click.confirm("Proceed?"):
            click.echo("Cancelled.")
            sys.exit(1)

    if delete_remote:
        cfg = load_config()
        if not cfg.hf_token:
            click.echo("Not authenticated. Run 'opentraces auth login' first.", err=True)
            sys.exit(3)
        try:
            _remote_delete(repo, cfg.hf_token)
            click.echo(f"Deleted {repo} on HuggingFace.")
        except Exception as e:
            click.echo(f"Failed to delete on HuggingFace: {e}", err=True)
            sys.exit(3)

    del remotes[repo]
    if proj_config.get("active_remote") == repo:
        proj_config["active_remote"] = next(iter(remotes), None)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)
    try:
        state.forget_remote(repo)
    except UnknownRemoteError:
        pass
    save_project_config(project_dir, proj_config)
    click.echo(f"Disconnected {repo}.")
    emit_json({"status": "ok", "remote": repo, "deleted_remote": delete_remote})


@remote.command("visibility")
@click.argument("repo", required=False, default=None)
@click.option("--private", "make_private", flag_value=True, default=None,
              help="Set the dataset to private.")
@click.option("--public", "make_private", flag_value=False,
              help="Set the dataset to public.")
def remote_visibility(repo: str | None, make_private: bool | None) -> None:
    """Flip a connected remote between private and public on HuggingFace.

    REPO is optional when exactly one remote is connected. Requires an
    authenticated session with ``manage-repos`` scope (the default from
    ``opentraces auth login``).
    """
    if make_private is None:
        click.echo("Specify --private or --public.", err=True)
        sys.exit(2)

    project_dir = Path.cwd()
    proj_config, remotes = _read_remotes(project_dir)
    if not remotes:
        click.echo("No remote connected.", err=True)
        sys.exit(2)
    if repo is None:
        if len(remotes) == 1:
            repo = next(iter(remotes))
        else:
            click.echo("Multiple remotes connected; specify which:", err=True)
            for r in sorted(remotes):
                click.echo(f"  {r}", err=True)
            sys.exit(2)
    if repo not in remotes:
        click.echo(f"No remote '{repo}'.", err=True)
        sys.exit(2)

    cfg = load_config()
    if not cfg.hf_token:
        click.echo("Not authenticated. Run 'opentraces auth login' first.", err=True)
        sys.exit(3)

    # Stored ``url`` is ``hf://owner/name`` for new-style remotes; HfApi
    # needs the bare ``owner/name``. Legacy ``origin``-keyed remotes store
    # the repo_id directly in ``url``.
    stored_url = remotes[repo].get("url", repo)
    repo_id = stored_url.split("://", 1)[1] if "://" in stored_url else stored_url
    try:
        _remote_set_visibility(repo_id, make_private, cfg.hf_token)
    except Exception as e:
        code, kind, message, hint = _classify_hf_repo_error(e, repo_id)
        click.echo(message, err=True)
        if hint:
            click.echo(f"  hint: {hint}", err=True)
        emit_json(error_response(code, kind, message, hint))
        sys.exit(3)

    visibility = "private" if make_private else "public"
    remotes[repo]["visibility"] = visibility
    save_project_config(project_dir, proj_config)
    click.echo(f"{repo_id} is now {visibility}.")
    emit_json({"status": "ok", "remote": repo, "visibility": visibility})


@remote.command("delete")
@click.argument("repo", required=False, default=None)
@click.option("--yes", "confirmed", is_flag=True, help="Skip the confirmation prompt.")
def remote_delete(repo: str | None, confirmed: bool) -> None:
    """Delete the dataset on HuggingFace AND disconnect locally (destructive).

    REPO is optional when exactly one remote is connected. For a local-only
    disconnect, use ``opentraces remote remove`` instead.
    """
    # Delegate to the existing remove-with-delete path so we have one code
    # path for both surfaces. This preserves the state-manager / config
    # cleanup and the confirmation prompt.
    ctx = click.get_current_context()
    ctx.invoke(remote_remove, repo=repo, delete_remote=True, confirmed=confirmed)


@remote.command("list")
@click.option("-v", "verbose", is_flag=True, help="Also show full URLs.")
def remote_list(verbose: bool) -> None:
    """List connected remotes (active marked with *)."""
    proj_config, remotes = _read_remotes(Path.cwd())
    active = proj_config.get("active_remote")

    if not remotes:
        click.echo("No remotes connected.")
        emit_json({"status": "ok", "remotes": {}, "active_remote": None})
        return

    payload = {}
    for name, cfg in sorted(remotes.items()):
        marker = "*" if name == active else " "
        visibility = cfg.get("visibility", "private")
        url = cfg.get("url", name)
        # Legacy-migrated remotes are keyed ``origin`` with the repo_id in
        # ``url``; new-style remotes are keyed by repo_id. Always print the
        # repo_id so the user can confirm which HF dataset is wired up.
        if name == url:
            line = f"  {marker} {name} ({visibility})"
        else:
            line = f"  {marker} {name} \u2192 {url} ({visibility})"
        if verbose:
            line = f"{line}\t{_expand_hf_url(url)}"
        click.echo(line)
        payload[name] = cfg

    emit_json({"status": "ok", "remotes": payload, "active_remote": active})


# Step 4 also rebinds bare `ot remote remove <name>` semantics — the existing
# no-arg form (line ~2013) is kept for back-compat and removed in step 15.
# Click does NOT support overloading commands, so the new name-taking remove
# is exposed as the hidden `remote remove-named` + `remote rm` aliases above.
# Step 15 will rename remove-named -> remove and delete the legacy no-arg form.


@main.command(hidden=True)
@click.option("--auto", is_flag=True, help="Auto-approve (skip review)")
@click.option("--limit", type=int, default=0, help="Max traces to parse (0=all)")
def parse(auto: bool, limit: int) -> None:
    """Deprecated: the watcher now ingests sessions automatically.

    For a forced manual pass, use ``opentraces _scan`` from inside an
    opted-in project.
    """
    click.echo(
        "opentraces parse is deprecated — the watcher keeps the inbox "
        "in sync automatically.\n"
        "For a manual pass, run `opentraces _scan` inside the project.",
        err=True,
    )
    sys.exit(2)


def _capture_project_root(path: Path) -> Path:
    """Resolve an agent cwd to the project root used for capture."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
        if out:
            return Path(out).resolve()
    except Exception:
        pass
    return path.resolve()


@main.command("_pi-bridge", hidden=True)
@click.option("--status", "status_only", is_flag=True, help="Print Pi bridge status.")
@click.option("--cwd", "cwd_override", default=None, type=click.Path(), help="Project cwd for status/payload.")
@click.option("--payload", default=None, help="JSON payload to record (prefer --payload-file for large events).")
@click.option("--payload-file", default=None, type=click.Path(), help="Path to JSON payload to record.")
@click.option("--strict", is_flag=True, help="Raise bridge validation errors instead of fail-open JSON.")
def _pi_bridge_cmd(
    status_only: bool,
    cwd_override: str | None,
    payload: str | None,
    payload_file: str | None,
    strict: bool,
) -> None:
    """Hidden argv-safe Pi extension bridge.

    The public Pi package calls this command instead of importing Python with
    system ``python3`` so pipx/uv-tool installs work and the bridge runs in the
    same environment as the OpenTraces CLI.
    """
    from ..capture.pi.bridge import BridgeResult, bridge_status, record_event

    if status_only:
        click.echo(json.dumps(bridge_status(cwd_override), sort_keys=True))
        return

    try:
        if payload_file:
            raw_payload = Path(payload_file).read_text(encoding="utf-8")
        elif payload is not None:
            raw_payload = payload
        else:
            raw_payload = sys.stdin.read()
        event = json.loads(raw_payload)
    except Exception as exc:  # noqa: BLE001 - hidden fail-open bridge surface
        if strict:
            raise
        click.echo(json.dumps(BridgeResult(ok=False, status="error", errors=[str(exc)]).to_json(), sort_keys=True))
        return
    if cwd_override and isinstance(event, dict) and not event.get("cwd"):
        event["cwd"] = cwd_override
    result = record_event(event, fail_open=not strict)
    click.echo(json.dumps(result.to_json(), sort_keys=True))


@main.command("_ingest-session", hidden=True)
@click.argument("transcript_path", type=click.Path())
@click.option("--project", "project_override", type=click.Path(),
              default=None,
              help="Resolve against a specific project (default: cwd).")
@click.option("--agent", "agent_name", default="claude-code",
              help="Registered parser name for this session.")
def _ingest_session(
    transcript_path: str,
    project_override: str | None,
    agent_name: str,
) -> None:
    """Ingest one native agent session invoked fire-and-forget by a Stop hook.

    Contract: must exit 0 quickly and silently in every reasonable
    failure mode (missing file, non-enlisted project, parse failure).
    Any stderr noise propagates into the agent's hook telemetry and
    degrades the user's session; it's not worth it. The watcher sweep
    will recover anything this path misses.
    """
    # Hard self-timeout: this child is fire-and-forget and unreaped, so it
    # must never pin a core indefinitely. If ingest hasn't finished within
    # the budget, exit hard — the watcher sweep recovers anything missed.
    import os
    import signal

    def _bail(_signum: int, _frame: object) -> None:
        os._exit(0)

    watchdog_armed = False
    try:
        signal.signal(signal.SIGALRM, _bail)
        signal.alarm(180)
        watchdog_armed = True
    except (ValueError, OSError):
        pass  # not the main thread / platform without SIGALRM

    try:
        path = Path(transcript_path)
        if not path.exists():
            return  # vanished transcript — nothing to do

        if project_override:
            project_dir = _capture_project_root(Path(project_override))
        else:
            project_dir = _capture_project_root(Path.cwd())

        if not (project_dir / ".opentraces.json").exists():
            from ..core.config import auto_enroll_if_global

            auto_enroll_if_global(project_dir)

        if not (project_dir / ".opentraces.json").exists():
            return  # not enlisted — watcher on other projects will catch it

        from ..core.ingest import ingest_one_session
        # Don't block behind another in-flight ingest for this session; a
        # follower that can't get the lock skips (idempotent + watcher net).
        ingest_one_session(
            path,
            project_dir,
            wait_for_lock=False,
            parser_name=agent_name,
        )
    except Exception:  # noqa: BLE001
        # Belt: never let a hook break the user's agent session.
        return
    finally:
        if watchdog_armed:
            try:
                signal.alarm(0)
            except (ValueError, OSError):
                pass


@main.command("_scan", hidden=True)
@click.option("--reparse", is_flag=True,
              help="Force re-derivation even if a session hasn't grown "
                   "(e.g. after a schema bump).")
@click.option("--session", "session_filter", type=str, default=None,
              help="Limit to a single session_id (JSONL basename).")
@click.option("--dry-run", is_flag=True,
              help="Report what would change without writing state.")
@click.option("--trace-record-only", is_flag=True,
              help="Backfill TraceRecords/raw source only; skip Trail/Context projections.")
@click.option("--project", "project_override", type=click.Path(),
              default=None,
              help="Run against an opted-in project other than the cwd.")
def _scan(reparse: bool, session_filter: str | None,
          dry_run: bool, trace_record_only: bool,
          project_override: str | None) -> None:
    """Manually re-sync the current project's inbox from its JSONL corpus.

    Hidden because the Stop hook + watcher sweep keep the inbox live
    without user intervention. Kept available for testing, post-upgrade
    reparse, and recovering from a missed hook fire.
    """
    from ..capture import session_id_from_path
    from ..core.ingest import discover_project_ingest_candidates, scan_project

    project_dir = _capture_project_root(
        Path(project_override) if project_override else Path.cwd()
    )

    if not dry_run:
        from ..core.config import auto_enroll_if_global

        auto_enroll_if_global(project_dir)

    if not (project_dir / ".opentraces.json").exists():
        click.echo(
            f"No opted-in project at {project_dir}. Run `opentraces init` first.",
            err=True,
        )
        sys.exit(4)

    paths: list[tuple[str, Path]] | None = None
    if session_filter:
        # Narrow the corpus to the requested session. We still go through
        # scan_project so the per-session rules (locks, state, etc.) are
        # applied uniformly.
        all_paths = discover_project_ingest_candidates(project_dir)
        paths = [
            (agent_name, p)
            for agent_name, p in all_paths
            if session_id_from_path(agent_name, p) == session_filter
        ]
        if not paths:
            click.echo(
                f"No JSONL found for session_id={session_filter} "
                f"under this project's raw agent corpus. Raw Claude/Codex "
                "session files are machine-local and may be absent even when "
                "the retained TraceRecord exists in the bucket; try `trace get` "
                "or rerun backfill on the source machine.",
                err=True,
            )
            sys.exit(3)

    if dry_run:
        # Dry-run mode: enumerate candidates without calling scan_project.
        # A full "would-do" report would need a parse-and-diff pass; for
        # Phase 1 we report the corpus size and what the action WOULD be
        # based on current state (new | refreshed | new_generation | noop).
        _emit_dry_run(project_dir, paths=paths)
        return

    report = scan_project(
        project_dir,
        reparse=reparse,
        paths=paths,
        trace_record_only=trace_record_only,
    )

    payload = {
        "project": str(project_dir),
        "sessions_seen": len(report.results),
        "created": report.created,
        "refreshed": report.refreshed,
        "new_generations": report.new_generations,
        "noops": report.noops,
        "errored": report.errored,
        "results": [
            {
                "session_id": r.session_id,
                "action": r.action,
                "trace_id": r.trace_id,
                "supersedes": r.supersedes,
                "supersedes_reason": r.supersedes_reason,
                "error": r.error,
            }
            for r in report.results
        ],
    }
    emit_json(payload)
    if _json_mode:
        return

    # Human-readable summary — terse.
    click.echo(
        f"scan {project_dir}: "
        f"new={report.created} refreshed={report.refreshed} "
        f"new_gen={report.new_generations} noop={report.noops} "
        f"err={report.errored}"
    )


def _emit_dry_run(
    project_dir: Path,
    *,
    paths: list[Path | tuple[str, Path]] | None,
) -> None:
    """Dry-run report: what would `_scan` do, given current state?"""
    from ..capture import session_id_from_path
    from ..core.config import get_project_state_path
    from ..core.ingest import (  # noqa: SLF001 — shared helper
        _has_grown,
        discover_project_ingest_candidates,
    )
    from ..core.state import StateManager, TraceStatus

    terminal = {
        TraceStatus.UPLOADED.value, TraceStatus.REJECTED.value,
        TraceStatus.COMMITTED.value, TraceStatus.FAILED.value,
    }

    if paths is not None:
        candidates = [
            (str(item[0]), Path(item[1]))
            if isinstance(item, tuple)
            else ("claude-code", Path(item))
            for item in paths
        ]
    else:
        candidates = discover_project_ingest_candidates(project_dir)
    state = StateManager(state_path=get_project_state_path(project_dir))

    would: list[dict] = []
    counts = {"new": 0, "refreshed": 0, "new_generation": 0, "noop": 0}
    for agent_name, p in candidates:
        sid = session_id_from_path(agent_name, p)
        sess = state.get_session(sid)
        if sess is None:
            action = "new"
        elif not _has_grown(p, sess.observed_size, sess.observed_mtime):
            action = "noop"
        else:
            latest = sess.generations[-1] if sess.generations else None
            if latest is None:
                action = "new"
            else:
                entry = state.get_trace(latest.trace_id)
                if entry and entry.status == TraceStatus.BLOCKED.value:
                    action = "noop"
                elif entry and entry.status in terminal:
                    action = "new_generation"
                else:
                    action = "refreshed"
        counts[action] = counts.get(action, 0) + 1
        would.append({
            "session_id": sid,
            "agent": agent_name,
            "action": action,
            "source_path": str(p),
        })

    payload = {
        "project": str(project_dir),
        "dry_run": True,
        "sessions_seen": len(candidates),
        "would": would,
        **counts,
    }
    emit_json(payload)
    if _json_mode:
        return
    click.echo(
        f"dry-run {project_dir}: "
        f"new={counts['new']} refreshed={counts['refreshed']} "
        f"new_gen={counts['new_generation']} noop={counts['noop']}"
    )


def _resolve_repo_id(username: str, repo_flag: str | None = None) -> str:
    """Resolve the HF dataset repo_id using priority chain.

    Priority:
      1. --repo flag (highest)
      2. .opentraces/config.json 'remote' field
      3. Default: {username}/opentraces
    """
    if repo_flag:
        return repo_flag

    from ..core.config import load_project_config
    proj_config = load_project_config(Path.cwd())
    config_remote = proj_config.get("remote")
    if config_remote:
        return config_remote

    return f"{username}/opentraces"


@main.command(hidden=True)
def migrate() -> None:
    """Check schema version and run migrations if needed."""
    from opentraces_schema import SCHEMA_VERSION

    cfg = load_config()
    click.echo(f"Config version: {cfg.config_version}")
    click.echo(f"Schema version: {SCHEMA_VERSION}")
    emit_json({
        "status": "ok",
        "config_version": cfg.config_version,
        "schema_version": SCHEMA_VERSION,
    })


@main.command("_migrate-trace-ids", hidden=True)
@click.option("--dry-run", is_flag=True, help="Report what would change without writing.")
def _migrate_trace_ids_cmd(dry_run: bool) -> None:
    """Rewrite legacy ``<agent>_<session>`` trace ids to canonical UUIDv4."""
    from ..core.migrate_trace_ids import migrate_project

    project_dir = Path.cwd()
    report = migrate_project(project_dir, dry_run=dry_run)
    click.echo(
        f"scanned={report.traces_scanned}  "
        f"migrated={report.traces_migrated}  "
        f"state_rekeyed={report.state_entries_rekeyed}  "
        f"generations_updated={report.generations_updated}  "
        f"commit_groups_updated={report.commit_groups_updated}  "
        f"attribution_files_updated={report.attribution_files_updated}"
    )
    for err in report.errors:
        click.echo(f"  error: {err}", err=True)
    emit_json({
        "status": "ok",
        "dry_run": dry_run,
        "traces_scanned": report.traces_scanned,
        "traces_migrated": report.traces_migrated,
        "state_entries_rekeyed": report.state_entries_rekeyed,
        "generations_updated": report.generations_updated,
        "errors": report.errors,
    })


@main.command(hidden=True)
@click.option("--json", "as_json", is_flag=True, default=True)
def capabilities(as_json: bool) -> None:
    """Show machine-discoverable feature list."""
    from opentraces_schema import SCHEMA_VERSION

    from ..capture import get_parsers

    caps = {
        "name": "opentraces",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "agents": sorted(get_parsers()),
        "modes": ["auto", "review"],
        "features": [
            "passive_capture",
            "claude_code_capture",
            "codex_cli_capture",
            "pi_capture",
            "git_post_commit_correlation",
            "private_bucket",
            "bucket_remote_sync",
            "trace_index",
            "trace_query",
            "trace_map",
            "trace_slice",
            "trace_teleport",
            "trace_trails",
            "context_tree",
            "otlp_receiver",
            "workflow_templates",
            "dataset_workflows",
            "dataset_review_cli",
            "dataset_sharded_publish",
            "security_tool_registry",
            "post_processors",
        ],
        "env_vars": {
            "HF_TOKEN": "HuggingFace access token (highest priority over saved credentials)",
        },
    }
    click.echo(json.dumps(caps, indent=2))


def _drop_command(name: str) -> None:
    """Remove ``name`` from the root group if it's currently registered."""
    try:
        del main.commands[name]
    except KeyError:
        pass


# Unreleased development surface: remove old flat inbox/project commands
# instead of carrying compatibility aliases. The canonical public roots are
# the sectioned setup, trace/trail/context, bucket, workflow/dataset,
# security, capture, and maintenance surfaces declared in COMMAND_SECTIONS.
for _legacy_root_command in [
    "list",
    "add",
    "push",
    "pull",
    "remote",
    "redact",
    "llm-review",
    "stats",
    "log",
    "graph",
]:
    _drop_command(_legacy_root_command)


@main.command(hidden=True)
def introspect() -> None:
    """Show full API schema for machine discovery."""
    from opentraces_schema import TraceRecord, SCHEMA_VERSION

    def _jsonable_default(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [
                item if item is None or isinstance(item, (str, int, float, bool)) else repr(item)
                for item in value
            ]
        if isinstance(value, dict):
            return {str(k): repr(v) for k, v in value.items()}
        return repr(value)

    def _option_schema(param: click.Option) -> dict[str, object]:
        return {
            "name": param.name,
            "opts": list(param.opts),
            "secondary_opts": list(param.secondary_opts),
            "help": param.help,
            "required": param.required,
            "multiple": param.multiple,
            "is_flag": param.is_flag,
            "default": _jsonable_default(param.default),
            "type": getattr(param.type, "name", repr(param.type)),
        }

    def _argument_schema(param: click.Argument) -> dict[str, object]:
        return {
            "name": param.name,
            "required": param.required,
            "nargs": param.nargs,
            "type": getattr(param.type, "name", repr(param.type)),
        }

    def _command_schema(
        name: str,
        command: click.Command,
        parent_ctx: click.Context | None,
    ) -> dict[str, object]:
        ctx = click.Context(command, info_name=name, parent=parent_ctx)
        params = command.get_params(ctx)
        payload: dict[str, object] = {
            "name": name,
            "help": command.help or command.short_help or "",
            "short_help": command.get_short_help_str(limit=120) or "",
            "hidden": bool(command.hidden),
            "options": [
                _option_schema(param)
                for param in params
                if isinstance(param, click.Option)
            ],
            "arguments": [
                _argument_schema(param)
                for param in params
                if isinstance(param, click.Argument)
            ],
        }
        if isinstance(command, click.Group):
            children: dict[str, object] = {}
            for child_name in command.list_commands(ctx):
                child = command.get_command(ctx, child_name)
                if child is None:
                    continue
                children[child_name] = _command_schema(child_name, child, ctx)
            payload["children"] = children
        return payload

    root_ctx = click.Context(main, info_name="opentraces")
    command_schema = {
        name: _command_schema(name, command, root_ctx)
        for name, command in sorted(main.commands.items())
    }

    schema = {
        "name": "opentraces",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "trace_record_schema": TraceRecord.model_json_schema(),
        "commands": command_schema,
        "exit_codes": {
            "0": "OK",
            "2": "Usage error (bad flags or conflicting options)",
            "3": "Auth/config error (not authenticated, not initialized)",
            "4": "Network error",
            "5": "Data corrupt",
            "6": "Not found (trace, project, or resource)",
            "7": "Lock/busy (concurrent local operation or remote sync lock)",
        },
    }
    click.echo(json.dumps(schema, indent=2))
