"""Rule-based natural-language summarisation of a TraceContribution.

``summarize_contribution`` returns a list of body bullet strings (no glyph
prefix, no colour) that the blame renderer paints. The bullet rules:

- ``+ Added E1, E2, E3 in <dir>``        when >=3 additions share a common
  parent directory.
- ``+ Added E1, E2``                     when 1-2 additions (no dir suffix).
- Multiple bullets when additions span dirs; scattered remainder becomes
  ``+ Added N more in other files``.
- Same pattern for Modified (~), Removed (-), and Renamed (rotate-glyph).
- Chunk-only: ``~ Modified chunks lines 261-280, 321-340 in schema.prisma``.
- Mix: group by change_type in the order add, modify, rename, delete,
  cap at 3 bullets, append ``(+ N more entities)`` for any remainder.
- Empty (non-zero line_count but no entity overlap): returns a single
  ``(no entity overlap on this commit)`` string.

``summarize_contribution_verbose`` returns one line per entity for
``--entities`` mode.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from os.path import dirname
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .entity_join import EntityChange, TraceContribution


# Display words per change_type. "renamed" isn't a past-participle verb in
# the same slot — the bullet for it uses ``E1 -> E2`` form, not a list.
_WORD = {
    "added": "Added",
    "modified": "Modified",
    "deleted": "Removed",
    "renamed": "Renamed",
}


def _common_dir(paths: list[str]) -> str | None:
    """Return the shared parent dir for all paths, or None."""
    if not paths:
        return None
    dirs = [dirname(p) for p in paths]
    if all(d == dirs[0] for d in dirs):
        return dirs[0] or None
    return None


def _group_bullets(change_type: str, entities: list["EntityChange"]) -> list[str]:
    """Build bullets for a single change_type group (non-rename)."""
    word = _WORD.get(change_type, change_type.title())
    # Partition by parent dir.
    by_dir: dict[str, list["EntityChange"]] = defaultdict(list)
    for e in entities:
        by_dir[dirname(e.file_path) or ""].append(e)

    # Bucket: dirs with >= 2 entities get their own bullet, singletons roll
    # up into a "scatter" list.
    bullets: list[str] = []
    scatter: list["EntityChange"] = []
    for d, ents in by_dir.items():
        if len(ents) >= 2:
            names = ", ".join(e.entity_name for e in ents)
            if d:
                bullets.append(f"{word} {names} in {d}")
            else:
                bullets.append(f"{word} {names}")
        else:
            scatter.extend(ents)

    if scatter:
        if len(scatter) <= 2 and not bullets:
            # 1-2 entities total — single bullet, no dir suffix.
            names = ", ".join(e.entity_name for e in scatter)
            bullets.append(f"{word} {names}")
        elif bullets:
            n = len(scatter)
            bullets.append(f"{word} {n} more in other files")
        else:
            # >= 3 scattered entities, no shared dir — still collapse.
            names = ", ".join(e.entity_name for e in scatter[:3])
            extra = len(scatter) - 3
            if extra > 0:
                bullets.append(f"{word} {names} (+{extra} more)")
            else:
                bullets.append(f"{word} {names}")
    return bullets


def _rename_bullets(entities: list["EntityChange"]) -> list[str]:
    out: list[str] = []
    for e in entities:
        old = e.old_entity_name or "?"
        out.append(f"Renamed {old} \u2192 {e.entity_name}")
    return out


def _chunk_only_bullet(contrib: "TraceContribution") -> str | None:
    if not contrib.chunks:
        return None
    # Group by file.
    by_file: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    # chunks don't carry file_path — they come from the entities list which
    # we lose in the join. For the chunk summary we group by dominant change.
    # Caller has already filtered to trace-level; so render flat.
    for start, end, ct in contrib.chunks:
        by_file["chunks"].append((start, end, ct))
    ranges = [f"{s}-{e}" for s, e, _ in contrib.chunks]
    word = _WORD.get(contrib.chunks[0][2], "Modified")
    return f"{word} chunks lines {', '.join(ranges)}"


def summarize_contribution(
    contrib: "TraceContribution", *, ascii_only: bool = False,
    max_bullets: int = 3,
) -> list[str]:
    """Return up to ``max_bullets`` bullet strings for ``contrib``."""
    if not contrib.entities and not contrib.chunks:
        if contrib.line_count > 0:
            return ["(no entity overlap on this commit)"]
        return []

    buckets: dict[str, list["EntityChange"]] = defaultdict(list)
    for e in contrib.entities:
        buckets[e.change_type].append(e)

    order = ("added", "modified", "renamed", "deleted")
    all_bullets: list[str] = []
    for ct in order:
        ents = buckets.get(ct) or []
        if not ents:
            continue
        if ct == "renamed":
            all_bullets.extend(_rename_bullets(ents))
        else:
            all_bullets.extend(_group_bullets(ct, ents))

    if contrib.chunks and not all_bullets:
        c = _chunk_only_bullet(contrib)
        if c:
            all_bullets.append(c)

    # Prefix each bullet with the glyph for the change type.
    prefixed: list[str] = []
    for b in all_bullets:
        glyph = _bullet_glyph(b, ascii_only=ascii_only)
        prefixed.append(f"{glyph} {b}")

    if len(prefixed) > max_bullets:
        remainder = sum(len(v) for v in buckets.values()) - sum(
            _count_in_bullet(b) for b in all_bullets[:max_bullets]
        )
        visible = prefixed[:max_bullets]
        if remainder > 0:
            visible.append(f"  (+{remainder} more entities)")
        return visible
    return prefixed


def _bullet_glyph(bullet: str, *, ascii_only: bool) -> str:
    """Pick the leading glyph from the bullet's verb."""
    head = bullet.split(" ", 1)[0].lower()
    if head == "added":
        return "+"
    if head == "modified":
        return "~"
    if head == "removed":
        return "-"
    if head == "renamed":
        return "rn" if ascii_only else "\u21B7"  # rotate-glyph
    return "~"


def _count_in_bullet(bullet: str) -> int:
    """Approximate entity-count per bullet (for remainder maths)."""
    # Count commas + 1 as entity count; rename is 1 per bullet.
    head = bullet.split(" ", 1)[0].lower()
    if head == "renamed":
        return 1
    # "<word> A, B, C in <dir>" or "<word> N more in other files"
    if " more in other files" in bullet:
        # parse the number
        try:
            return int(bullet.split()[1])
        except (IndexError, ValueError):
            return 0
    body = bullet.split(" ", 1)[1] if " " in bullet else bullet
    body = body.split(" in ", 1)[0]
    return max(1, body.count(",") + 1)


def summarize_contribution_verbose(
    contrib: "TraceContribution",
) -> list[str]:
    """One bullet per entity — for ``--entities`` mode."""
    lines: list[str] = []
    for e in contrib.entities:
        glyph = {
            "added": "+", "modified": "~", "deleted": "-",
            "renamed": "\u21B7",
        }.get(e.change_type, "~")
        head = f"{glyph} {e.change_type} {e.entity_type} {e.entity_name}"
        if e.change_type == "renamed" and e.old_entity_name:
            head = f"{glyph} renamed {e.old_entity_name} \u2192 {e.entity_name}"
        lines.append(f"{head}  {e.file_path}")
    for start, end, ct in contrib.chunks:
        word = _WORD.get(ct, "Modified")
        lines.append(f"~ {word.lower()} chunk lines {start}-{end}")
    return lines
