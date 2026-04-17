"""Hidden ``ot __complete`` dynamic-completion resolver.

Installed shell scripts delegate every completion query here. The resolver
walks the Click command tree and emits TSV lines:

    <section>\\t<name>\\t<description>

Sections:
    core / inbox / project / resource  — root-level command groupings
    flag                                — option flags (``--foo``)
    value                               — argument values (e.g. trace IDs)
    ""                                  — ungrouped (nested subcommand names)

Shells parse the TSV:
    zsh  — groups by section and renders each as its own menu block
    fish — uses ``<name>\\t<description>`` natively
    bash — takes just the name column (no description support)

Keeping all semantic logic here means the shell scripts never need
regenerating when verbs, flags, or value sources change.
"""

from __future__ import annotations

import time
from pathlib import Path

import click


def _walk(root: click.Command, tokens: list[str]) -> tuple[click.Command, str]:
    """Walk the command tree following ``tokens``; return (command, partial).

    The final token (possibly empty) is treated as the in-progress word.
    Non-matching tokens stop the descent — anything after them counts as
    positional args against the deepest matched command.
    """
    if not tokens:
        return root, ""
    current: click.Command = root
    for tok in tokens[:-1]:
        if isinstance(current, click.Group) and tok in current.commands:
            current = current.commands[tok]
        else:
            return current, tokens[-1]
    return current, tokens[-1]


def _section_of(name: str) -> str:
    """Return the section name for a root-level command, or '' if unlisted."""
    from . import COMMAND_SECTIONS

    for section, names in COMMAND_SECTIONS:
        if name in names:
            return section.lower()
    return ""


def _short_help(cmd: click.Command) -> str:
    try:
        return cmd.get_short_help_str(limit=80) or ""
    except Exception:
        return ""


def _subcommand_candidates(
    cmd: click.Command, partial: str, root: click.Command
) -> list[tuple[str, str, str]]:
    if not isinstance(cmd, click.Group):
        return []
    is_root = cmd is root
    out: list[tuple[str, str, str]] = []
    for name, sub in cmd.commands.items():
        if getattr(sub, "hidden", False):
            continue
        if not name.startswith(partial):
            continue
        section = _section_of(name) if is_root else ""
        out.append((section, name, _short_help(sub)))
    return out


def _flag_candidates(
    cmd: click.Command, partial: str
) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for param in cmd.params:
        if not isinstance(param, click.Option):
            continue
        desc = (param.help or "").strip()
        for opt in param.opts + param.secondary_opts:
            if opt.startswith(partial):
                out.append(("flag", opt, desc))
    # Click adds ``--help`` lazily via Context, not via ``cmd.params``, so it
    # never shows up in the loop above. Surface it explicitly unless the
    # command has disabled it.
    ctx_settings = getattr(cmd, "context_settings", {}) or {}
    help_names = ctx_settings.get("help_option_names", ["--help"])
    for hn in help_names:
        if hn.startswith(partial) and not any(n == hn for _, n, _ in out):
            out.append(("flag", hn, "Show this message and exit."))
    return out


def _fmt_age(secs: float) -> str:
    secs = max(0.0, secs)
    if secs < 60:
        return f"{int(secs)}s ago"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


# Statuses the user is most likely acting on at the prompt. We surface these
# first and drop "uploaded" / "rejected" unless the partial already narrows
# the list — a project with thousands of historical uploads would otherwise
# swamp the completion menu.
_ACTIONABLE_STATUSES = {
    "inbox", "discovered", "parsed", "staged", "reviewing",
    "committed", "approved", "uploading", "failed", "blocked",
}
_TRACE_ID_LIMIT = 30


def _strip_handle_prefix(s: str, k: str) -> tuple[str, str]:
    """Return (stripped, prefix) where prefix is "t:"/"c:" if present."""
    lead = f"{k}:"
    if s.startswith(lead):
        return s[len(lead):], lead
    return s, ""


