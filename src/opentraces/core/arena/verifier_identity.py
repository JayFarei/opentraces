"""Canonical, importable identity for stored bench verifiers."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VerifierIdentityError(ValueError):
    """A verifier cannot be named and imported for stored-only replay."""

    code = "verifier_identity_invalid"


@dataclass(frozen=True, slots=True)
class ResolvedVerifier:
    """Canonical imported callable and the identity bound to its source bytes."""

    target: Callable[..., Any]
    name: str
    digest: str


def _canonical_module_from_source(source: Path) -> str | None:
    candidates: dict[str, tuple[int, int]] = {}
    for raw_root in sys.path:
        root = Path(raw_root or Path.cwd()).resolve()
        try:
            relative = source.relative_to(root)
        except ValueError:
            continue
        if relative.suffix != ".py":
            continue
        parts = list(relative.with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
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
            package_depth = 0
            package_path = root
            for part in parts if is_package else parts[:-1]:
                package_path /= part
                if not (package_path / "__init__.py").is_file():
                    break
                package_depth += 1
            specificity = (package_depth, len(parts))
            candidates[candidate] = max(candidates.get(candidate, specificity), specificity)
    if not candidates:
        return None
    highest_specificity = max(candidates.values())
    preferred = sorted(
        candidate
        for candidate, specificity in candidates.items()
        if specificity == highest_specificity
    )
    if len(preferred) > 1:
        raise VerifierIdentityError("verifier source has multiple canonical import paths")
    return preferred[0]


def resolve_verifier(verifier: Callable[..., Any]) -> ResolvedVerifier:
    """Resolve untrusted callable metadata to the canonical imported callable."""

    source_value = inspect.getsourcefile(verifier)
    if source_value is None:
        raise VerifierIdentityError("cannot locate verifier source")
    source = Path(source_value).resolve()
    qualname = verifier.__qualname__
    if (
        not isinstance(qualname, str)
        or not qualname
        or "<locals>" in qualname
        or any(not part.isidentifier() for part in qualname.split("."))
    ):
        raise VerifierIdentityError("verifier must be an importable module-level callable")
    module_name = _canonical_module_from_source(source) or verifier.__module__
    if not isinstance(module_name, str) or not module_name:
        raise VerifierIdentityError("verifier has no module identity")
    try:
        target: object = importlib.import_module(module_name)
        for attribute in qualname.split("."):
            target = getattr(target, attribute)
        target_source_value = inspect.getsourcefile(target)
        target_source = (
            Path(target_source_value).resolve() if target_source_value is not None else None
        )
    except (AttributeError, ImportError, ModuleNotFoundError, OSError, TypeError) as exc:
        raise VerifierIdentityError("verifier must be an importable module-level callable") from exc
    if not callable(target) or target_source != source:
        raise VerifierIdentityError("verifier must be an importable module-level callable")
    try:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        raise VerifierIdentityError("cannot read verifier source") from exc
    return ResolvedVerifier(
        target=target,
        name=f"{module_name}.{qualname}",
        digest=f"sha256:{digest}",
    )


def callable_identity(verifier: Callable[..., Any]) -> tuple[str, str]:
    """Return the stable import coordinate and exact source digest for a callable."""

    resolved = resolve_verifier(verifier)
    return resolved.name, resolved.digest
