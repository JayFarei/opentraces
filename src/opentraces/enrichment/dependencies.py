"""Dependency extraction from manifest files and tool call arguments."""

from __future__ import annotations

import json
import re
from pathlib import Path

from opentraces_schema.models import Step

from .known_packages import (
    COMMON_INTERNAL_NAMES,
    NODE_BUILTINS,
    PYTHON_STDLIB,
    WELL_KNOWN_PACKAGES,
)


def _parse_package_json(path: Path) -> list[str]:
    """Extract dependency names from package.json."""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    names: list[str] = []
    for key in ("dependencies", "devDependencies"):
        deps = data.get(key, {})
        if isinstance(deps, dict):
            names.extend(deps.keys())
    return names


def _parse_requirements_txt(path: Path) -> list[str]:
    """Extract package names from requirements.txt, stripping version specifiers."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []

    names: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Strip version specifiers: ==, >=, <=, ~=, !=, <, >
        name = re.split(r"[><=!~;@\[]", line)[0].strip()
        if name:
            names.append(name)
    return names


def _parse_pyproject_toml(path: Path) -> list[str]:
    """Extract dependency names from pyproject.toml [project].dependencies."""
    try:
        content = path.read_text()
    except OSError:
        return []

    names: list[str] = []
    in_deps = False
    for line in content.splitlines():
        stripped = line.strip()

        if stripped == "dependencies = [":
            in_deps = True
            continue
        elif in_deps:
            if stripped == "]":
                break
            # Extract package name from PEP 508 string like "click>=8.0"
            dep = stripped.strip('",').strip()
            if dep:
                name = re.split(r"[><=!~;\[\s]", dep)[0].strip()
                if name:
                    names.append(name)
    return names


def _parse_gemfile(path: Path) -> list[str]:
    """Extract gem names from Gemfile."""
    try:
        content = path.read_text()
    except OSError:
        return []

    names: list[str] = []
    # Match: gem 'name' or gem "name"
    for match in re.finditer(r"""gem\s+['"]([^'"]+)['"]""", content):
        names.append(match.group(1))
    return names


def _parse_go_mod(path: Path) -> list[str]:
    """Extract module paths from go.mod require block."""
    try:
        content = path.read_text()
    except OSError:
        return []

    names: list[str] = []
    in_require = False
    for line in content.splitlines():
        stripped = line.strip()

        if stripped.startswith("require ("):
            in_require = True
            continue
        elif stripped == ")" and in_require:
            in_require = False
            continue
        elif in_require:
            # Lines like: github.com/foo/bar v1.2.3
            parts = stripped.split()
            if parts:
                names.append(parts[0])
        elif stripped.startswith("require "):
            # Single-line require
            parts = stripped.split()
            if len(parts) >= 2:
                names.append(parts[1])

    return names


def extract_dependencies(project_path: str | Path) -> list[str]:
    """Read manifest files and extract package names (not versions).

    Checks: package.json, requirements.txt, pyproject.toml, Gemfile, go.mod.
    Returns a deduplicated, sorted list of package names.
    """
    project_path = Path(project_path)
    all_deps: set[str] = set()

    manifest_parsers = {
        "package.json": _parse_package_json,
        "requirements.txt": _parse_requirements_txt,
        "pyproject.toml": _parse_pyproject_toml,
        "Gemfile": _parse_gemfile,
        "go.mod": _parse_go_mod,
    }

    for filename, parser in manifest_parsers.items():
        manifest = project_path / filename
        if manifest.exists():
            all_deps.update(parser(manifest))

    return sorted(all_deps)


def extract_dependencies_from_steps(steps: list[Step]) -> list[str]:
    """Extract dependency names from Bash tool calls that install packages.

    Looks for patterns like: npm install X, pip install X, gem install X,
    go get X, cargo add X.
    """
    install_patterns = [
        # npm/yarn/pnpm install
        re.compile(r"(?:npm|yarn|pnpm)\s+(?:install|add|i)\s+(.+)", re.IGNORECASE),
        # pip install
        re.compile(r"pip3?\s+install\s+(.+)", re.IGNORECASE),
        # gem install
        re.compile(r"gem\s+install\s+(.+)", re.IGNORECASE),
        # go get
        re.compile(r"go\s+get\s+(.+)", re.IGNORECASE),
        # cargo add
        re.compile(r"cargo\s+add\s+(.+)", re.IGNORECASE),
    ]

    deps: set[str] = set()

    for step in steps:
        for tc in step.tool_calls:
            if tc.tool_name.lower() != "bash":
                continue

            command = tc.input.get("command", "")
            if not command:
                continue

            for pattern in install_patterns:
                match = pattern.search(command)
                if match:
                    raw = match.group(1)
                    # Split on spaces and filter out flags (starting with -)
                    for token in raw.split():
                        token = token.strip()
                        if token and not token.startswith("-"):
                            # Strip version specifiers
                            name = re.split(r"[@>=<~!]", token)[0]
                            if name:
                                deps.add(name)

    return sorted(deps)


# ---------------------------------------------------------------------------
# Extension -> ecosystem mapping
# ---------------------------------------------------------------------------

_EXT_TO_ECOSYSTEM: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rb": "ruby",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".swift": "swift",
    ".kt": "kotlin",
    ".cs": "csharp",
}

_IGNORED_EXTENSIONS: set[str] = {
    ".json", ".yaml", ".yml", ".md", ".toml", ".css", ".html", ".svg",
    ".png", ".jpg", ".gif", ".ico", ".txt", ".gitignore", ".lock",
    ".cfg", ".ini", ".env", ".sh", ".bat",
}

# Bash command patterns -> ecosystem
_BASH_ECOSYSTEM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:python3?|pip3?|pytest|uv)\b"), "python"),
    (re.compile(r"\b(?:node|npm|pnpm|yarn|bun|npx)\b"), "javascript"),
    (re.compile(r"\bcargo\b"), "rust"),
    (re.compile(r"\bgo\s"), "go"),
    (re.compile(r"\b(?:bundle|gem)\b"), "ruby"),
]


def infer_language_ecosystem(steps: list[Step]) -> list[str]:
    """Infer programming language ecosystems from file extensions in tool calls.

    Scans file_path and path keys in tool call inputs, plus Bash command
    patterns. Returns a deduplicated, sorted list of ecosystem names.
    """
    ecosystems: set[str] = set()

    for step in steps:
        for tc in step.tool_calls:
            # Check file extensions from tool call inputs
            for key in ("file_path", "path"):
                file_path = tc.input.get(key, "")
                if not file_path:
                    continue
                ext = _get_extension(file_path)
                if ext and ext not in _IGNORED_EXTENSIONS and ext in _EXT_TO_ECOSYSTEM:
                    ecosystems.add(_EXT_TO_ECOSYSTEM[ext])

            # Check Bash commands for ecosystem signals
            if tc.tool_name.lower() == "bash":
                command = tc.input.get("command", "")
                if command:
                    for pattern, ecosystem in _BASH_ECOSYSTEM_PATTERNS:
                        if pattern.search(command):
                            ecosystems.add(ecosystem)

    return sorted(ecosystems)


def _get_extension(file_path: str) -> str:
    """Extract file extension including the dot, handling dotfiles."""
    # Use Path to get the suffix
    p = Path(file_path)
    suffix = p.suffix.lower()
    # Handle files like .gitignore (no stem before dot)
    if not suffix and p.name.startswith("."):
        return "." + p.name[1:].lower()
    return suffix


# ---------------------------------------------------------------------------
# Import-based dependency extraction
# ---------------------------------------------------------------------------

# Python: from X import ... / import X
_PY_FROM_IMPORT = re.compile(r"^\s*from\s+(\S+)\s+import\b")
_PY_IMPORT = re.compile(r"^\s*import\s+(\S+)")

# JS/TS: import ... from 'X' / import ... from "X" (handles import type { ... } from 'X')
_JS_IMPORT_FROM = re.compile(r"""^\s*import\s+.*?\s+from\s+['"]([^'"]+)['"]""")

# JS/TS: require('X') / require("X")
_JS_REQUIRE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")

# Ruby: require 'X' / require "X"
_RUBY_REQUIRE = re.compile(r"""^\s*require\s+['"]([^'"]+)['"]""")

# Go: import "X"
_GO_IMPORT = re.compile(r"""^\s*import\s+["']([^"']+)["']""")


def extract_dependencies_from_imports(
    steps: list[Step],
    project_name: str | None = None,
) -> list[str]:
    """Extract library names from import statements in observation content.

    Scans observation.content for Python, JS/TS, Ruby, and Go import
    patterns. Applies three-stage filtering to remove stdlib, internal
    packages, and false positives. Merges with install-command extraction.

    Args:
        steps: List of trace steps containing observations.
        project_name: Project directory name for internal package filtering.

    Returns:
        Deduplicated, sorted list of third-party dependency names.
    """
    raw_names: set[str] = set()

    # Scan source code content from two locations:
    # 1. Observation content (tool execution output that may contain source)
    # 2. Write/Edit tool call inputs (source code being written to files)
    _write_tool_names = {"write", "edit"}

    for step in steps:
        for obs in step.observations:
            if not obs.content:
                continue
            for line in obs.content.splitlines():
                # Strip line numbers: "  42\u2192code" -> "code"
                line = re.sub(r"^\s*\d+\u2192", "", line)
                _extract_from_line(line, raw_names)

        for tc in step.tool_calls:
            if tc.tool_name.lower() not in _write_tool_names:
                continue
            content = tc.input.get("content", "")
            if not content or not isinstance(content, str):
                continue
            for line in content.splitlines():
                _extract_from_line(line, raw_names)

    # Three-stage filtering
    filtered: set[str] = set()
    for name in raw_names:
        if _is_valid_dependency(name, project_name):
            filtered.add(name)

    # Merge with install-command extraction
    install_deps = extract_dependencies_from_steps(steps)
    filtered.update(install_deps)

    return sorted(filtered)


def _extract_from_line(line: str, names: set[str]) -> None:
    """Extract package names from a single source line."""
    # Python: from X import ...
    m = _PY_FROM_IMPORT.match(line)
    if m:
        pkg = m.group(1).split(".")[0]
        if pkg:
            names.add(pkg)
        return

    # Go: import "X" -- must check before Python import to avoid false match
    m = _GO_IMPORT.match(line)
    if m:
        names.add(m.group(1))
        return

    # JS/TS: import ... from 'X' -- check before bare Python import
    m = _JS_IMPORT_FROM.match(line)
    if m:
        pkg = _normalize_js_package(m.group(1))
        if pkg:
            names.add(pkg)
        return

    # Python: import X (but not JS import)
    m = _PY_IMPORT.match(line)
    if m:
        raw = m.group(1)
        pkg = raw.split(".")[0].split(",")[0]
        if pkg and not pkg.startswith("{") and not pkg.startswith("(") and not pkg.startswith("'") and not pkg.startswith('"'):
            names.add(pkg)
            return

    # JS/TS: require('X')
    m = _JS_REQUIRE.search(line)
    if m:
        pkg = _normalize_js_package(m.group(1))
        if pkg:
            names.add(pkg)
        return

    # Ruby: require 'X'
    m = _RUBY_REQUIRE.match(line)
    if m:
        pkg = m.group(1).split("/")[0]
        if pkg:
            names.add(pkg)
        return


def _normalize_js_package(raw: str) -> str | None:
    """Normalize a JS/TS package name, handling scoped packages."""
    if not raw:
        return None
    # Relative imports
    if raw.startswith(".") or raw.startswith("@/"):
        return None
    # Scoped packages: @scope/name
    if raw.startswith("@"):
        parts = raw.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return None
    # Regular package: take first path segment
    return raw.split("/")[0]


def _is_valid_dependency(name: str, project_name: str | None) -> bool:
    """Apply three-stage filtering to determine if a name is a valid dependency."""
    if not name:
        return False

    # Well-known packages always pass
    if name in WELL_KNOWN_PACKAGES:
        return True

    # Stage 1: Stdlib filter
    if name in PYTHON_STDLIB:
        return False
    if name in NODE_BUILTINS:
        return False

    # Stage 2: Internal package filter
    # Skip if matches project name
    if project_name and name == project_name:
        return False

    # Skip common internal names
    if name in COMMON_INTERNAL_NAMES:
        return False

    # Skip CamelCase names (likely React component imports)
    if name and name[0].isupper():
        return False

    # Skip relative or path alias imports
    if name.startswith(".") or name.startswith("@/"):
        return False

    # Skip single-character names
    if len(name) <= 1:
        return False

    return True