def _trace_id_candidates(
    cmd: click.Command, partial: str
) -> list[tuple[str, str, str]]:
    """Emit trace IDs for commands taking a trace_id (arg or --trace opt).

    Accepts ``t:XX`` prefix on the partial and preserves it in the emitted
    completion so the user's typed form is round-tripped.
    """
    if partial.startswith("-"):
        return []
    wants_arg = any(
        isinstance(p, click.Argument) and p.name in (
            "trace_id", "trace_ids", "trace_handle",
        )
        for p in cmd.params
    )
    wants_opt = any(
        isinstance(p, click.Option) and p.name == "trace_id"
        for p in cmd.params
    )
    if not (wants_arg or wants_opt):
        return []
    try:
        from ..core.config import get_project_state_path, project_is_opted_in
        from ..core.state import StateManager
    except Exception:
        return []
    project_dir = Path.cwd()
    try:
        if not project_is_opted_in(project_dir):
            return []
        state = StateManager(get_project_state_path(project_dir))
    except Exception:
        return []
    probe, lead = _strip_handle_prefix(partial, "t")
    probe = probe.lower()
    now = time.time()
    # Two buckets so actionable traces sort above historical ones even when
    # recency would otherwise mix them.
    hot: list[tuple[float, str, str]] = []
    cold: list[tuple[float, str, str]] = []
    for entry in state._state.get("traces", {}).values():
        tid = entry.get("trace_id", "")
        if not tid:
            continue
        sid = entry.get("session_id", "") or ""
        tid_lc = tid.lower()
        sid_lc = sid.lower()
        if probe and not (tid_lc.startswith(probe) or sid_lc.startswith(probe)):
            continue
        status = entry.get("status", "?")
        created = float(entry.get("created_at") or 0.0)
        age = _fmt_age(now - created) if created else ""
        desc = " · ".join(x for x in (status, age) if x)
        bucket = hot if status in _ACTIONABLE_STATUSES else cold
        bucket.append((created, tid, desc))

    hot.sort(key=lambda t: t[0], reverse=True)
    cold.sort(key=lambda t: t[0], reverse=True)
    # If the partial is empty we show actionable only — avoids dumping
    # thousands of uploaded-long-ago traces into the menu. A non-empty
    # partial signals the user is searching, so we allow cold matches.
    pool = hot if not probe else hot + cold
    return [("value", f"{lead}{tid[:8]}", desc) for _, tid, desc in pool[:_TRACE_ID_LIMIT]]


def _commit_sha_candidates(
    cmd: click.Command, partial: str
) -> list[tuple[str, str, str]]:
    """Emit commit-SHA prefixes for commands taking a ``sha`` arg.

    Sourced from the local attribution cache — commits already correlated
    with traces. Accepts bare or ``c:``-prefixed partials.
    """
    if partial.startswith("-"):
        return []
    wants = any(
        isinstance(p, click.Argument) and p.name == "sha"
        for p in cmd.params
    )
    if not wants:
        return []
    try:
        from ..core.cache import AttributionCache
    except Exception:
        return []
    try:
        cache = AttributionCache(Path.cwd())
        shas = cache.list_attributed_shas()
    except Exception:
        return []
    probe, lead = _strip_handle_prefix(partial, "c")
    probe = probe.lower()
    out: list[tuple[str, str, str]] = []
    for sha in shas:
        if probe and not sha.lower().startswith(probe):
            continue
        display = f"{lead}{sha[:8]}"
        out.append(("value", display, "commit"))
        if len(out) >= 20:
            break
    return out


def _shell_choice_candidates(
    cmd: click.Command, partial: str, tokens: list[str]
) -> list[tuple[str, str, str]]:
    """Complete shell names after ``ot completions [install|uninstall] <TAB>``."""
    if partial.startswith("-"):
        return []
    # Only fire inside the completions tree.
    try:
        from .completions import SUPPORTED_SHELLS
    except Exception:
        return []
    name = getattr(cmd, "name", "")
    if name not in {"completions", "install", "uninstall"}:
        return []
    labels = {
        "bash": "Bash completions",
        "zsh": "Zsh completions (full menu with descriptions)",
        "fish": "Fish completions (full menu with descriptions)",
    }
    out = []
    for sh in SUPPORTED_SHELLS:
        if sh.startswith(partial):
            out.append(("value", sh, labels.get(sh, "")))
    return out


@click.command(
    "__complete",
    hidden=True,
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("tokens", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def complete_cmd(ctx: click.Context, tokens: tuple[str, ...]) -> None:
    """Resolve completions for the given partial command line."""
    # Lazy import to avoid circular imports at module load time.
    from . import main as root

    token_list = list(tokens)
    cmd, partial = _walk(root, token_list)

    candidates: list[tuple[str, str, str]] = []
    if partial.startswith("-"):
        # Flag-only mode when the user is clearly typing a flag.
        candidates.extend(_flag_candidates(cmd, partial))
    else:
        sub = _subcommand_candidates(cmd, partial, root)
        values = _trace_id_candidates(cmd, partial)
        values += _commit_sha_candidates(cmd, partial)
        values += _shell_choice_candidates(cmd, partial, token_list)
        candidates.extend(sub)
        candidates.extend(values)
        # For leaf commands with nothing else to offer, surface flags so
        # ``otd assess <TAB>`` shows its options instead of coming back empty.
        if not sub and not values and not isinstance(cmd, click.Group):
            candidates.extend(_flag_candidates(cmd, ""))

    # Dedupe by name, preserve first occurrence so desc stays stable. The
    # section column is preserved in the TSV for shells that want it; the
    # default zsh/bash/fish scripts just use name + desc.
    seen: set[str] = set()
    for section, name, desc in candidates:
        if name in seen:
            continue
        seen.add(name)
        safe_desc = desc.replace("\t", " ").replace("\n", " ").strip()
        click.echo(f"{name}\t{safe_desc}\t{section}")
