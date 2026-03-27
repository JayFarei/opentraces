"""Snippet extraction utilities: language detection, line range estimation."""

from __future__ import annotations

# Extension to language name mapping
_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".rb": "ruby",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".cs": "csharp",
    ".php": "php",
    ".sh": "shell",
    ".zsh": "shell",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".dockerfile": "dockerfile",
    ".ex": "elixir",
    ".exs": "elixir",
    ".clj": "clojure",
    ".scala": "scala",
    ".r": "r",
    ".lua": "lua",
    ".zig": "zig",
    ".nim": "nim",
    ".dart": "dart",
    ".vue": "vue",
    ".svelte": "svelte",
}


def detect_language(file_path: str) -> str | None:
    """Map a file path's extension to a language name.

    Returns None if the extension is not recognized.
    """
    # Handle special filenames
    lower = file_path.lower()
    if lower.endswith("dockerfile") and "." not in lower.rsplit("/", 1)[-1]:
        return "dockerfile"

    # Extract extension
    dot_idx = file_path.rfind(".")
    if dot_idx == -1:
        return None

    ext = file_path[dot_idx:].lower()
    return _EXTENSION_MAP.get(ext)


def estimate_line_range(content: str, offset: int = 1) -> tuple[int, int]:
    """Given content and a starting line offset, return (start_line, end_line).

    Counts newlines in content to determine the range.
    """
    if not content:
        return (offset, offset)

    line_count = content.count("\n")
    # If content doesn't end with newline, there's still one more line
    if content and not content.endswith("\n"):
        line_count += 1

    # At minimum one line
    line_count = max(line_count, 1)

    return (offset, offset + line_count - 1)


def extract_edited_lines(
    old_string: str,
    new_string: str,
    file_content: str | None = None,
) -> tuple[int | None, int | None]:
    """Determine the line numbers where an edit was applied.

    If file_content is provided, searches for old_string to find the position.
    Returns (start_line, end_line) of the new content, or (None, None) if
    the position cannot be determined.
    """
    if file_content is None:
        return (None, None)

    # Find old_string in file_content
    idx = file_content.find(old_string)
    if idx == -1:
        return (None, None)

    # Count lines before the match to get start_line (1-indexed)
    start_line = file_content[:idx].count("\n") + 1

    # Count lines in the new string
    new_line_count = new_string.count("\n")
    if new_string and not new_string.endswith("\n"):
        new_line_count += 1
    new_line_count = max(new_line_count, 1)

    end_line = start_line + new_line_count - 1

    return (start_line, end_line)
