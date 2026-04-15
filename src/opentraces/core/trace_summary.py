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


# ---------------------------------------------------------------------------
# Compact columnar summary (graph renderer post-audit).
# ---------------------------------------------------------------------------

# Entity-type label aliases used in the state column. A dominant
# ``>=60%`` bucket wins the label; mixed types get no label at all.
_TYPE_LABELS = {
    "function": "fns",
    "fn": "fns",
    "method": "fns",
    "struct": "structs",
    "class": "structs",
    "type": "types",
    "alias": "types",
    "chunk": "chunks",
    "heading": "headings",
    "section": "headings",
}


def _dedupe_adjacent(names: list[str]) -> list[str]:
    """Collapse runs of identical adjacent names -> ``<name> x N``."""
    out: list[str] = []
    i = 0
    while i < len(names):
        n = names[i]
        j = i
        while j < len(names) and names[j] == n:
            j += 1
        run = j - i
        if run > 1:
            out.append(f"{n} x{run}")
        else:
            out.append(n)
        i = j
    return out


def _truncate(s: str, max_len: int) -> str:
    if max_len <= 1 or len(s) <= max_len:
        return s
    return s[: max_len - 1] + "\u2026"


def _basename_no_ext(path: str) -> str:
    """Strip directory + single trailing extension. ``a/b/c.ts`` -> ``c``."""
    from os.path import basename, splitext
    b = basename(path) or path
    stem, _ = splitext(b)
    return stem or b


def _type_label(entities: list["EntityChange"]) -> str | None:
    """Return an aggregate type label if one type is dominant (>=60%)."""
    if not entities:
        return None
    counts: Counter[str] = Counter()
    for e in entities:
        label = _TYPE_LABELS.get((e.entity_type or "").lower())
        if label:
            counts[label] += 1
    if not counts:
        return None
    total = len(entities)
    top_label, top_count = counts.most_common(1)[0]
    if top_count / total >= 0.6:
        return top_label
    return None


def _fit_items(items: list[str], space: int) -> str:
    """Pack ``items`` as ``a, b, c +N more``, never splitting mid-item."""
    if not items or space <= 0:
        return ""
    sep = ", "
    out: list[str] = []
    used = 0
    for i, it in enumerate(items):
        # Reserve space for a potential trailing " +N more" (worst-case 16).
        remaining = len(items) - (i + 1)
        tail = f", +{remaining} more" if remaining else ""
        needed = (sep if out else "") + it
        if used + len(needed) + len(tail) > space:
            # can we at least fit the tail alone?
            if out and used + len(tail) <= space:
                remaining_all = len(items) - len(out)
                if remaining_all > 0:
                    return ", ".join(out) + f" +{remaining_all} more"
                return ", ".join(out)
            # no room even for tail — truncate this item with ellipsis.
            if not out:
                return _truncate(it, space)
            remaining_all = len(items) - len(out)
            return ", ".join(out) + (f" +{remaining_all} more"
                                     if remaining_all else "")
        out.append(it)
        used += len(needed)
    return ", ".join(out)


