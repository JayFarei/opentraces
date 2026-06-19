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
from .journey import max_tier as _max_tier
from .journey import normalize_tier_label as _normalize_tier_label

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
    # Plan 069 R6: max tier across all owning journeys ("" when unowned
    # or no tier_label declared — render as the default tier).
    max_tier: str = ""

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
# Journey shell steps that pin an explicit opentraces binary (e.g. the
# runtime-selection journeys invoking `.testvenv/bin/opentraces`) still
# exercise the command for ownership purposes — recognise the binary by
# basename so a leading binary token is not mistaken for the command path.
_OT_BINARY_BASENAMES = frozenset({"opentraces", "ot", "otd"})


def _is_ot_binary_token(token: object) -> bool:
    return isinstance(token, str) and token.rsplit("/", 1)[-1] in _OT_BINARY_BASENAMES


def _extract_command_path(argv: list[str]) -> str | None:
    """Extract a 'command path' (e.g. 'dataset remote create') from a journey argv.

    A ``cli`` step argv is just the args passed to opentraces; a ``shell`` step
    that invokes opentraces directly leads with the binary token (a bare name or
    an explicit path like ``.testvenv/bin/opentraces``). A leading binary token
    is skipped so both forms yield the same command path.
    """
    parts: list[str] = []
    seen_arg = False
    for token in argv:
        if not isinstance(token, str):
            return None
        # Skip a leading opentraces-binary token (shell steps pin an explicit
        # binary path to exercise runtime selection); it is not the command.
        if not parts and not seen_arg and _is_ot_binary_token(token):
            continue
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


