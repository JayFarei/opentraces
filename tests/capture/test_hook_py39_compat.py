from __future__ import annotations

import ast
from pathlib import Path

from opentraces.capture.claude_code.hooks import HOOK_SCRIPTS_DIR


def _hook_sources() -> list[Path]:
    claude_hooks_dir = HOOK_SCRIPTS_DIR
    codex_hooks_dir = claude_hooks_dir.parents[1] / "codex_cli" / "hooks"
    return sorted([*claude_hooks_dir.glob("*.py"), *codex_hooks_dir.glob("*.py")])


def _has_future_annotations(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            return any(alias.name == "annotations" for alias in node.names)
    return False


def test_every_hook_script_has_future_annotations_import() -> None:
    missing: list[str] = []
    for source in _hook_sources():
        tree = ast.parse(source.read_text(), filename=str(source))
        if not _has_future_annotations(tree):
            missing.append(str(source))

    assert missing == []


def test_hook_sources_compile() -> None:
    for source in _hook_sources():
        compile(source.read_text(), str(source), "exec")