def summarize_contribution_compact(
    contrib: "TraceContribution", *,
    summary_width: int = 60,
    max_items: int = 4,
) -> tuple[str, str]:
    """Return ``(state_col, summary_col)`` for the graph renderer.

    ``state_col`` is the short counts/state cell (right-aligned by caller).
    ``summary_col`` is the comma-list of entity / file names, already
    width-fitted to ``summary_width``. Either may be empty.

    Branches:
      - overlap_case != 'has_entities': state is ``[N lines . scattered]``
        or ``[N lines . diffuse, t:XX dominates]``; summary is "".
      - Chunk-only (all entities are type 'chunk'): state carries
        ``+N -M ~K chunks``; summary is ``<file> lines A-B, C-D, +N more``.
      - Name-list mode (1-2 files AND total <= 5 entities):
        state is ``+N ~M``; summary is ``fooFn, BarClass, baz, +N more``.
      - File-aggregate (default for >=3 files or >5 entities):
        state is ``+N ~M [label]``; summary lists basenames with per-file
        counts like ``010-managed-agents (+17), 000-convention (+15)``.
    """
    from .entity_join import EntityChange  # noqa: F401 (type only at runtime)

    case = getattr(contrib, "overlap_case", "has_entities")
    if case == "scattered":
        n = contrib.line_count
        return (f"[{n} lines \u00b7 scattered]", "")
    if case == "diffuse":
        dom = getattr(contrib, "dominant_by", None)
        # Note: no ``t:`` prefix — this is an informational reference inside
        # the bracketed state note, not a clickable handle. Using the handle
        # form here made the bracket look like a second styled handle and
        # invited users to paste it into ``ot show`` even though the
        # bracket is just descriptive metadata. Plain short id reads cleanly
        # alongside the dim-grey bracket paint applied by _paint_state.
        from opentraces.core.trace_meta import short_trace_id as _short
        tag = _short(dom) if dom else "another trace"
        n = contrib.line_count
        return (f"[{n} lines \u00b7 diffuse, {tag} dominates]", "")
    if case == "phantom":
        # Should have been filtered upstream, but be defensive.
        return ("", "")

    ents = list(contrib.entities)
    chunks = list(contrib.chunks)
    if not ents and not chunks:
        # Safety: has_entities with nothing recorded — treat as scattered.
        n = contrib.line_count
        return (f"[{n} lines \u00b7 scattered]", "")

    # Counts across entities + chunks (chunks roll into their change_type).
    counts: Counter[str] = Counter()
    for e in ents:
        counts[e.change_type] += 1
    for _s, _e, ct in chunks:
        counts[ct] += 1

    def _counts_str(label: str | None = None) -> str:
        tokens: list[str] = []
        if counts.get("added"):
            tokens.append(f"+{counts['added']}")
        if counts.get("modified"):
            tokens.append(f"~{counts['modified']}")
        if counts.get("deleted"):
            tokens.append(f"-{counts['deleted']}")
        if counts.get("renamed"):
            tokens.append(f"\u21B7{counts['renamed']}")
        base = " ".join(tokens) if tokens else ""
        if label:
            return f"{base} {label}".strip()
        return base

    # Chunk-only branch: every entity is a chunk OR we only have bare chunks.
    ent_all_chunks = bool(ents) and all(
        (e.entity_type or "").lower() == "chunk" for e in ents
    )
    chunk_only = (ent_all_chunks and not any(
        (e.entity_type or "").lower() != "chunk" for e in ents
    )) or (not ents and bool(chunks))
    if chunk_only:
        state = _counts_str("chunks")
        # Summary: filename lines A-B, C-D, +N more
        by_file: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for e in ents:
            if (e.entity_type or "").lower() == "chunk":
                import re as _re
                m = _re.match(r"^lines\s+(\d+)\s*-\s*(\d+)", e.entity_name or "")
                if m:
                    by_file[e.file_path].append(
                        (int(m.group(1)), int(m.group(2))),
                    )
        # Also fold in bare chunks from contrib.chunks (no file path).
        # They become "lines A-B" with no basename prefix.
        bare = [(s, end) for s, end, _ct in chunks]
        first_file = next(iter(by_file), None)
        if first_file:
            ranges = by_file[first_file]
            labels = [f"{a}-{b}" for a, b in ranges[:max_items]]
            head = f"{_basename_no_ext(first_file)} lines {', '.join(labels)}"
            extra = len(ranges) - len(labels)
            summary = head + (f", +{extra} more" if extra > 0 else "")
        elif bare:
            labels = [f"{a}-{b}" for a, b in bare[:max_items]]
            head = f"lines {', '.join(labels)}"
            extra = len(bare) - len(labels)
            summary = head + (f", +{extra} more" if extra > 0 else "")
        else:
            summary = ""
        return (state, _truncate(summary, summary_width))

    # File count for mode selection.
    files_of: dict[str, list["EntityChange"]] = defaultdict(list)
    for e in ents:
        files_of[e.file_path].append(e)
    n_files = len(files_of)
    n_ents = len(ents)

    # Name-list mode: 1-2 files AND <= 5 entities.
    if n_files <= 2 and n_ents <= 5:
        state = _counts_str()
        names = [_truncate(e.entity_name or "?", 60) for e in ents]
        names = _dedupe_adjacent(names)
        summary = _fit_items(names[:max_items] + (
            [f"+{len(names) - max_items} more"]
            if len(names) > max_items else []
        ), summary_width)
        # cleaner: reuse _fit_items fully on the list
        summary = _fit_items(names, summary_width)
        return (state, summary)

    # File-aggregate mode.
    label = _type_label(ents)
    state = _counts_str(label)
    # Rank files by descending entity count (stable by name tiebreak).
    ranked = sorted(files_of.items(),
                    key=lambda kv: (-len(kv[1]), kv[0]))
    items: list[str] = []
    for fpath, fents in ranked:
        base = _truncate(_basename_no_ext(fpath), 28)
        items.append(f"{base} (+{len(fents)})")
    summary = _fit_items(items, summary_width)
    return (state, summary)