def map_journey_ownership(
    entries: Iterable[CommandEntry],
    *,
    journey_tiers_out: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Annotate ``entries`` in-place with the journeys that exercise them.

    Returns the ``(journey_name, trajectory_slug)`` pairs declared by
    each journey TOML's ``trajectories = [...]`` field, so the drift
    check can verify each name resolves in 063.

    Plan 069 R6: when ``journey_tiers_out`` is provided, the resolved
    ``tier_label`` for each journey is recorded there (default
    ``bronze`` when absent), and each entry's ``max_tier`` is computed
    from the highest tier of any owning journey.
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
        tier_label = _normalize_tier_label(doc.get("tier_label"))
        if journey_tiers_out is not None:
            journey_tiers_out[journey_name] = tier_label
        for traj in doc.get("trajectories", []) or []:
            journey_trajectories.append((journey_name, str(traj)))
        for step in doc.get("steps", []):
            stype = step.get("type")
            argv = step.get("argv", [])
            # Credit `cli` steps (argv = bare opentraces args) and `shell`
            # steps that invoke an opentraces binary directly (argv[0] is the
            # binary — e.g. runtime-selection journeys). Other shell steps
            # (`sh -c`, `cat`, ...) are not opentraces invocations.
            if stype == "cli":
                cmd = _extract_command_path(argv)
            elif stype == "shell" and argv and _is_ot_binary_token(argv[0]):
                cmd = _extract_command_path(argv)
            else:
                continue
            if cmd is None:
                continue
            # Walk back through partial matches: "dataset remote create"
            # also marks "dataset remote" and "dataset" as exercised.
            parts = cmd.split()
            for i in range(len(parts), 0, -1):
                candidate = " ".join(parts[:i])
                if candidate in by_path:
                    entry = by_path[candidate]
                    if journey_name not in entry.owning_journeys:
                        entry.owning_journeys.append(journey_name)
                    # Plan 069 R6: roll up max tier across owners.
                    entry.max_tier = (
                        tier_label if not entry.max_tier
                        else _max_tier(entry.max_tier, tier_label)
                    )
                    break
    return journey_trajectories


# --------------------------------------------------------------------------
# Markdown emitter
# --------------------------------------------------------------------------
def _tier_display(entry: CommandEntry) -> str:
    """Render the tier cell for ``entry``: owned -> max tier label;
    unowned/group -> em-dash."""
    if not entry.owning_journeys:
        return "—"
    return entry.max_tier or "bronze"


def _per_trajectory_tier_rollup(
    entries: list[CommandEntry],
    journey_trajectories: list[tuple[str, str]],
    journey_tiers: dict[str, str],
) -> list[tuple[str, str, int]]:
    """Compute ``(trajectory_slug, max_tier_label, journey_count)`` for
    every trajectory declared by any journey, sorted by slug.

    Plan 069 R6: this is the data behind the per-trajectory tier
    coverage section in the markdown output.
    """
    from .journey import _TIER_RANK
    by_traj_journeys: dict[str, set[str]] = {}
    for journey_name, traj in journey_trajectories:
        if not traj:
            continue
        by_traj_journeys.setdefault(traj, set()).add(journey_name)
    rollup: list[tuple[str, str, int]] = []
    for traj in sorted(by_traj_journeys):
        max_rank = -1
        for jn in by_traj_journeys[traj]:
            rank = _TIER_RANK.get(journey_tiers.get(jn, "bronze"), 0)
            if rank > max_rank:
                max_rank = rank
        label = next(k for k, v in _TIER_RANK.items() if v == max_rank)
        rollup.append((traj, label, len(by_traj_journeys[traj])))
    return rollup


def render_markdown(
    entries: list[CommandEntry],
    *,
    journey_trajectories: list[tuple[str, str]] | None = None,
    journey_tiers: dict[str, str] | None = None,
) -> str:
    visible = [e for e in entries if not e.hidden]
    hidden = [e for e in entries if e.hidden]
    owned = sum(1 for e in visible if e.owned)
    unowned = sum(1 for e in visible if not e.owned)

    lines = [
        "# opentraces Click command inventory",
        "",
        "Generated by `otbox matrix --inventory` (plan 062 M62-1 + plan 063 SSoT + plan 069 tiered coverage).",
        "Each row's trajectory comes from `kb/plans/063-jtbd-command-map.md` —",
        "the single source of truth. Drift fails CI under `--strict`.",
        "",
        f"- Public commands: **{len(visible)}** ({owned} owned, {unowned} unowned)",
        f"- Hidden commands: **{len(hidden)}**",
        "",
        "## Public commands",
        "",
        "| Command | Trajectory (063) | Tier | Owned by | Description |",
        "|---|---|---|---|---|",
    ]
    for e in visible:
        owners = ", ".join(e.owning_journeys) if e.owning_journeys else "**unowned**"
        traj = e.trajectory or ("_group_" if e.is_group else "**missing**")
        lines.append(
            f"| `{e.path}` | {traj} | {_tier_display(e)} | {owners} | {e.description} |"
        )
    lines += ["", "## Hidden commands", "",
              "| Command | Trajectory (063) | Tier | Owned by | Description |",
              "|---|---|---|---|---|"]
    for e in hidden:
        owners = ", ".join(e.owning_journeys) if e.owning_journeys else "—"
        traj = e.trajectory or ("_group_" if e.is_group else "—")
        lines.append(
            f"| `_{e.path}` | {traj} | {_tier_display(e)} | {owners} | {e.description} |"
        )

    # Plan 069 R6: per-trajectory tier coverage rollup section.
    if journey_trajectories is not None and journey_tiers is not None:
        rollup = _per_trajectory_tier_rollup(
            entries, journey_trajectories, journey_tiers
        )
        bronze = sum(1 for _, t, _ in rollup if t == "bronze")
        silver = sum(1 for _, t, _ in rollup if t == "silver")
        gold = sum(1 for _, t, _ in rollup if t == "gold")
        lines += [
            "",
            "## Per-trajectory tier coverage",
            "",
            (
                f"- Trajectories covered: **{len(rollup)}** "
                f"({gold} gold, {silver} silver, {bronze} bronze)"
            ),
            "",
            "| Trajectory | Max tier | Owning journeys |",
            "|---|---|---|",
        ]
        for traj, tier, count in rollup:
            lines.append(f"| {traj} | {tier} | {count} |")

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
    journey_tiers: dict[str, str] = {}
    journey_trajectories = map_journey_ownership(
        entries, journey_tiers_out=journey_tiers
    )

    # Layer the 063 SSoT onto the live registry: every parsed command
    # contributes its trajectory.
    jtbd = load_jtbd_map()
    for e in entries:
        cmd = jtbd.commands.get(e.path)
        if cmd:
            e.trajectory = cmd.trajectory

    md = render_markdown(
        entries,
        journey_trajectories=journey_trajectories,
        journey_tiers=journey_tiers,
    )
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
        journey_tiers=journey_tiers,
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
