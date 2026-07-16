"""Canonical, importable identity for stored bench verifiers."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _canonical_module_from_source(source: Path) -> str | None:
    candidates: set[str] = set()
    for raw_root in sys.path:
        root = Path(raw_root or Path.cwd()).resolve()
        try:
            relative = source.relative_to(root)
        except ValueError:
            continue
        if relative.suffix != ".py":
            continue
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if not parts or any(not part.isidentifier() for part in parts):
            continue
        if not (root / parts[0] / "__init__.py").is_file():
            continue
        candidate = ".".join(parts)
        try:
            spec = importlib.util.find_spec(candidate)
        except (ImportError, ModuleNotFoundError, ValueError):
            continue
        if spec is None or spec.origin is None:
            continue
        try:
            origin = Path(spec.origin).resolve()
        except OSError:
            continue
        if origin == source:
            candidates.add(candidate)
    if len(candidates) > 1:
        raise ValueError("verifier source has multiple canonical import paths")
    return next(iter(candidates), None)


def callable_identity(verifier: Callable[..., Any]) -> tuple[str, str]:
    """Return the stable import coordinate and exact source digest for a callable."""

    source_value = inspect.getsourcefile(verifier)
    if source_value is None:
        raise ValueError("cannot locate verifier source")
    source = Path(source_value).resolve()
    qualname = verifier.__qualname__
    if not isinstance(qualname, str) or not qualname or "<locals>" in qualname:
        module_name = verifier.__module__
    else:
        module_name = _canonical_module_from_source(source) or verifier.__module__
    if not isinstance(module_name, str) or not module_name:
        raise ValueError("verifier has no module identity")
    try:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError("cannot read verifier source") from exc
    return f"{module_name}.{qualname}", f"sha256:{digest}"
