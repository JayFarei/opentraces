"""Parse plan 063 as the single source of truth for the CLI command map.

Plan ``kb/plans/063-jtbd-command-map.md`` enumerates every Click command,
its JTBD one-liner, the action trajectory it belongs to, and the
persona it serves. This module reads 063 and exposes that map to the
otbox matrix gate so:

1. A Click command that is *not* in 063 fails the inventory check.
2. A journey TOML that names an *unknown* trajectory fails the matrix.
3. A core-lane Click command with *no owning journey* fails the
   coverage gate (when ``strict=True``).

Drift between 063 and the live Click registry fails CI loudly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
JTBD_PATH = REPO_ROOT / "kb" / "plans" / "063-jtbd-command-map.md"

# Plan 069 R5: trajectory slugs that MUST be covered by at least one
# ``tier_label = "gold"`` journey. Starts empty so the slice ships
# without forcing any existing trajectory to upgrade — plan 070
# populates this set incrementally as agent-facing journeys backfill
# their credible-state coverage.
#
# Plan 070 M70-1 / R3: ``survival-walk`` now has a credible-state
# journey (``survival-walk-reverted.toml``) that asserts
# ``trail track`` / ``trail search --survival reverted`` / ``trail
# explain`` against the ``c-captured-with-revert`` checkpoint.
# Plan 070 M70-4 / R2: ``automate-dataset-runs`` now has a credible-
# state journey (``dataset-sync-skill-history.toml``) that asserts
# ``workflow create`` / ``dataset new --workflow`` /
# ``dataset schedule add | list | show | pause | resume | remove``
# against the ``c-captured-multi-skill`` checkpoint.
# Plan 070 M70-2 / R1: ``pr-lineage-publish`` now has a credible-state
# journey (``pr-blame-on-captured-branch.toml``) that asserts
# ``trail blame pr render`` produces a real PR body for a real 2-commit
# captured branch against the ``c-captured-with-pr-branch`` checkpoint;
# the existing ``pr-lineage-publish.toml`` stays as the bronze
# ``--help`` sibling.
# Plan 070 M70-3 / R4: ``inspect-security-pipeline`` now has a credible-
# state journey (``security-sanitize-captured-content.toml``) that
# asserts the security pipeline ran on a real captured Claude session
# (the checkpoint explicitly enables regex + entropy to fingerprint the trace)
# AND that the user-facing ``trace query`` projection does not leak
# the secret literal — against the ``c-captured-with-secrets``
# checkpoint. The existing ``inspect-security-pipeline.toml`` stays
# as the bronze ``--help`` sibling on the smoke seed.
AGENT_FACING_TRAJECTORIES_MIN_GOLD: frozenset[str] = frozenset({
    "survival-walk",
    "automate-dataset-runs",
    "pr-lineage-publish",
    "inspect-security-pipeline",
})

# Plan 069 R4: known tier labels, lowest -> highest. The canonical
# table lives in journey.py — import it from there so a future tier
# vocabulary extension stays single-source.
from .journey import _TIER_RANK, normalize_tier_label as _normalize_tier  # noqa: E402


# Trajectory cell forms we accept from §2..§7:
#   onboard-repo (1/5)
#   retrieve-relevant-traces (1/3) ★
#   connect-hf-identity (1/3) — alias
#   build-publishable-dataset (3/6) ★
#   session-ingest (auto-invoked)
#   enable-live-attribution (lifecycle: start)
#   enable-live-attribution (diagnostic)
#   enable-live-attribution (check)
#   build-dataset-from-lineage
#   schema-migration (1/1)
#   deprecated                       <- excluded (no trajectory required)
# The leading kebab-case slug is the trajectory key.
_TRAJ_SLUG_RE = re.compile(r"^([a-z][a-z0-9-]*)\b")

# §1 trajectory inventory rows use **bold-wrapped** slugs in the first cell.
# Cross-bucket rows append `★` after the bold close:
#   | **onboard-repo** | 1 → 5 | 1 (Setup) | A developer ... |
#   | **recreate-trace-environment** ★ | 1 → 2 | 3 + 6 | … |
_TRAJ_INVENTORY_RE = re.compile(r"^\|\s*\*\*([a-z][a-z0-9-]*)\*\*[^|]*\|")

# Per-bucket table rows: `| `command` | jtbd | trajectory cell | journey | persona |`
# The command cell wraps in backticks and may carry parenthetical marks like
# `(hidden)` / `(hidden, deprecated)` outside the backticks.
_ROW_RE = re.compile(
    r"^\|\s*`([^`]+)`(?:\s*\(([^)]+)\))?\s*\|"  # command (and optional outer marker)
    r"\s*([^|]+?)\s*\|"                          # JTBD one-liner
    r"\s*([^|]+?)\s*\|"                          # trajectory cell
    r"\s*([^|]+?)\s*\|"                          # owning journey cell (from doc; we re-derive)
    r"\s*([^|]+?)\s*\|"                          # persona cell
    r"\s*$"
)


@dataclass(frozen=True)
class TrajectoryEntry:
    """Per-trajectory metadata pulled from 063 §1."""

    slug: str
    span: str = ""
    buckets: str = ""
    description: str = ""


@dataclass
class CommandEntry:
    """Per-command metadata pulled from 063 §2..§7."""

    path: str
    hidden: bool
    deprecated: bool
    trajectory: str
    jtbd: str
    persona: str
    section: str = ""


@dataclass
class JtbdMap:
    trajectories: dict[str, TrajectoryEntry] = field(default_factory=dict)
    commands: dict[str, CommandEntry] = field(default_factory=dict)
    source: Path = JTBD_PATH

    def trajectory_slugs(self) -> set[str]:
        return set(self.trajectories)

    def known_command_paths(self) -> set[str]:
        return set(self.commands)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _strip_md(text: str) -> str:
    """Light markdown strip: drop **bold**, `code`, ★, and em-dashes."""
    text = text.replace("**", "")
    text = text.replace("`", "")
    text = text.replace("★", "").strip(" —–-")
    return text.strip()


def _extract_command_path(raw_cell: str, outer_marker: str | None) -> tuple[str, bool, bool]:
    """Return (path, hidden, deprecated) from the command cell.

    The command cell wraps in backticks. Some rows append an outer
    marker like ``(hidden)`` or ``(hidden, deprecated)`` after the
    backticks; ``outer_marker`` captures that content (without parens).
    """
    path = raw_cell.strip()
    hidden = False
    deprecated = False
    if outer_marker:
        marker = outer_marker.lower()
        hidden = "hidden" in marker
        deprecated = "deprecated" in marker
    return path, hidden, deprecated


def _parse_trajectory_cell(cell: str) -> tuple[str, bool]:
    """Return (trajectory_slug, is_deprecated).

    Cells like ``deprecated`` map to ``("", True)`` — the command does
    not belong to any owned trajectory and is excluded from coverage.
    """
    cell = _strip_md(cell).lower()
    if not cell or cell.startswith("deprecated"):
        return "", True
    match = _TRAJ_SLUG_RE.match(cell)
    if not match:
        return "", False
    return match.group(1), False


def _parse_jtbd_doc(text: str) -> JtbdMap:
    """Parse 063 into a JtbdMap (trajectories + commands)."""
    out = JtbdMap()
    current_section = ""

    # First pass: trajectories from §1.
    inside_traj_table = False
    for line in text.splitlines():
        if line.startswith("## §1 "):
            inside_traj_table = True
            continue
        if inside_traj_table and line.startswith("## "):
            inside_traj_table = False
        if not inside_traj_table:
            continue
        m = _TRAJ_INVENTORY_RE.match(line)
        if not m:
            continue
        slug = m.group(1)
        # Pull the remaining cells for description; tolerate missing ones.
        cells = [c.strip() for c in line.strip("|").split("|")]
        out.trajectories[slug] = TrajectoryEntry(
            slug=slug,
            span=cells[1] if len(cells) > 1 else "",
            buckets=cells[2] if len(cells) > 2 else "",
            description=cells[3] if len(cells) > 3 else "",
        )

    # Second pass: per-command rows from §2..§7.
    for line in text.splitlines():
        if line.startswith("## §"):
            current_section = line.strip("# ").strip()
            continue
        if not line.startswith("| `"):
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        cmd_raw, outer, jtbd, traj_cell, _journey_cell, persona_cell = m.groups()
        path, hidden, deprecated = _extract_command_path(cmd_raw, outer)
        traj_slug, deprecated_traj = _parse_trajectory_cell(traj_cell)
        deprecated = deprecated or deprecated_traj
        # Skip section-header lookalike rows (no real trajectory + no
        # deprecation marker = malformed; tolerate by skipping).
        if not traj_slug and not deprecated:
            continue
        out.commands[path] = CommandEntry(
            path=path,
            hidden=hidden,
            deprecated=deprecated,
            trajectory=traj_slug,
            jtbd=_strip_md(jtbd),
            persona=_strip_md(persona_cell),
            section=current_section,
        )

    return out


@lru_cache(maxsize=1)
def load_jtbd_map() -> JtbdMap:
    """Parse 063 once per process; results are cached."""
    if not JTBD_PATH.exists():
        raise FileNotFoundError(f"plan 063 not found at {JTBD_PATH}")
    return _parse_jtbd_doc(JTBD_PATH.read_text())


# ---------------------------------------------------------------------------
# Drift checks (the gate)
# ---------------------------------------------------------------------------
@dataclass
class DriftReport:
    missing_from_063: list[str] = field(default_factory=list)        # Click cmd ∉ 063
    phantom_in_063: list[str] = field(default_factory=list)          # 063 cmd ∉ Click
    unknown_trajectories: list[tuple[str, str]] = field(default_factory=list)  # (journey, trajectory)
    unowned_commands: list[str] = field(default_factory=list)        # visible+non-deprecated+no journey
    # Plan 069 R5: (trajectory_slug, current_max_tier, required_tier)
    # for each agent-facing trajectory whose top-tier owning journey
    # is below the per-trajectory required minimum.
    coverage_below_required_tier: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.missing_from_063
            or self.phantom_in_063
            or self.unknown_trajectories
            or self.unowned_commands
            or self.coverage_below_required_tier
        )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "missing_from_063": list(self.missing_from_063),
            "phantom_in_063": list(self.phantom_in_063),
            "unknown_trajectories": [
                {"journey": j, "trajectory": t} for j, t in self.unknown_trajectories
            ],
            "unowned_commands": list(self.unowned_commands),
            "coverage_below_required_tier": [
                {
                    "trajectory": traj,
                    "current_max_tier": cur,
                    "required_tier": req,
                }
                for traj, cur, req in self.coverage_below_required_tier
            ],
        }

    def human_summary(self) -> str:
        if self.ok:
            return "jtbd: drift check OK"
        bits = []
        if self.missing_from_063:
            bits.append(
                f"{len(self.missing_from_063)} Click command(s) missing from 063: "
                + ", ".join(self.missing_from_063[:8])
                + ("…" if len(self.missing_from_063) > 8 else "")
            )
        if self.phantom_in_063:
            bits.append(
                f"{len(self.phantom_in_063)} 063 row(s) not in the live Click registry: "
                + ", ".join(self.phantom_in_063[:8])
                + ("…" if len(self.phantom_in_063) > 8 else "")
            )
        if self.unknown_trajectories:
            bits.append(
                f"{len(self.unknown_trajectories)} journey(s) name unknown trajectories: "
                + ", ".join(f"{j} → {t}" for j, t in self.unknown_trajectories[:5])
                + ("…" if len(self.unknown_trajectories) > 5 else "")
            )
        if self.unowned_commands:
            bits.append(
                f"{len(self.unowned_commands)} core-lane command(s) unowned: "
                + ", ".join(self.unowned_commands[:8])
                + ("…" if len(self.unowned_commands) > 8 else "")
            )
        if self.coverage_below_required_tier:
            bits.append(
                f"{len(self.coverage_below_required_tier)} trajectory/-ies below "
                "required tier: "
                + ", ".join(
                    f"{t} ({cur} < {req})"
                    for t, cur, req in self.coverage_below_required_tier[:5]
                )
                + ("…" if len(self.coverage_below_required_tier) > 5 else "")
            )
        return "jtbd: drift detected — " + "; ".join(bits)


def check_drift(
    click_paths: Iterable[str],
    journey_trajectories: Iterable[tuple[str, str]],
    ownership: dict[str, list[str]],
    *,
    all_click_paths: Iterable[str] | None = None,
    jtbd: JtbdMap | None = None,
    journey_tiers: dict[str, str] | None = None,
) -> DriftReport:
    """Run all SSoT gates and return a structured drift report.

    Arguments
    ---------
    click_paths
        Visible non-group Click command paths — these MUST be in 063.
    journey_trajectories
        Iterable of ``(journey_name, trajectory_slug)`` pairs from each
        journey TOML's ``trajectories`` field. Unknown trajectories
        surface here.
    ownership
        Map of ``{command_path: [owning_journey, …]}``.
    all_click_paths
        Every Click path (visible + hidden + groups). When provided,
        the bidirectional check flags 063 rows that don't exist in
        Click. Default ``None`` skips that direction (legacy callers).
    journey_tiers
        Optional ``{journey_name: tier_label}`` mapping. Plan 069 R5:
        when provided, the gate computes each trajectory's current
        max tier (across all journeys that name it) and flags any
        trajectory in ``AGENT_FACING_TRAJECTORIES_MIN_GOLD`` that is
        below the required tier. With the default (empty) vocabulary
        no trajectories are checked, so existing callers stay green.
    """
    report = DriftReport()
    jtbd = jtbd or load_jtbd_map()

    known_paths = jtbd.known_command_paths()
    known_trajs = jtbd.trajectory_slugs()

    # Gate 1 — Click command missing from 063.
    for path in sorted(set(click_paths)):
        if path not in known_paths:
            report.missing_from_063.append(path)

    # Gate 1b — 063 row not in Click (phantom command).
    if all_click_paths is not None:
        click_set = set(all_click_paths)
        for path in sorted(known_paths - click_set):
            report.phantom_in_063.append(path)

    # Gate 2 — journey naming unknown trajectory.
    journey_traj_pairs = list(journey_trajectories)
    for journey_name, traj in journey_traj_pairs:
        if traj and traj not in known_trajs:
            report.unknown_trajectories.append((journey_name, traj))

    # Gate 3 — unowned core-lane command (visible + non-deprecated +
    # trajectory not in the implicit auto-invoked set).
    auto_buckets = {"session-ingest", "commit-correlation"}
    for path, entry in jtbd.commands.items():
        if entry.hidden or entry.deprecated:
            continue
        if entry.trajectory in auto_buckets:
            # auto-invoked trajectories are proven indirectly by other
            # journeys — covered transitively, no direct journey needed.
            continue
        if not ownership.get(path):
            report.unowned_commands.append(path)
    report.unowned_commands.sort()

    # Gate 4 (plan 069 R5) — tiered trajectory coverage. For each
    # trajectory in AGENT_FACING_TRAJECTORIES_MIN_GOLD, compute the
    # max tier across all journeys that name it and flag if below
    # the required minimum. Empty vocabulary => no-op.
    if AGENT_FACING_TRAJECTORIES_MIN_GOLD and journey_tiers is not None:
        # Per-trajectory list of tier ranks for journeys owning it.
        traj_max_tier: dict[str, int] = {}
        for journey_name, traj in journey_traj_pairs:
            if not traj:
                continue
            rank = _TIER_RANK[_normalize_tier(journey_tiers.get(journey_name))]
            cur = traj_max_tier.get(traj, -1)
            if rank > cur:
                traj_max_tier[traj] = rank
        required_rank = _TIER_RANK["gold"]
        for traj in sorted(AGENT_FACING_TRAJECTORIES_MIN_GOLD):
            cur_rank = traj_max_tier.get(traj, -1)
            if cur_rank < required_rank:
                cur_label = (
                    "none" if cur_rank < 0
                    else next(k for k, v in _TIER_RANK.items() if v == cur_rank)
                )
                report.coverage_below_required_tier.append(
                    (traj, cur_label, "gold")
                )

    return report
