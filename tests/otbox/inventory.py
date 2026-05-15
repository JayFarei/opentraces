"""Click command inventory + journey ownership map (plan 062 M62-1).

Walks the opentraces Click registry, lists every public + hidden
command, and cross-references against the journey TOMLs in
``tests/otbox/catalogue/journeys/`` to flag which commands are
*owned* by at least one journey and which are *unowned*.

Plan 063 promoted ``kb/plans/063-jtbd-command-map.md`` to the single
source of truth for the JTBD × trajectory map. The inventory builder
now layers the 063 SSoT check on top of the Click registry: a drift
report flags Click commands missing from 063, journey TOMLs naming
unknown trajectories, and unowned core-lane commands. ``strict=True``
turns those into a non-zero exit so CI fails loudly on drift.

Output: ``tests/otbox/catalogue/journey-inventory.md`` (markdown table).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .jtbd import DriftReport, check_drift, load_jtbd_map

CATALOGUE_DIR = Path(__file__).resolve().parent / "catalogue" / "journeys"
INVENTORY_PATH = Path(__file__).resolve().parent / "catalogue" / "journey-inventory.md"


@dataclass
class CommandEntry:
    path: str            # e.g. "dataset remote create"
    hidden: bool
    description: str
    is_group: bool = False  # pure-help group entry (063 §8.8 exempts these)
    trajectory: str = ""    # from 063, "" when missing
    owning_journeys: list[str] = field(default_factory=list)

    @property
    def owned(self) -> bool:
        return bool(self.owning_journeys)


# --------------------------------------------------------------------------
# Click registry walk
# --------------------------------------------------------------------------
def walk_click_registry() -> list[CommandEntry]:
    """Recursively walk the opentraces Click root group."""
    from opentraces.cli import main as click_root

    entries: list[CommandEntry] = []

    def _walk(group, prefix: list[str]) -> None:
        from click import Group as _ClickGroup

        for sub_name in sorted(group.commands):
            sub = group.commands[sub_name]
            path = " ".join(prefix + [sub_name])
            desc = (sub.short_help or "").strip()
            if not desc and sub.help:
                desc = sub.help.strip().splitlines()[0]
            entries.append(
                CommandEntry(
                    path=path,
                    hidden=bool(getattr(sub, "hidden", False)),
                    description=desc[:100],
                    is_group=isinstance(sub, _ClickGroup),
                )
            )
            if isinstance(sub, _ClickGroup):
                _walk(sub, prefix + [sub_name])

    _walk(click_root, [])
    return entries


# --------------------------------------------------------------------------
# Journey ownership cross-reference
# --------------------------------------------------------------------------
def _extract_command_path(argv: list[str]) -> str | None:
    """Extract a 'command path' (e.g. 'dataset remote create') from a journey argv.

    A journey's ``cli`` step argv is just the args passed to opentraces — the
    first non-flag positional words form the command path.
    """
    parts: list[str] = []
    seen_arg = False
    for token in argv:
        if not isinstance(token, str):
            return None
        if token.startswith("-"):
            if seen_arg:
                break
            continue
        # Heuristic: stop at the first token that's clearly an argument
        # (looks like a value, not a subcommand): contains "/", ".",
        # an "=", quotes, or starts with a digit. Subcommands are short
        # lowercase identifiers.
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_-]*$", token):
            break
        parts.append(token)
        seen_arg = True
    return " ".join(parts) if parts else None


def map_journey_ownership(entries: Iterable[CommandEntry]) -> list[tuple[str, str]]:
    """Annotate ``entries`` in-place with the journeys that exercise them.

    Returns the ``(journey_name, trajectory_slug)`` pairs declared by
    each journey TOML's ``trajectories = [...]`` field, so the drift
    check can verify each name resolves in 063.
    """
    by_path = {e.path: e for e in entries}
    journey_trajectories: list[tuple[str, str]] = []
    if not CATALOGUE_DIR.exists():
        return journey_trajectories
    for toml_path in sorted(CATALOGUE_DIR.glob("*.toml")):
        try:
            doc = tomllib.loads(toml_path.read_text())
        except Exception:  # noqa: BLE001 - skip malformed scenarios
            continue
        journey_name = doc.get("name", toml_path.stem)
        for traj in doc.get("trajectories", []) or []:
            journey_trajectories.append((journey_name, str(traj)))
        for step in doc.get("steps", []):
            if step.get("type") != "cli":
                continue
            argv = step.get("argv", [])
            cmd = _extract_command_path(argv)
            if cmd is None:
                continue
            # Walk back through partial matches: "dataset remote create"
            # also marks "dataset remote" and "dataset" as exercised.
            parts = cmd.split()
            for i in range(len(parts), 0, -1):
                candidate = " ".join(parts[:i])
                if candidate in by_path:
                    if journey_name not in by_path[candidate].owning_journeys:
                        by_path[candidate].owning_journeys.append(journey_name)
                    break
    return journey_trajectories


# --------------------------------------------------------------------------
# Markdown emitter
# --------------------------------------------------------------------------
def render_markdown(entries: list[CommandEntry]) -> str:
    visible = [e for e in entries if not e.hidden]
    hidden = [e for e in entries if e.hidden]
    owned = sum(1 for e in visible if e.owned)
    unowned = sum(1 for e in visible if not e.owned)

    lines = [
        "# opentraces Click command inventory",
        "",
        "Generated by `otbox matrix --inventory` (plan 062 M62-1 + plan 063 SSoT).",
        "Each row's trajectory comes from `kb/plans/063-jtbd-command-map.md` —",
        "the single source of truth. Drift fails CI under `--strict`.",
        "",
        f"- Public commands: **{len(visible)}** ({owned} owned, {unowned} unowned)",
        f"- Hidden commands: **{len(hidden)}**",
        "",
        "## Public commands",
        "",
        "| Command | Trajectory (063) | Owned by | Description |",
        "|---|---|---|---|",
    ]
    for e in visible:
        owners = ", ".join(e.owning_journeys) if e.owning_journeys else "**unowned**"
        traj = e.trajectory or ("_group_" if e.is_group else "**missing**")
        lines.append(f"| `{e.path}` | {traj} | {owners} | {e.description} |")
    lines += ["", "## Hidden commands", "",
              "| Command | Trajectory (063) | Owned by | Description |",
              "|---|---|---|---|"]
    for e in hidden:
        owners = ", ".join(e.owning_journeys) if e.owning_journeys else "—"
        traj = e.trajectory or ("_group_" if e.is_group else "—")
        lines.append(f"| `_{e.path}` | {traj} | {owners} | {e.description} |")
    return "\n".join(lines) + "\n"


def build_inventory(
    out_path: Path | None = None,
    *,
    strict: bool = False,
) -> tuple[Path, dict, DriftReport]:
    """Walk the registry, cross-reference journeys, write the markdown.

    Plan 063 SSoT gate: every visible Click command must be in 063,
    every journey-declared trajectory must resolve, every core-lane
    command (non-group, non-deprecated) must be owned by ≥1 journey.

    Returns ``(out_path, summary, drift_report)``. With ``strict=True``,
    callers are expected to fail the run when the drift report is not
    OK; this function never raises.
    """
    entries = walk_click_registry()
    journey_trajectories = map_journey_ownership(entries)

    # Layer the 063 SSoT onto the live registry: every parsed command
    # contributes its trajectory.
    jtbd = load_jtbd_map()
    for e in entries:
        cmd = jtbd.commands.get(e.path)
        if cmd:
            e.trajectory = cmd.trajectory

    md = render_markdown(entries)
    target = out_path or INVENTORY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md)
    visible = [e for e in entries if not e.hidden]

    # Drift check: feed Click paths minus *group* entry-points (063 §8.8
    # exempts pure-help groups). Groups remain in the inventory output
    # for completeness but never count against the gate.
    click_visible_nongroup = [e.path for e in visible if not e.is_group]
    all_click_paths = [e.path for e in entries]
    ownership = {e.path: list(e.owning_journeys) for e in entries}
    drift = check_drift(
        click_visible_nongroup,
        journey_trajectories,
        ownership,
        all_click_paths=all_click_paths,
        jtbd=jtbd,
    )

    summary = {
        "public_total": len(visible),
        "public_owned": sum(1 for e in visible if e.owned),
        "public_unowned": sum(1 for e in visible if not e.owned),
        "hidden_total": sum(1 for e in entries if e.hidden),
        "unowned_commands": [e.path for e in visible if not e.owned],
        "drift": drift.to_dict(),
        "strict": strict,
    }
    return target, summary, drift
