"""Clean-room diff renderer for attribution overlay.

Uses only stdlib difflib + Rich Text for rendering.
Does NOT depend on any third-party diff widget.
"""

from __future__ import annotations

import difflib

from rich.text import Text
from textual.widgets import Static

# Palette (Rich hex colors, not CSS tokens)
_GREEN = "#22C55E"
_RED = "#EF4444"
_CYAN = "#22D3EE"
_DIM = "#666666"
_BOLD_GREEN = f"bold {_GREEN}"
_BOLD_RED = f"bold {_RED}"


def _char_level_markup(
    old_line: str,
    new_line: str,
) -> tuple[Text, Text]:
    """Return a (deletion, addition) pair with character-level bold highlights.

    Uses ``difflib.SequenceMatcher`` on the two strings to find matching
    blocks, then marks *non-matching* spans as bold.
    """
    sm = difflib.SequenceMatcher(None, old_line, new_line)

    del_text = Text("- ")
    del_text.stylize(_RED)
    add_text = Text("+ ")
    add_text.stylize(_GREEN)

    # Walk through opcodes to build styled spans
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            del_text.append(old_line[i1:i2], style=_RED)
            add_text.append(new_line[j1:j2], style=_GREEN)
        elif tag == "replace":
            del_text.append(old_line[i1:i2], style=_BOLD_RED)
            add_text.append(new_line[j1:j2], style=_BOLD_GREEN)
        elif tag == "delete":
            del_text.append(old_line[i1:i2], style=_BOLD_RED)
        elif tag == "insert":
            add_text.append(new_line[j1:j2], style=_BOLD_GREEN)

    return del_text, add_text


def render_diff(
    before: str,
    after: str,
    filename: str = "",
    context_lines: int = 3,
) -> Text:
    """Produce a Rich ``Text`` object showing a unified diff.

    Parameters
    ----------
    before:
        Original text.
    after:
        Modified text.
    filename:
        Optional filename shown in the ``---``/``+++`` header.
    context_lines:
        Number of unchanged context lines around each hunk (default 3).
    """
    old_lines = before.splitlines(keepends=True)
    new_lines = after.splitlines(keepends=True)

    from_name = f"a/{filename}" if filename else "a"
    to_name = f"b/{filename}" if filename else "b"

    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=from_name,
            tofile=to_name,
            n=context_lines,
        )
    )

    if not diff_lines:
        result = Text("(no changes)", style=_DIM)
        return result

    result = Text()

    # We accumulate contiguous replace blocks so we can apply char-level
    # highlighting when the deletion count equals the addition count.
    pending_del: list[str] = []
    pending_add: list[str] = []

    def _flush_pending() -> None:
        """Flush accumulated del/add lines, applying char-level diffs when possible."""
        nonlocal pending_del, pending_add

        if pending_del and pending_add and len(pending_del) == len(pending_add):
            # Equal-length replace block: character-level highlighting
            for old_l, new_l in zip(pending_del, pending_add):
                del_rich, add_rich = _char_level_markup(old_l, new_l)
                result.append_text(del_rich)
                result.append("\n")
                result.append_text(add_rich)
                result.append("\n")
        else:
            for line in pending_del:
                result.append(f"- {line}\n", style=_RED)
            for line in pending_add:
                result.append(f"+ {line}\n", style=_GREEN)

        pending_del = []
        pending_add = []

    for raw in diff_lines:
        line = raw.rstrip("\n\r")

        # File header lines
        if line.startswith("--- "):
            _flush_pending()
            result.append(f"{line}\n", style=f"bold {_DIM}")
            continue
        if line.startswith("+++ "):
            _flush_pending()
            result.append(f"{line}\n", style=f"bold {_DIM}")
            continue

        # Hunk header
        if line.startswith("@@"):
            _flush_pending()
            result.append(f"{line}\n", style=_CYAN)
            continue

        # Deletion
        if line.startswith("-"):
            # If we already have pending adds, flush them first (pure insert
            # block followed by a new delete block).
            if pending_add:
                _flush_pending()
            pending_del.append(line[1:])
            continue

        # Addition (part of a replace block if dels are pending, or pure insert)
        if line.startswith("+"):
            pending_add.append(line[1:])
            continue

        # Context line (or anything else)
        _flush_pending()
        result.append(f"  {line}\n", style=_DIM)

    _flush_pending()
    return result


def _escape_markup(text: str) -> str:
    """Escape Rich markup characters in text."""
    return text.replace("[", "\\[").replace("]", "\\]")


def render_diff_markup(
    before: str,
    after: str,
    filename: str = "",
    context_lines: int = 3,
    max_lines: int = 60,
) -> list[str]:
    """Return diff as a list of Rich markup strings.

    Suitable for embedding in Static widgets that use ``markup=True``.
    Character-level highlighting uses bold within colored spans.
    """
    old_lines = before.splitlines(keepends=True)
    new_lines = after.splitlines(keepends=True)

    from_name = f"a/{filename}" if filename else "a"
    to_name = f"b/{filename}" if filename else "b"

    diff_iter = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=from_name,
        tofile=to_name,
        n=context_lines,
    )

    output: list[str] = []
    for raw in diff_iter:
        if len(output) >= max_lines:
            output.append(f"[{_DIM}]... (diff truncated)[/{_DIM}]")
            break
        line = raw.rstrip("\n\r")
        safe = _escape_markup(line)

        if line.startswith("--- ") or line.startswith("+++ "):
            output.append(f"[bold {_DIM}]{safe}[/bold {_DIM}]")
        elif line.startswith("@@"):
            output.append(f"[{_CYAN}]{safe}[/{_CYAN}]")
        elif line.startswith("-"):
            output.append(f"[{_RED}]{safe}[/{_RED}]")
        elif line.startswith("+"):
            output.append(f"[{_GREEN}]{safe}[/{_GREEN}]")
        else:
            output.append(f"[{_DIM}] {_escape_markup(line.lstrip(' '))}[/{_DIM}]")

    if not output:
        output.append(f"[{_DIM}](no changes)[/{_DIM}]")

    return output


class DiffView(Static):
    """Clean-room diff renderer for attribution overlay.

    Shows unified diff with:
    - Green (+) lines for additions
    - Red (-) lines for deletions
    - Gray context lines
    - Character-level highlighting within changed lines
    """

    def __init__(
        self,
        before: str,
        after: str,
        filename: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__("", markup=False, **kwargs)
        self._before = before
        self._after = after
        self._filename = filename

    def on_mount(self) -> None:
        rich_text = render_diff(self._before, self._after, self._filename)
        self.update(rich_text)
