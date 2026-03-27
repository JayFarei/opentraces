"""Dependency extraction from manifest files and tool call arguments."""

from __future__ import annotations

import json
import re
from pathlib import Path

from opentraces_schema.models import Step


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
